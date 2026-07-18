"""Mockable host-boundary workflows for ready specialist stories.

The production host supplies the callables here; no local model, GitHub token,
shell, or systemd access is embedded in a specialist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ScopeResult:
    blocked: bool
    reason: str = ""


class CodingSession:
    """Records proposed in-worktree paths and exposes no execution capability."""

    def __init__(self, worktree: str, *, approved_paths: tuple[str, ...], approved_commands: tuple[str, ...]) -> None:
        self.worktree = worktree
        self._paths, self.approved_commands, self._changed = approved_paths, approved_commands, []

    def propose_change(self, path: str) -> ScopeResult:
        if ".." in path.split("/") or not any(path == item or path.startswith(item + "/") for item in self._paths):
            return ScopeResult(True, "Blocked pending a user-approved scope expansion.")
        self._changed.append(path)
        return ScopeResult(False)

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(self._changed)


class CommitAuthority:
    """Host-only commit boundary: verify first, then invoke a constrained committer."""

    def __init__(self, verify: Callable[[tuple[str, ...]], bool], commit: Callable[[tuple[str, ...]], str]) -> None:
        self._verify, self._commit = verify, commit

    def commit(self, session: CodingSession) -> str:
        paths = session.changed_paths
        if not paths or not self._verify(paths):
            raise PermissionError("commit authority rejected an unverified worktree diff")
        return self._commit(paths)


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    path: str
    violated_requirement: str
    remediation: str


@dataclass(frozen=True)
class ReviewOutcome:
    needs_revision: bool = False
    blocked: bool = False
    pr_ref: str | None = None


class ReviewService:
    """Read-only review gate; its PR payload explicitly has no approval authority."""

    def __init__(self, create_pr: Callable[[dict[str, object]], str], *, max_corrections: int = 2) -> None:
        self._create_pr, self._max_corrections, self._corrections = create_pr, max_corrections, 0

    def review(self, *, read_only: bool, findings: list[ReviewFinding], story_ref: str = "", branch: str = "", commits: tuple[str, ...] = (), checks: tuple[str, ...] = ()) -> ReviewOutcome:
        if not read_only:
            return ReviewOutcome(blocked=True)
        if findings:
            if self._corrections >= self._max_corrections:
                return ReviewOutcome(blocked=True)
            self._corrections += 1
            return ReviewOutcome(needs_revision=True)
        if not story_ref or not branch or not commits or not checks:
            return ReviewOutcome(blocked=True)
        return ReviewOutcome(pr_ref=self._create_pr({"story": story_ref, "head": branch, "base": "main", "commits": commits, "checks": checks, "review_gate": "v1", "approval": False, "merge": False, "deploy": False}))


@dataclass(frozen=True)
class StoryHandoff:
    kind: str
    subject: str
    evidence_ref: str


class IncidentService:
    """Persistent adapter can store these handoffs; policy creates one event per incident."""

    def __init__(self, record_handoff: Callable[[StoryHandoff], None]) -> None:
        self._record, self._failures, self._open = record_handoff, {}, {}

    def record_failure(self, operation: str, evidence_ref: str) -> str | None:
        self._failures[operation] = self._failures.get(operation, 0) + 1
        if self._failures[operation] < 3:
            return None
        incident = self._open.setdefault(operation, f"incident:{operation}")
        if self._failures[operation] == 3:
            self._record(StoryHandoff("incident.opened", incident, evidence_ref))
        return incident

    def record_recovery(self, operation: str, *, healthy_hours: int) -> str | None:
        incident = self._open.get(operation)
        if incident is None or healthy_hours < 24:
            return None
        self._open.pop(operation)
        self._failures.pop(operation, None)
        self._record(StoryHandoff("incident.recovered", incident, "redacted"))
        return incident


class BackupService:
    """Host adapters own external-drive I/O and isolated SQLite restore validation."""

    def __init__(self, copy_to_external: Callable[[str], None], verify_isolated_restore: Callable[[str], None]) -> None:
        self._copy, self._verify = copy_to_external, verify_isolated_restore

    def daily_backup(self, record_path: str) -> str:
        self._copy(record_path)
        return f"backup:{record_path}"

    def monthly_restore_verify(self, backup_ref: str) -> bool:
        self._verify(backup_ref)
        return True
