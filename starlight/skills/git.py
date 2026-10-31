"""Version the skills folder with git, in a history kept outside your project repo
(``data/skills.git``)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

FIELD_SEP = "\x1f"  # what %x1f writes in git log --format


class GitError(Exception):
    pass


class SkillHistory:
    def __init__(self, work_tree: Path, git_dir: Path) -> None:
        self.work_tree = work_tree
        self.git_dir = git_dir
        self.available = shutil.which("git") is not None

    def _git(self, *args: str, check: bool = True) -> str:
        if not self.available:
            raise GitError("git is not installed; skill history is off")
        result = subprocess.run(
            ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.work_tree}", *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if check and result.returncode != 0:
            raise GitError(result.stderr.strip() or result.stdout.strip())
        return result.stdout

    def init(self) -> None:
        if not self.available or (self.git_dir / "HEAD").exists():
            return
        self.git_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "--quiet", "--bare", str(self.git_dir)],
            capture_output=True, check=True, timeout=30,
        )  # fmt: skip
        # Only the history lives here; the files are skills/ itself (--work-tree in _git), so
        # it is set up bare (no checkout of its own) and then told it is not, which lets
        # add, commit and checkout work on that outside tree.
        self._git("config", "core.bare", "false")
        self._git("config", "user.name", "Starlight")
        self._git("config", "user.email", "starlight@localhost")
        self._git("config", "commit.gpgsign", "false")

    def commit(self, message: str, path: str = ".") -> bool:
        """Commit changes under ``path``. Returns False when there was nothing to commit."""
        if not self.available:
            return False
        self.init()
        self._git("add", "--all", "--", path)
        if not self._git("status", "--porcelain", "--", path).strip():
            return False
        self._git("commit", "--quiet", "-m", message, "--", path)
        return True

    def log(self, path: str, limit: int = 20) -> list[tuple[str, str, str]]:
        """(revision, date, message), newest first."""
        if not self.available or not (self.git_dir / "HEAD").exists():
            return []
        # short hash, committer date (YYYY-MM-DD), subject; split by the ASCII unit separator
        # (%x1f), which no message contains
        out = self._git("log", f"-{limit}", "--format=%h%x1f%cs%x1f%s", "--", path, check=False)
        return [tuple(line.split(FIELD_SEP, 2)) for line in out.splitlines() if line]  # type: ignore[misc]

    def restore(self, path: str, revision: str) -> None:
        self._git("checkout", revision, "--", path)
