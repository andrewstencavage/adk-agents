from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adk_agents.contracts import SpecialistType, TaskStatus
from adk_agents.manager import AdmissionDenied, Manager, accepted_result
from adk_agents.trace import TraceStore


def valid_task() -> dict[str, object]:
    return {
        "control_issue_ref": "#1",
        "story_ref": "#11",
        "dispatch_id": "dispatch-0001",
        "specialist": SpecialistType.RESEARCH.value,
        "objective": "Summarize one bounded public source.",
        "acceptance_criteria": ["Return one cited finding."],
        "requested_by": "human:andrew",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "budget_steps": 2,
    }


def manager(tmp_path):
    store = TraceStore(tmp_path / "record.sqlite3")
    return Manager(store, {role.value: accepted_result for role in SpecialistType}), store


def test_accepts_valid_bounded_task_and_records_redacted_trace(tmp_path):
    subject, store = manager(tmp_path)

    result = subject.admit(valid_task())

    assert result.status is TaskStatus.COMPLETED
    entry = store.entries()[-1]
    assert entry["decision"] == "accepted"
    assert entry["dispatch_id"] == "dispatch-0001"
    assert entry["request_digest"].startswith("sha256:")
    assert "Summarize" not in str(entry)
    ledger = store.ledger_entries()[-1]
    assert (ledger["dispatch_id"], ledger["action_type"]) == ("dispatch-0001", "manager_accepted")


def test_denies_malformed_task_before_specialist_execution(tmp_path):
    called = False

    def should_not_run(_):
        nonlocal called
        called = True
        raise AssertionError("must not run")

    store = TraceStore(tmp_path / "record.sqlite3")
    subject = Manager(
        store,
        {role.value: should_not_run for role in SpecialistType},
    )
    task = valid_task()
    task["objective"] = ""

    with pytest.raises(AdmissionDenied):
        subject.admit(task)

    assert not called
    entry = store.entries()[-1]
    assert entry["decision"] == "denied"
    assert entry["error_class"] == "ValidationError"


def test_denies_scope_grant_to_non_coding_specialist(tmp_path):
    subject, store = manager(tmp_path)
    task = valid_task()
    task["coding_agent_scope"] = {"approved_paths": ["src/"], "approved_commands": ["pytest"]}

    with pytest.raises(AdmissionDenied):
        subject.admit(task)

    assert store.entries()[-1]["decision"] == "denied"


def test_does_not_persist_an_invalid_request_payload(tmp_path):
    subject, _ = manager(tmp_path)
    task = valid_task()
    task["unexpected_credential"] = "secret-value-that-must-not-be-stored"

    with pytest.raises(AdmissionDenied):
        subject.admit(task)

    assert b"secret-value-that-must-not-be-stored" not in (tmp_path / "record.sqlite3").read_bytes()


def test_records_an_unexpected_specialist_failure(tmp_path):
    def fails_unexpectedly(_):
        raise RuntimeError("provider unavailable")

    store = TraceStore(tmp_path / "record.sqlite3")
    subject = Manager(store, {role.value: fails_unexpectedly for role in SpecialistType})

    with pytest.raises(AdmissionDenied):
        subject.admit(valid_task())

    entry = store.entries()[-1]
    assert entry["decision"] == "denied"
    assert entry["error_class"] == "InvalidSpecialistResult"


def test_rejects_a_registry_that_omits_an_approved_specialist(tmp_path):
    with pytest.raises(ValueError, match="exactly the four"):
        Manager(TraceStore(tmp_path / "record.sqlite3"), {SpecialistType.RESEARCH.value: accepted_result})
