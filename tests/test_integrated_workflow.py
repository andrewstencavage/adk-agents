from __future__ import annotations

from adk_agents.evidence import EvidenceLedger
from adk_agents.integration import ApprovedStoryWorkflow
from adk_agents.operational_record import OperationalRecord


def test_approved_story_records_redacted_dispatch_and_handoff_across_restart(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    board_events: list[dict[str, str]] = []
    workflow = ApprovedStoryWorkflow(EvidenceLedger(record), board_events.append)

    workflow.dispatch("dispatch-0001", "#19", {"credential": "do-not-store"})
    workflow.dispatch("dispatch-0001", "#19", {"credential": "do-not-store"})
    restarted = ApprovedStoryWorkflow(EvidenceLedger(record), board_events.append)
    restarted.handoff("dispatch-0001", "completed", {"raw": "do-not-store"})

    with record.connection() as connection:
        rows = connection.execute("SELECT action_type, input_digest, output_digest FROM evidence_ledger").fetchall()
        dispatches = connection.execute("SELECT dispatch_id, local_state FROM dispatch").fetchall()
    assert [row["action_type"] for row in rows] == ["story.dispatch", "story.handoff"]
    assert [tuple(row) for row in dispatches] == [("dispatch-0001", "completed")]
    assert len(board_events) == 1
    assert b"do-not-store" not in record.path.read_bytes()
