"""Pure operational policies; host adapters perform systemd, backup, and GitHub I/O."""

from __future__ import annotations

from dataclasses import dataclass


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
