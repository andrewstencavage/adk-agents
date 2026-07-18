"""Forward-migrated SQLite local system record."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator


class RecordIntegrityError(RuntimeError):
    """The local operational record cannot safely be used."""


_MIGRATION_1_STATEMENTS = (
    "CREATE TABLE artifact_manifest (digest TEXT PRIMARY KEY, logical_type TEXT NOT NULL, byte_size INTEGER NOT NULL, storage_path TEXT NOT NULL UNIQUE, producing_invocation_id TEXT, retention_class TEXT NOT NULL CHECK(retention_class IN ('routine', 'protected', 'permanent')), created_at TEXT NOT NULL, quarantined_at TEXT, deleted_at TEXT)",
    "CREATE TABLE evidence_ledger (event_id TEXT PRIMARY KEY, dispatch_id TEXT, invocation_id TEXT, action_type TEXT NOT NULL, input_digest TEXT, output_digest TEXT, outcome_class TEXT, error_class TEXT, artifact_digest TEXT REFERENCES artifact_manifest(digest), created_at TEXT NOT NULL)",
    "CREATE TABLE cleanup_run (run_id TEXT PRIMARY KEY, policy_version TEXT NOT NULL, candidate_count INTEGER NOT NULL, quarantined_count INTEGER NOT NULL, deleted_count INTEGER NOT NULL, failure_count INTEGER NOT NULL, summary_artifact_digest TEXT REFERENCES artifact_manifest(digest), created_at TEXT NOT NULL)",
    "CREATE TRIGGER evidence_ledger_append_only BEFORE UPDATE ON evidence_ledger BEGIN SELECT RAISE(ABORT, 'evidence ledger is append-only'); END",
    "CREATE TRIGGER evidence_ledger_no_delete BEFORE DELETE ON evidence_ledger BEGIN SELECT RAISE(ABORT, 'evidence ledger is append-only'); END",
)
_MIGRATION_1 = "\n".join(_MIGRATION_1_STATEMENTS)
_MIGRATION_2_STATEMENTS = (
    "CREATE TABLE model_assessment (assessment_id TEXT PRIMARY KEY, suite_version TEXT NOT NULL, runtime_id TEXT NOT NULL CHECK(runtime_id IN ('ollama', 'lm_studio')), model_id TEXT NOT NULL, fingerprint TEXT NOT NULL, runtime_version TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('passed', 'failed', 'error')), score REAL NOT NULL, artifact_ref TEXT NOT NULL, completed_at TEXT NOT NULL)",
    "CREATE INDEX model_assessment_lookup ON model_assessment (suite_version, runtime_id, model_id, fingerprint, runtime_version, role, completed_at DESC)",
    "CREATE TABLE model_selection (selection_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL, role TEXT NOT NULL, selected_runtime_id TEXT, selected_model_id TEXT, selected_fingerprint TEXT, override_used INTEGER NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('selected', 'blocked')), evidence_ref TEXT NOT NULL, created_at TEXT NOT NULL)",
)
_MIGRATION_2 = "\n".join(_MIGRATION_2_STATEMENTS)
_MIGRATION_3_STATEMENTS = (
    "CREATE TABLE dispatch (dispatch_id TEXT PRIMARY KEY, project_item_id TEXT, issue_node_id TEXT, ready_generation INTEGER, local_state TEXT NOT NULL, selected_model_ref TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE invocation_trace (invocation_id TEXT PRIMARY KEY, dispatch_id TEXT REFERENCES dispatch(dispatch_id), role TEXT NOT NULL, model_fingerprint TEXT, request_digest TEXT NOT NULL, response_digest TEXT, error_class TEXT, created_at TEXT NOT NULL)",
    "CREATE TABLE poll_checkpoint (project_id TEXT PRIMARY KEY, cursor TEXT, last_success_at TEXT, updated_at TEXT NOT NULL)",
)
_MIGRATION_3 = "\n".join(_MIGRATION_3_STATEMENTS)
_MIGRATION_4_STATEMENTS = (
    "CREATE TABLE operational_incident (operation TEXT PRIMARY KEY, incident_ref TEXT NOT NULL, consecutive_failures INTEGER NOT NULL, opened_at TEXT NOT NULL, healthy_since TEXT, closed_at TEXT, evidence_ref TEXT NOT NULL)",
)
_MIGRATION_4 = "\n".join(_MIGRATION_4_STATEMENTS)
_MIGRATION_5_STATEMENTS = (
    "CREATE TABLE story_handoff (dispatch_id TEXT NOT NULL REFERENCES dispatch(dispatch_id), status TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, delivered INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, PRIMARY KEY(dispatch_id, status))",
)
_MIGRATION_5 = "\n".join(_MIGRATION_5_STATEMENTS)
_MIGRATION_6_STATEMENTS = (
    "CREATE TABLE model_outcome (outcome_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL, role TEXT NOT NULL, runtime_id TEXT NOT NULL, model_id TEXT NOT NULL, fingerprint TEXT NOT NULL, outcome TEXT NOT NULL, created_at TEXT NOT NULL)",
)
_MIGRATION_6 = "\n".join(_MIGRATION_6_STATEMENTS)
_MIGRATION_7_STATEMENTS = (
    "CREATE TABLE polling_lease (project_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, expires_at TEXT NOT NULL)",
)
_MIGRATION_7 = "\n".join(_MIGRATION_7_STATEMENTS)
_MIGRATION_8_STATEMENTS = (
    "CREATE TABLE board_observation (project_item_id TEXT PRIMARY KEY, last_status TEXT NOT NULL, ready_generation INTEGER NOT NULL)",
    "CREATE TABLE board_dispatch (dispatch_id TEXT PRIMARY KEY, project_item_id TEXT NOT NULL, ready_generation INTEGER NOT NULL, event_id TEXT, occurred_at TEXT, comment_id TEXT, comment_digest TEXT, confirmed INTEGER NOT NULL DEFAULT 0, UNIQUE(project_item_id, ready_generation))",
)
_MIGRATION_8 = "\n".join(_MIGRATION_8_STATEMENTS)
_MIGRATION_9_STATEMENTS = (
    "CREATE TABLE manager_admission_trace (trace_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, dispatch_id TEXT, specialist TEXT, decision TEXT NOT NULL CHECK(decision IN ('accepted', 'denied')), request_digest TEXT NOT NULL, result_digest TEXT, error_class TEXT)",
)
_MIGRATION_9 = "\n".join(_MIGRATION_9_STATEMENTS)


class OperationalRecord:
    """Owns schema validation and safe connections to one SQLite record."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def startup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migration (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
            for version, statements, migration in ((1, _MIGRATION_1_STATEMENTS, _MIGRATION_1), (2, _MIGRATION_2_STATEMENTS, _MIGRATION_2), (3, _MIGRATION_3_STATEMENTS, _MIGRATION_3), (4, _MIGRATION_4_STATEMENTS, _MIGRATION_4), (5, _MIGRATION_5_STATEMENTS, _MIGRATION_5), (6, _MIGRATION_6_STATEMENTS, _MIGRATION_6), (7, _MIGRATION_7_STATEMENTS, _MIGRATION_7), (8, _MIGRATION_8_STATEMENTS, _MIGRATION_8), (9, _MIGRATION_9_STATEMENTS, _MIGRATION_9)):
                checksum = hashlib.sha256(migration.encode()).hexdigest()
                existing = connection.execute("SELECT checksum FROM schema_migration WHERE version = ?", (version,)).fetchone()
                if existing is not None and existing[0] != checksum:
                    raise RecordIntegrityError(f"migration checksum mismatch for version {version}")
                if existing is None:
                    try:
                        connection.execute("BEGIN")
                        for statement in statements:
                            connection.execute(statement)
                        connection.execute(
                            "INSERT INTO schema_migration (version, checksum, applied_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                            (version, checksum),
                        )
                        connection.commit()
                    except sqlite3.DatabaseError:
                        connection.rollback()
                        raise
            self.verify(connection)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def verify(self, connection: sqlite3.Connection | None = None) -> None:
        if connection is None:
            with self.connection() as verified_connection:
                self.verify(verified_connection)
            return
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        required_tables = {"schema_migration", "artifact_manifest", "evidence_ledger", "cleanup_run", "model_assessment", "model_selection", "model_outcome", "dispatch", "invocation_trace", "poll_checkpoint", "polling_lease", "board_observation", "board_dispatch", "manager_admission_trace", "operational_incident", "story_handoff"}
        required_triggers = {"evidence_ledger_append_only", "evidence_ledger_no_delete"}
        if integrity != "ok" or foreign_keys or not required_tables <= tables or not required_triggers <= triggers:
            raise RecordIntegrityError("SQLite integrity safeguards failed")


class PollingLease:
    """A renewable, project-scoped SQLite lease for the sole polling worker."""

    def __init__(self, record: OperationalRecord, *, project_id: str, owner_id: str, duration: timedelta = timedelta(seconds=90), now: Callable[[], datetime] | None = None) -> None:
        if not project_id or not owner_id or duration <= timedelta():
            raise ValueError("polling lease requires project, owner, and positive duration")
        self._record, self._project_id, self._owner_id = record, project_id, owner_id
        self._duration, self._now = duration, now or (lambda: datetime.now(timezone.utc))

    def acquire(self) -> bool:
        """Acquire or renew when the prior lease has expired or is already ours."""
        now = self._now()
        expiry = now + self._duration
        with self._record.connection() as connection:
            cursor = connection.execute(
                """INSERT INTO polling_lease(project_id, owner_id, expires_at) VALUES (?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET owner_id=excluded.owner_id, expires_at=excluded.expires_at
                   WHERE polling_lease.expires_at <= ? OR polling_lease.owner_id = excluded.owner_id""",
                (self._project_id, self._owner_id, expiry.isoformat(), now.isoformat()),
            )
        return cursor.rowcount == 1
