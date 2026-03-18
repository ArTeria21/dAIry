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
    """Base error for journal sync failures."""


class GitRepoDirtyError(GitSyncError):
    """Raised when repo has local changes that would make sync unsafe."""


class GitPermissionError(GitSyncError):
    """Raised when the current process cannot write into the repo metadata."""


class GitConflictError(GitSyncError):
    """Raised when remote changes cannot be rebased cleanly."""


class GitPushError(GitSyncError):
    """Raised when a local commit could not be pushed after retries."""


class GitService:
    """Git workflow for sync-before-write and commit/rebase/push-after-write."""

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

    def _refresh_index(self, repo: Repo) -> None:
        repo.git.update_index("--refresh")

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
        paths = {item.a_path for item in repo.index.diff(None)}
        paths.update(repo.untracked_files)
        return sorted(path for path in paths if path)

    def _ensure_clean_worktree(self, repo: Repo) -> None:
        self._refresh_index(repo)
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

    def sync_from_remote(self, *, allow_dirty: bool = False) -> bool:
        """Fetch remote changes and rebase local commits on top of them."""
        if not self.enabled:
            return True
        try:
            repo = self._ensure_repo()
            self._require_remote(repo)
            self._assert_repo_writable(repo)
            if not allow_dirty:
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
        except Exception as exc:  # pragma: no cover - defensive
            raise GitSyncError("Unexpected error during git sync from remote") from exc

    def prepare_for_write(self) -> None:
        """Ensure repo is clean and up to date before modifying journal files."""
        self.sync_from_remote()

    def _stage_paths(
        self, repo: Repo, file_paths: Path | Sequence[Path]
    ) -> tuple[list[Path], list[str]]:
        paths = [file_paths] if isinstance(file_paths, Path) else list(file_paths)
        if not paths:
            return [], []

        rel_paths: list[str] = []
        for fp in paths:
            rel_paths.append(str(fp.resolve().relative_to(repo.working_tree_dir)))
        return paths, rel_paths

    def commit_and_push(self, file_paths: Path | Sequence[Path]) -> GitSyncResult:
        """Stage changes, commit if needed, and push with one sync/retry on rejection."""
        if not self.enabled:
            return GitSyncResult(pushed=True)

        try:
            repo = self._ensure_repo()
            self._require_remote(repo)
            self._assert_repo_writable(repo)
            paths, rel_paths = self._stage_paths(repo, file_paths)
        except (NoSuchPathError, InvalidGitRepositoryError, ValueError) as exc:
            raise GitSyncError("Cannot resolve journal file(s) inside repo") from exc

        if not rel_paths:
            return GitSyncResult(pushed=True)

        try:
            repo.index.add(rel_paths)
            has_staged_changes = repo.is_dirty(
                index=True, working_tree=False, untracked_files=False
            )
            if not has_staged_changes:
                return GitSyncResult(pushed=True)

            timestamp = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S %Z")
            repo.index.commit(f"Journal entry: {timestamp}")
            tracking_ref = self._tracking_ref_name(repo)

            try:
                repo.git.push()
                return GitSyncResult(pushed=True)
            except GitCommandError as exc:
                logger.warning(
                    "Initial git push failed, retrying after sync (%s)",
                    _format_git_error(exc),
                    extra={"files": [str(p) for p in paths]},
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
        except Exception as exc:  # pragma: no cover - defensive
            raise GitSyncError("Unexpected error during git commit/push") from exc
