"""Pure operational policies; host adapters perform systemd, backup, and GitHub I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

    def __init__(
        self,
        record: OperationalRecord,
        publish: Callable[[str, str], None],
        *,
        now: Callable[[], datetime] | None = None,
        max_failures: int | None = None,
    ) -> None:
        threshold = ServicePolicy().max_failures if max_failures is None else max_failures
        if threshold < 1:
            raise ValueError("incident failure threshold must be positive")
        self._record, self._publish = record, publish
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._max_failures = threshold

    def record_failure(self, operation: str, evidence_ref: str) -> str | None:
        now = self._now().isoformat()
        with self._record.connection() as connection:
            row = connection.execute(
                "SELECT consecutive_failures, closed_at, incident_published FROM operational_incident WHERE operation = ?",
                (operation,),
            ).fetchone()
            failures = 1 if row is None or row["closed_at"] is not None else row["consecutive_failures"] + 1
            incident = f"incident:{operation}" if failures >= self._max_failures else None
            connection.execute(
                "INSERT INTO operational_incident(operation, incident_ref, consecutive_failures, opened_at, healthy_since, closed_at, evidence_ref) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?) "
                "ON CONFLICT(operation) DO UPDATE SET incident_ref=excluded.incident_ref, "
                "consecutive_failures=excluded.consecutive_failures, opened_at=excluded.opened_at, "
                "healthy_since=NULL, closed_at=NULL, evidence_ref=excluded.evidence_ref, "
                "incident_published=CASE WHEN operational_incident.incident_ref = '' "
                "OR operational_incident.closed_at IS NOT NULL THEN 0 ELSE operational_incident.incident_published END",
                (operation, incident or "", failures, now, evidence_ref),
            )
            publish_needed = incident is not None and not connection.execute(
                "SELECT incident_published FROM operational_incident WHERE operation = ?", (operation,)
            ).fetchone()["incident_published"]
        if publish_needed:
            self._publish(incident, evidence_ref)
            with self._record.connection() as connection:
                connection.execute("UPDATE operational_incident SET incident_published = 1 WHERE operation = ?", (operation,))
        return incident

    def record_recovery(self, operation: str, *, healthy_hours: int) -> str | None:
        if healthy_hours < 24:
            return None
        now = self._now().isoformat()
        with self._record.connection() as connection:
            row = connection.execute("SELECT incident_ref FROM operational_incident WHERE operation = ? AND incident_ref <> '' AND closed_at IS NULL", (operation,)).fetchone()
            if row is None:
                return None
            incident = row["incident_ref"]
            connection.execute("UPDATE operational_incident SET closed_at = ?, healthy_since = ?, consecutive_failures = 0 WHERE operation = ?", (now, now, operation))
        self._publish(incident, "recovered")
        return incident

    def record_success(self, operation: str) -> str | None:
        """Record a healthy tick and close an open incident after 24 healthy hours."""
        current = self._now()
        with self._record.connection() as connection:
            row = connection.execute(
                "SELECT incident_ref, healthy_since, closed_at, incident_published FROM operational_incident WHERE operation = ?",
                (operation,),
            ).fetchone()
            if row is None or not row["incident_ref"] or row["closed_at"] is not None:
                if row is not None:
                    connection.execute(
                        "UPDATE operational_incident SET consecutive_failures = 0 WHERE operation = ?",
                        (operation,),
                    )
                return None
            incident = row["incident_ref"]
            healthy_since = row["healthy_since"]
            if healthy_since is None:
                connection.execute(
                    "UPDATE operational_incident SET healthy_since = ? WHERE operation = ?",
                    (current.isoformat(), operation),
                )
                return None
            if current - datetime.fromisoformat(healthy_since) < timedelta(hours=24):
                return None
        self._publish(incident, "recovered")
        with self._record.connection() as connection:
            connection.execute(
                "UPDATE operational_incident SET closed_at = ?, consecutive_failures = 0 WHERE operation = ?",
                (current.isoformat(), operation),
            )
        return incident
