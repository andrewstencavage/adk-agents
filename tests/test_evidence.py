import sqlite3

import pytest

from adk_agents.evidence import ArtifactStore, EvidenceLedger
from adk_agents.operational_record import OperationalRecord


def test_artifacts_are_content_addressed_and_cannot_be_overwritten(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    artifacts = ArtifactStore(record, tmp_path / "artifacts")

    manifest = artifacts.write(b"redacted evidence", logical_type="test-transcript")
    same_manifest = artifacts.write(b"redacted evidence", logical_type="test-transcript")

    assert manifest.digest == "sha256:b364a1779f6184fd0389c957024c95cc85aa2da4f34c171b861a4312174baec0"
    assert same_manifest == manifest
    assert artifacts.read(manifest.digest) == b"redacted evidence"
    with pytest.raises(FileExistsError):
        artifacts.write(b"different evidence", logical_type="test-transcript", digest=manifest.digest)


def test_ledger_is_append_only_and_persists_only_digests(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    ledger = EvidenceLedger(record)

    ledger.append(action_type="tool_finished", input_value={"token": "secret"}, output_value={"prompt": "raw text"})

    with record.connection() as connection:
        row = connection.execute("SELECT input_digest, output_digest FROM evidence_ledger").fetchone()
        assert row[0].startswith("sha256:")
        assert row[1].startswith("sha256:")
    assert b"secret" not in (tmp_path / "record.sqlite3").read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        with record.connection() as connection:
            connection.execute("DELETE FROM evidence_ledger")
