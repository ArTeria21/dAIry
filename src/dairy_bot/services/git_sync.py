import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from dairy_bot.config import DEFAULT_TZ

logger = logging.getLogger(__name__)


def _format_git_error(error: GitCommandError) -> str:
    cmd = error.command
    if isinstance(cmd, (list, tuple)):
        cmd_text = " ".join(str(part) for part in cmd if part is not None)
    else:
        cmd_text = str(cmd) if cmd else ""
    stderr = (error.stderr or "").strip()
    stdout = (error.stdout or "").strip()
    parts: list[str] = []
    if cmd_text:
        parts.append(f"cmd={cmd_text}")
    if error.status is not None:
        parts.append(f"status={error.status}")
    if stderr:
        parts.append(f"stderr={stderr}")
    if stdout:
        parts.append(f"stdout={stdout}")
    return "; ".join(parts) if parts else "no details"


@dataclass(slots=True)
class GitSyncResult:
    pushed: bool


class GitSyncError(RuntimeError):
    """Base journal synchronization error."""


class GitRepoDirtyError(GitSyncError):
    """Repository has local changes that make synchronization unsafe."""


class GitPermissionError(GitSyncError):
    """The process cannot write repository Git metadata."""


class GitConflictError(GitSyncError):
    """Remote changes could not be applied cleanly through rebase."""


class GitPushError(GitSyncError):
    """The local commit could not be pushed after a retry."""


class GitService:
    """Git workflow: sync before write, then commit/rebase/push."""

    def __init__(
        self, journal_dir: Path, enabled: bool = True, timezone: ZoneInfo | None = None
    ) -> None:
        self.journal_dir = Path(journal_dir)
        self.enabled = enabled
        self.timezone = timezone or DEFAULT_TZ
        self._repo: Repo | None = None

    def _ensure_repo(self) -> Repo:
        if self._repo is None:
            self._repo = Repo(self.journal_dir)
        return self._repo

    def _tracking_ref_name(self, repo: Repo) -> str:
        try:
            tracking_branch = repo.active_branch.tracking_branch()
        except TypeError as exc:  # detached HEAD
            raise GitSyncError("Repository is in detached HEAD state") from exc
        if tracking_branch is None:
            raise GitSyncError("Current branch has no upstream tracking branch")
        return str(tracking_branch)

    def _require_remote(self, repo: Repo) -> None:
        if not repo.remotes:
            raise GitSyncError("No git remotes configured for journal repository")

    def _assert_repo_writable(self, repo: Repo) -> None:
        git_dir = Path(repo.git_dir)
        objects_dir = git_dir / "objects"
        checks = (git_dir, objects_dir)
        for path in checks:
            if not path.exists():
                raise GitPermissionError(f"Git metadata path is missing: {path}")
            if not os.access(path, os.W_OK | os.X_OK):
                raise GitPermissionError(f"Git metadata path is not writable: {path}")

    def _dirty_paths(self, repo: Repo) -> list[str]:
        status_output = repo.git.status("--porcelain")
        paths: list[str] = []
        for line in status_output.splitlines():
            if not line:
                continue
            entry = line[3:] if len(line) > 3 else line
            if " -> " in entry:
                _, entry = entry.split(" -> ", maxsplit=1)
            entry = entry.strip()
            if entry:
                paths.append(entry)
        return sorted(dict.fromkeys(paths))

    def _stage_all_changes(self, repo: Repo) -> None:
        repo.git.add(A=True)

    def _has_staged_changes(self, repo: Repo) -> bool:
        return bool(repo.git.diff("--cached", "--name-only").strip())

    def _commit_staged_changes(
        self,
        repo: Repo,
        message_prefix: str,
        *,
        commit_message: str | None = None,
    ) -> bool:
        if not self._has_staged_changes(repo):
            return False
        message = commit_message
        if message is None:
            timestamp = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S %Z")
            message = f"{message_prefix}: {timestamp}"
        repo.index.commit(message)
        return True

    def _ensure_clean_worktree(self, repo: Repo) -> None:
        dirty_paths = self._dirty_paths(repo)
        if dirty_paths:
            preview = ", ".join(dirty_paths[:10])
            suffix = "" if len(dirty_paths) <= 10 else f" (+{len(dirty_paths) - 10} more)"
            raise GitRepoDirtyError(
                f"Journal repo has uncommitted changes: {preview}{suffix}"
            )

    def _ahead_behind(self, repo: Repo, tracking_ref: str) -> tuple[int, int]:
        output = repo.git.rev_list("--left-right", "--count", f"HEAD...{tracking_ref}")
        ahead_text, behind_text = output.strip().split()
        return int(ahead_text), int(behind_text)

    def _abort_rebase(self, repo: Repo) -> None:
        git_dir = Path(repo.git_dir)
        if not ((git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()):
            return
        try:
            repo.git.rebase("--abort")
        except GitCommandError:
            logger.warning("Failed to abort git rebase cleanly", exc_info=True)

    def sync_from_remote(self, *, allow_dirty: bool = False, autocommit_dirty: bool = False) -> bool:
        """Fetch remote changes and replay local commits on top of them."""
        if not self.enabled:
            return True
        try:
            repo = self._ensure_repo()
            self._require_remote(repo)
            self._assert_repo_writable(repo)
            if autocommit_dirty:
                dirty_paths = self._dirty_paths(repo)
                if dirty_paths:
                    logger.info(
                        "Auto-committing %d existing repo change(s) before sync: %s",
                        len(dirty_paths),
                        ", ".join(dirty_paths[:10]),
                    )
                    self._stage_all_changes(repo)
                    self._commit_staged_changes(repo, "Journal repo snapshot")
            elif not allow_dirty:
                self._ensure_clean_worktree(repo)

            tracking_ref = self._tracking_ref_name(repo)
            repo.git.fetch("--prune", "--tags")
            _, behind = self._ahead_behind(repo, tracking_ref)
            if behind == 0:
                return True

            try:
                repo.git.rebase(tracking_ref)
            except GitCommandError as exc:
                self._abort_rebase(repo)
                raise GitConflictError(
                    f"Git rebase failed while syncing from remote ({_format_git_error(exc)})"
                ) from exc
            return True
        except GitSyncError:
            raise
        except (NoSuchPathError, InvalidGitRepositoryError) as exc:
            raise GitSyncError("Journal directory is not a git repository") from exc
        except GitCommandError as exc:
            raise GitSyncError(
                f"Git sync from remote failed ({_format_git_error(exc)})"
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise GitSyncError("Unexpected error during git sync from remote") from exc

    def prepare_for_write(self) -> None:
        """Save existing changes and synchronize before writing."""
        self.sync_from_remote(autocommit_dirty=True)

    def commit_and_push(
        self,
        file_paths: Path | Sequence[Path] | None = None,
        *,
        commit_message: str | None = None,
    ) -> GitSyncResult:
        """Add changes to the index, create a commit, and push it to remote."""
        if not self.enabled:
            return GitSyncResult(pushed=True)

        try:
            repo = self._ensure_repo()
            self._require_remote(repo)
            self._assert_repo_writable(repo)
        except (NoSuchPathError, InvalidGitRepositoryError) as exc:
            raise GitSyncError("Cannot access journal repository") from exc

        try:
            self._stage_all_changes(repo)
            if not self._commit_staged_changes(
                repo,
                "Journal entry",
                commit_message=commit_message,
            ):
                return GitSyncResult(pushed=True)

            tracking_ref = self._tracking_ref_name(repo)

            try:
                repo.git.push()
                return GitSyncResult(pushed=True)
            except GitCommandError as exc:
                logger.warning(
                    "Initial git push failed, retrying after sync (%s)",
                    _format_git_error(exc),
                )

            try:
                repo.git.fetch("--prune", "--tags")
                _, behind = self._ahead_behind(repo, tracking_ref)
                if behind > 0:
                    repo.git.rebase(tracking_ref)
                repo.git.push()
                return GitSyncResult(pushed=True)
            except GitCommandError as exc:
                self._abort_rebase(repo)
                raise GitPushError(
                    f"Git push failed after retry ({_format_git_error(exc)})"
                ) from exc
        except GitSyncError:
            raise
        except GitCommandError as exc:
            raise GitSyncError(
                f"Git commit/push failed ({_format_git_error(exc)})"
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise GitSyncError("Unexpected error during git commit/push") from exc
