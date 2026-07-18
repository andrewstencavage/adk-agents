"""Host-enforced isolation and commit boundaries for Coding stories."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ScopeGapBlocked(PermissionError):
    """Raised when Coding requests a path or command outside its recorded scope."""


@dataclass(frozen=True)
class ScopeExpansion:
    """A user-approved and externally recorded addition to a Coding scope."""

    approved_by: str
    approval_ref: str
    paths: tuple[str, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if not self.approved_by.startswith("human:") or not self.approval_ref:
            raise ValueError("scope expansions require a recorded user approval")
        if not self.paths and not self.commands:
            raise ValueError("scope expansions must add a path or command")


@dataclass
class CodingBoundary:
    """The complete allowlist visible to an uncredentialed Coding specialist."""

    approved_paths: tuple[str, ...]
    approved_commands: tuple[tuple[str, ...], ...]
    _expansions: list[ScopeExpansion] = field(default_factory=list, init=False, repr=False)

    def require_path(self, path: str) -> None:
        if not any(path.startswith(prefix) for prefix in self._approved_paths()):
            raise ScopeGapBlocked(f"path requires user-approved scope expansion: {path}")

    def require_command(self, command: tuple[str, ...]) -> None:
        if command not in self._approved_commands():
            display = " ".join(command)
            raise ScopeGapBlocked(f"command requires user-approved scope expansion: {display}")

    def record_expansion(self, expansion: ScopeExpansion) -> None:
        """Apply an approval that the Scrum Master has already recorded on the story."""
        self._expansions.append(expansion)

    def _approved_paths(self) -> tuple[str, ...]:
        return self.approved_paths + tuple(path for item in self._expansions for path in item.paths)

    def _approved_commands(self) -> tuple[tuple[str, ...], ...]:
        return self.approved_commands + tuple(
            command for item in self._expansions for command in item.commands
        )


class WorktreeHost(Protocol):
    """Host-owned Git operation; Coding never receives this capability."""

    def create_worktree(self, *, base_branch: str, branch: str, path: Path) -> None: ...


@dataclass(frozen=True)
class StoryWorktree:
    """A single story's host-created worktree and branch identity."""

    branch: str
    path: Path

    @classmethod
    def create(cls, *, host: WorktreeHost, issue_number: int, slug: str, path: Path) -> "StoryWorktree":
        if issue_number < 1 or not slug or "/" in slug:
            raise ValueError("story worktree requires a positive issue number and simple slug")
        branch = f"agent/{issue_number}-{slug}"
        host.create_worktree(base_branch="main", branch=branch, path=path)
        return cls(branch=branch, path=path)


class CommitHost(Protocol):
    """Host-side diff, check, and commit operations unavailable to Coding."""

    def changed_paths_for(self, worktree: StoryWorktree) -> tuple[str, ...]: ...

    def run_check(self, worktree: StoryWorktree, command: tuple[str, ...]) -> bool: ...

    def create_commit(self, worktree: StoryWorktree, message: str) -> str: ...


class CommitAuthority:
    """Creates a story commit only after independently verifying its diff and checks."""

    def __init__(self, host: CommitHost, boundary: CodingBoundary) -> None:
        self._host = host
        self._boundary = boundary

    def commit(self, worktree: StoryWorktree, message: str) -> str:
        for path in self._host.changed_paths_for(worktree):
            self._boundary.require_path(path)
        for command in self._boundary._approved_commands():
            if not self._host.run_check(worktree, command):
                raise RuntimeError(f"required check failed: {' '.join(command)}")
        return self._host.create_commit(worktree, message)
