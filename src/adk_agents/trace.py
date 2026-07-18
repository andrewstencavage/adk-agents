"""Redacted, append-only admission traces for Manager decisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .evidence import EvidenceLedger
from .operational_record import OperationalRecord


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class TraceStore:
    """Stores hashes and classifications only, never request/result payloads."""

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._record = OperationalRecord(self._path)
        self._record.startup()
        self._ledger = EvidenceLedger(self._record)

    def record(
        self,
        *,
        decision: str,
        request: Any,
        dispatch_id: str | None = None,
        specialist: str | None = None,
        result: Any | None = None,
        error_class: str | None = None,
    ) -> None:
        """Append one durable trace record after an admission decision."""
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO manager_admission_trace
                   (trace_id, created_at, dispatch_id, specialist, decision, request_digest, result_digest, error_class)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    datetime.now(timezone.utc).isoformat(),
                    dispatch_id,
                    specialist,
                    decision,
                    _digest(request),
                    _digest(result) if result is not None else None,
                    error_class,
                ),
            )
        self._ledger.append(
            action_type=f"manager_{decision}",
            input_value=request,
            output_value=result,
            dispatch_id=dispatch_id,
            outcome_class=decision,
            error_class=error_class,
        )

    def entries(self) -> list[dict[str, str | None]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT dispatch_id, specialist, decision, request_digest, result_digest, error_class "
                "FROM manager_admission_trace ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def ledger_entries(self) -> list[dict[str, str | None]]:
        """Return the redacted operational ledger for audit inspection."""
        with self._record.connection() as connection:
            rows = connection.execute(
                "SELECT dispatch_id, action_type, input_digest, output_digest FROM evidence_ledger ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection
