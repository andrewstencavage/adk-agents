"""Host-enforced isolation and commit boundaries for Coding stories."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .contracts import BoardUpdateRequest, SpecialistResult, TaskStatus


class ScopeGapBlocked(PermissionError):
    """Raised when Coding requests a path or command outside its recorded scope."""

    def __init__(self, gap: str) -> None:
        super().__init__(gap)
        self.result = SpecialistResult(
            status=TaskStatus.BLOCKED,
            summary="Coding is blocked by an unapproved scope gap.",
            next_manager_action="request_user_scope_approval",
            scope_gap=gap,
            board_update_request=BoardUpdateRequest(
                proposed_status="Blocked", rationale="Coding requires a scope expansion."
            ),
        )


@dataclass(frozen=True)
class PythonCommandProfile:
    """Manifest-selected, non-network commands permitted for a Python story."""

    commands: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        executables = {"pytest", "ruff", "mypy", "pyright"}
        blocked_words = {"add", "install", "pip", "sync", "curl", "wget", "--install-types"}
        if not self.commands or any(not command for command in self.commands):
            raise ValueError("Python command profile requires one or more commands")
        if any(not self._is_python_check(command, executables, blocked_words) for command in self.commands):
            raise ValueError("Python command profile excludes network and installation commands")

    @staticmethod
    def _is_python_check(
        command: tuple[str, ...], executables: set[str], blocked_words: set[str]
    ) -> bool:
        executable = command[0]
        if executable == "uv":
            return len(command) >= 3 and command[1] == "run" and command[2] in executables
        return executable in executables and not blocked_words.intersection(command)


@dataclass(frozen=True)
class ScopeExpansion:
    """A user-approved and externally recorded addition to a Coding scope."""

    approved_by: str
    approval_ref: str
    recorded_by: str = ""
    paths: tuple[str, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if not self.approved_by.startswith("human:") or not self.approval_ref:
            raise ValueError("scope expansions require a recorded user approval")
        if self.recorded_by != "scrum_master":
            raise ValueError("scope expansions require a Scrum Master record")
        if not self.paths and not self.commands:
            raise ValueError("scope expansions must add a path or command")


@dataclass
class CodingBoundary:
    """The complete allowlist visible to an uncredentialed Coding specialist."""

    approved_paths: tuple[str, ...]
    command_profile: PythonCommandProfile
    _expansions: list[ScopeExpansion] = field(default_factory=list, init=False, repr=False)

    def require_path(self, path: str) -> None:
        if not any(path.startswith(prefix) for prefix in self._approved_paths()):
            raise ScopeGapBlocked(f"path requires user-approved scope expansion: {path}")

    def require_command(self, command: tuple[str, ...]) -> None:
        if command not in self.allowed_commands():
            display = " ".join(command)
            raise ScopeGapBlocked(f"command requires user-approved scope expansion: {display}")

    def record_expansion(self, expansion: ScopeExpansion) -> None:
        """Apply an approval that the Scrum Master has already recorded on the story."""
        if expansion.commands:
            PythonCommandProfile(expansion.commands)
        self._expansions.append(expansion)

    def _approved_paths(self) -> tuple[str, ...]:
        return self.approved_paths + tuple(path for item in self._expansions for path in item.paths)

    def allowed_commands(self) -> tuple[tuple[str, ...], ...]:
        return self.command_profile.commands + tuple(
            command for item in self._expansions for command in item.commands
        )

    def required_checks(self) -> tuple[tuple[str, ...], ...]:
        """The immutable manifest checks the host must run before a commit."""
        return self.command_profile.commands


class WorktreeHost(Protocol):
    """Host-owned Git operation; Coding never receives this capability."""

    def create_worktree(self, *, base_branch: str, branch: str, path: Path) -> str: ...


@dataclass(frozen=True)
class StoryWorktree:
    """A single story's host-created worktree and branch identity."""

    branch: str
    path: Path
    base_commit: str | None = None

    @classmethod
    def create(cls, *, host: WorktreeHost, issue_number: int, slug: str, path: Path) -> "StoryWorktree":
        if issue_number < 1 or not slug or "/" in slug:
            raise ValueError("story worktree requires a positive issue number and simple slug")
        if path.exists():
            raise ValueError("story worktree path must be fresh")
        branch = f"agent/{issue_number}-{slug}"
        base_commit = host.create_worktree(base_branch="main", branch=branch, path=path)
        return cls(branch=branch, path=path, base_commit=base_commit)


class CommitHost(Protocol):
    """Host-side diff, check, and commit operations unavailable to Coding."""

    def changed_paths_for(self, worktree: StoryWorktree) -> tuple[str, ...]: ...

    def run_check(self, worktree: StoryWorktree, command: tuple[str, ...]) -> bool: ...

    def create_commit(self, worktree: StoryWorktree, message: str) -> str: ...

    def current_branch(self, worktree: StoryWorktree) -> str: ...

    def verifies_worktree(self, worktree: StoryWorktree) -> bool: ...


class CommitAuthority:
    """Creates a story commit only after independently verifying its diff and checks."""

    def __init__(self, host: CommitHost, boundary: CodingBoundary) -> None:
        self._host = host
        self._boundary = boundary
        self._committed_worktrees: set[tuple[str, str]] = set()

    def commit(self, worktree: StoryWorktree, message: str) -> str:
        identity = (str(worktree.path), worktree.branch)
        if identity in self._committed_worktrees:
            raise RuntimeError("story worktree already committed")
        if (not worktree.branch.startswith("agent/") or worktree.base_commit is None
                or self._host.current_branch(worktree) != worktree.branch
                or not self._host.verifies_worktree(worktree)):
            raise RuntimeError("commit authority requires a verified host-created story branch")
        for path in self._host.changed_paths_for(worktree):
            self._boundary.require_path(path)
        for command in self._boundary.required_checks():
            if not self._host.run_check(worktree, command):
                raise RuntimeError(f"required check failed: {' '.join(command)}")
        commit = self._host.create_commit(worktree, message)
        self._committed_worktrees.add(identity)
        return commit
