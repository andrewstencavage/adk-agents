"""Mockable host-boundary workflows for ready specialist stories.

The production host supplies the callables here; no local model, GitHub token,
shell, or systemd access is embedded in a specialist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable


class PythonCommandProfile:
    """Exact argv allowlist for non-network Python checks, executed by a host runner."""

    def __init__(self, approved: tuple[tuple[str, ...], ...], runner: Callable[[tuple[str, ...]], str]) -> None:
        self._approved, self._runner = approved, runner

    def run(self, argv: tuple[str, ...]) -> str:
        if argv not in self._approved:
            raise PermissionError("command is outside the approved Python profile")
        return self._runner(argv)


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


class TransientCheckFailure(RuntimeError):
    """A runner/service failure eligible for exactly one automatic retry."""


class ReviewService:
    """Read-only review gate; its PR payload explicitly has no approval authority."""

    def __init__(self, create_pr: Callable[[dict[str, object]], str], *, max_corrections: int = 2) -> None:
        self._create_pr, self._max_corrections, self._corrections, self._transient_retried = create_pr, max_corrections, 0, False

    def check(self, run_check: Callable[[], object]) -> ReviewOutcome:
        try:
            run_check()
            return ReviewOutcome()
        except TransientCheckFailure:
            if self._transient_retried:
                return ReviewOutcome(blocked=True)
            self._transient_retried = True
            try:
                run_check()
                return ReviewOutcome()
            except TransientCheckFailure:
                return ReviewOutcome(blocked=True)

    def review(self, *, read_only: bool, findings: list[ReviewFinding], story_ref: str = "", branch: str = "", commits: tuple[str, ...] = (), checks: tuple[str, ...] = (), implementation_summary: str = "") -> ReviewOutcome:
        if not read_only:
            return ReviewOutcome(blocked=True)
        if findings:
            if self._corrections >= self._max_corrections:
                return ReviewOutcome(blocked=True)
            self._corrections += 1
            return ReviewOutcome(needs_revision=True)
        if not story_ref or not branch or not commits or not checks or not implementation_summary:
            return ReviewOutcome(blocked=True)
        return ReviewOutcome(pr_ref=self._create_pr({"story": story_ref, "head": branch, "base": "main", "commits": commits, "checks": checks, "summary": implementation_summary, "review_gate": "v1", "revision_count": self._corrections, "approval": False, "merge": False, "deploy": False}))


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
        self._sets: list[tuple[str, str, datetime]] = []

    def daily_backup(self, record_path: str) -> str:
        self._copy(record_path)
        return f"backup:{record_path}"

    def monthly_restore_verify(self, backup_ref: str) -> bool:
        self._verify(backup_ref)
        return True

    def record_set(self, backup_ref: str, cadence: str, created_at: datetime) -> None:
        if cadence not in {"daily", "monthly"}:
            raise ValueError("backup cadence must be daily or monthly")
        self._sets.append((backup_ref, cadence, created_at))

    def expired(self, now: datetime) -> tuple[str, ...]:
        limits = {"daily": 14, "monthly": 12}
        grouped = {cadence: sorted((item for item in self._sets if item[1] == cadence), key=lambda item: item[2], reverse=True) for cadence in limits}
        return tuple(item[0] for cadence, items in grouped.items() for item in items[limits[cadence]:])
