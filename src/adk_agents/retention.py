"""Tiered retention with a recoverable local quarantine."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .evidence import ArtifactStore
from .operational_record import OperationalRecord
from .ids import uuid7


@dataclass(frozen=True)
class CleanupResult:
    quarantined_count: int
    deleted_count: int
    failure_count: int


class RetentionService:
    """Applies 90/180-day retention without touching referenced evidence."""

    def __init__(self, record: OperationalRecord, artifacts: ArtifactStore, quarantine_directory: str | Path) -> None:
        self._record = record
        self._artifacts = artifacts
        self._quarantine = Path(quarantine_directory)

    def run(self, *, now: datetime | None = None) -> CleanupResult:
        now = now or datetime.now(timezone.utc)
        quarantined = deleted = failures = 0
        self._quarantine.mkdir(parents=True, exist_ok=True)
        with self._record.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT digest, storage_path FROM artifact_manifest WHERE quarantined_at IS NULL AND ((retention_class = 'routine' AND created_at < ?) OR (retention_class = 'protected' AND created_at < ?)) AND NOT EXISTS (SELECT 1 FROM evidence_ledger WHERE artifact_digest = artifact_manifest.digest)",
                ((now - timedelta(days=90)).isoformat(), (now - timedelta(days=180)).isoformat()),
            ).fetchall()
            candidate_count = len(rows)
            for row in rows:
                try:
                    os.replace(row["storage_path"], self._quarantine / row["digest"].removeprefix("sha256:"))
                    connection.execute("UPDATE artifact_manifest SET quarantined_at = ? WHERE digest = ?", (now.isoformat(), row["digest"]))
                    quarantined += 1
                except OSError:
                    failures += 1
            expired = connection.execute(
                "SELECT digest FROM artifact_manifest WHERE quarantined_at < ? AND NOT EXISTS (SELECT 1 FROM evidence_ledger WHERE artifact_digest = artifact_manifest.digest)",
                ((now - timedelta(days=7)).isoformat(),),
            ).fetchall()
            candidate_count += len(expired)
            for row in expired:
                try:
                    (self._quarantine / row["digest"].removeprefix("sha256:")).unlink(missing_ok=True)
                    connection.execute("DELETE FROM artifact_manifest WHERE digest = ?", (row["digest"],))
                    deleted += 1
                except OSError:
                    failures += 1
            self._record.verify(connection)
        summary = self._artifacts.write(
            json.dumps({"quarantined": quarantined, "deleted": deleted, "failures": failures}, sort_keys=True).encode(),
            logical_type="cleanup-summary",
            retention_class="permanent",
        )
        with self._record.connection() as connection:
            connection.execute(
                "INSERT INTO cleanup_run (run_id, policy_version, candidate_count, quarantined_count, deleted_count, failure_count, summary_artifact_digest, created_at) VALUES (?, 'v1', ?, ?, ?, ?, ?, ?)",
                (uuid7(), candidate_count, quarantined, deleted, failures, summary.digest, now.isoformat()),
            )
        return CleanupResult(quarantined, deleted, failures)
