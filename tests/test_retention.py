from datetime import datetime, timedelta, timezone

from adk_agents.evidence import ArtifactStore
from adk_agents.operational_record import OperationalRecord
from adk_agents.retention import RetentionService


def test_cleanup_quarantines_expired_routine_evidence_then_removes_it_after_recovery_window(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    artifacts = ArtifactStore(record, tmp_path / "artifacts")
    manifest = artifacts.write(b"expired", logical_type="test-transcript")
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    with record.connection() as connection:
        connection.execute("UPDATE artifact_manifest SET created_at = ? WHERE digest = ?", ((now - timedelta(days=91)).isoformat(), manifest.digest))
    cleanup = RetentionService(record, artifacts, tmp_path / "quarantine")

    first = cleanup.run(now=now)

    assert first.quarantined_count == 1
    assert not manifest.storage_path.exists()
    assert (tmp_path / "quarantine" / manifest.digest.removeprefix("sha256:")).exists()
    second = cleanup.run(now=now + timedelta(days=8))
    assert second.deleted_count == 1
    with record.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM cleanup_run").fetchone()[0] == 2
        assert connection.execute("SELECT candidate_count FROM cleanup_run ORDER BY created_at LIMIT 1").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM artifact_manifest WHERE digest = ?", (manifest.digest,)).fetchone()[0] == 0
