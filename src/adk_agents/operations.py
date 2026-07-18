"""Pure operational policies; host adapters perform systemd, backup, and GitHub I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .operational_record import OperationalRecord


@dataclass(frozen=True)
class ServicePolicy:
    restart_delay_seconds: int = 10
    max_failures: int = 3
    failure_window_minutes: int = 10
    journal_days: int = 30
    journal_megabytes: int = 512
    daily_backup_sets: int = 14
    monthly_backup_sets: int = 12


class IncidentTracker:
    """Deduplicates persistent-failure incidents and enforces the recovery window."""

    def __init__(self, *, policy: ServicePolicy | None = None) -> None:
        self._policy = policy or ServicePolicy()
        self._failures: dict[str, int] = {}
        self._open: dict[str, str] = {}

    def record_failure(self, operation: str) -> str | None:
        self._failures[operation] = self._failures.get(operation, 0) + 1
        if self._failures[operation] < self._policy.max_failures:
            return None
        return self._open.setdefault(operation, f"incident:{operation}")

    def record_recovery(self, operation: str, *, healthy_hours: int) -> str | None:
        if healthy_hours < 24:
            return None
        self._failures.pop(operation, None)
        return self._open.pop(operation, None)


class PersistentIncidentTracker:
    """Restart-safe incident policy; the supplied adapter performs the one GitHub write."""

    def __init__(self, record: OperationalRecord, publish: Callable[[str, str], None]) -> None:
        self._record, self._publish = record, publish

    def record_failure(self, operation: str, evidence_ref: str) -> str | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._record.connection() as connection:
            row = connection.execute("SELECT incident_ref, consecutive_failures FROM operational_incident WHERE operation = ?", (operation,)).fetchone()
            failures = 1 if row is None else row["consecutive_failures"] + 1
            incident = f"incident:{operation}" if failures >= 3 else None
            connection.execute("INSERT INTO operational_incident(operation, incident_ref, consecutive_failures, opened_at, healthy_since, closed_at, evidence_ref) VALUES (?, ?, ?, ?, NULL, NULL, ?) ON CONFLICT(operation) DO UPDATE SET incident_ref=excluded.incident_ref, consecutive_failures=excluded.consecutive_failures, evidence_ref=excluded.evidence_ref, healthy_since=NULL", (operation, incident or "", failures, now, evidence_ref))
        if incident and failures == 3:
            self._publish(incident, evidence_ref)
        return incident
