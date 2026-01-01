import logging
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


class GitService:
    """Thin wrapper around GitPython for pull/commit/push workflow."""

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

    def pull_changes(self) -> bool:
        """Fetch and merge latest changes from the default remote."""
        if not self.enabled:
            return True
        try:
            repo = self._ensure_repo()
            if not repo.remotes:
                logger.error("Git pull skipped: no remotes configured")
                return False
            repo.remote().pull()
            return True
        except (NoSuchPathError, InvalidGitRepositoryError):
            logger.exception("Journal directory is not a git repository")
        except GitCommandError as exc:
            logger.exception("Git pull failed (%s)", _format_git_error(exc))
        except Exception:  # pragma: no cover - defensive
            logger.exception("Unexpected error during git pull")
        return False

    def commit_and_push(self, file_path: Path) -> bool:
        """Stage the given file, create a commit if needed, and push."""
        if not self.enabled:
            return True
        try:
            repo = self._ensure_repo()
            rel_path = file_path.resolve().relative_to(repo.working_tree_dir)
        except (NoSuchPathError, InvalidGitRepositoryError, ValueError):
            logger.exception(
                "Cannot resolve journal file inside repo",
                extra={"file": str(file_path)},
            )
            return False

        try:
            repo.index.add([str(rel_path)])
            has_staged_changes = repo.is_dirty(
                index=True, working_tree=False, untracked_files=False
            )
            if not has_staged_changes and not repo.untracked_files:
                return True

            timestamp = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S %Z")
            repo.index.commit(f"Journal entry: {timestamp}")
            if not repo.remotes:
                logger.error("Git push skipped: no remotes configured")
                return False
            repo.remote().push()
            return True
        except GitCommandError as exc:
            logger.exception(
                "Git commit/push failed (%s)", _format_git_error(exc), extra={"file": str(file_path)}
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Unexpected error during git commit/push",
                extra={"file": str(file_path)},
            )
        return False
