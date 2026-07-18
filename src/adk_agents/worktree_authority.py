"""Host-only Git worktree creation; Coding never receives this authority."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class WorktreeAuthority:
    def __init__(self, run_host_git: Callable[[tuple[str, ...]], None], root: str | Path) -> None:
        self._run_host_git, self._root = run_host_git, Path(root)

    def create(self, issue_number: int, slug: str) -> Path:
        if issue_number < 1 or not slug or "/" in slug or ".." in slug:
            raise ValueError("invalid story worktree identity")
        branch = f"agent/{issue_number}-{slug}"
        path = self._root / f"{issue_number}-{slug}"
        self._run_host_git(("git", "worktree", "add", "-b", branch, str(path), "main"))
        return path
