import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from dairy_bot.config import DEFAULT_TZ

logger = logging.getLogger(__name__)


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
            repo.remote().pull()
            return True
        except (NoSuchPathError, InvalidGitRepositoryError):
            logger.exception("Journal directory is not a git repository")
        except GitCommandError:
            logger.exception("Git pull failed")
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
            repo.remote().push()
            return True
        except GitCommandError:
            logger.exception("Git commit/push failed", extra={"file": str(file_path)})
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Unexpected error during git commit/push",
                extra={"file": str(file_path)},
            )
        return False
