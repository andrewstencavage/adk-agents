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
    restarted.handoff("dispatch-0001", "completed", {"raw": "do-not-store"})

    with record.connection() as connection:
        rows = connection.execute("SELECT action_type, input_digest, output_digest FROM evidence_ledger").fetchall()
        dispatches = connection.execute("SELECT dispatch_id, local_state FROM dispatch").fetchall()
    assert [row["action_type"] for row in rows] == ["story.dispatch", "story.handoff"]
    assert [tuple(row) for row in dispatches] == [("dispatch-0001", "completed")]
    assert len(board_events) == 1
    assert b"do-not-store" not in record.path.read_bytes()


def test_restart_replays_an_undelivered_handoff_without_duplicating_evidence(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    events: list[dict[str, str]] = []

    def fail_once(_event):
        if not events:
            events.append({"failed": "once"})
            raise OSError("temporary board outage")
        events.append(_event)

    workflow = ApprovedStoryWorkflow(EvidenceLedger(record), fail_once)
    workflow.dispatch("dispatch-0002", "#19", {"bounded": True})
    try:
        workflow.handoff("dispatch-0002", "completed", {"bounded": True})
    except OSError:
        pass
    else:
        raise AssertionError("the board outage must surface to the caller")

    restarted = ApprovedStoryWorkflow(EvidenceLedger(record), events.append)
    assert restarted.recover_pending_handoffs() == 1
    assert restarted.recover_pending_handoffs() == 0
    assert len(events) == 2
    with record.connection() as connection:
        assert connection.execute("SELECT delivered FROM story_handoff").fetchone()["delivered"] == 1
        assert connection.execute("SELECT COUNT(*) FROM evidence_ledger WHERE action_type = 'story.handoff'").fetchone()[0] == 1
