import sqlite3

from adk_agents.operational_record import OperationalRecord


def test_startup_applies_forward_migrations_and_enables_integrity_safeguards(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")

    record.startup()

    with record.connection() as connection:
        migration_versions = connection.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        ).fetchall()
        assert [row[0] for row in migration_versions] == [1, 2]
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
