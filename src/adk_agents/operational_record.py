"""Forward-migrated SQLite local system record."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


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


class OperationalRecord:
    """Owns schema validation and safe connections to one SQLite record."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def startup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migration (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
            checksum = hashlib.sha256(_MIGRATION_1.encode()).hexdigest()
            existing = connection.execute("SELECT checksum FROM schema_migration WHERE version = 1").fetchone()
            if existing is not None and existing[0] != checksum:
                raise RecordIntegrityError("migration checksum mismatch for version 1")
            if existing is None:
                try:
                    connection.execute("BEGIN")
                    for statement in _MIGRATION_1_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migration (version, checksum, applied_at) VALUES (1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                        (checksum,),
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
        required_tables = {"schema_migration", "artifact_manifest", "evidence_ledger", "cleanup_run"}
        required_triggers = {"evidence_ledger_append_only", "evidence_ledger_no_delete"}
        if integrity != "ok" or foreign_keys or not required_tables <= tables or not required_triggers <= triggers:
            raise RecordIntegrityError("SQLite integrity safeguards failed")
