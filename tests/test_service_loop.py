from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from adk_agents.contracts import SpecialistResult, TaskStatus
from adk_agents.operational_record import OperationalRecord
from adk_agents.service_loop import ControlPollingHealth, LeasedPollingWorker, PollingService
from adk_agents.service_loop import task_from_issue_body
import json


@dataclass(frozen=True)
class Dispatch:
    dispatch_id: str


class Board:
    def __init__(self):
        self.claimed = []
        self.blocked = []

    def claim_ready_story(self, candidate):
        self.claimed.append(candidate)
        return Dispatch("dispatch-0001")

    def block_claimed_story(self, candidate, dispatch, summary):
        self.blocked.append((candidate, dispatch.dispatch_id, summary))
        return True


class Manager:
    def admit(self, task):
        assert task["dispatch_id"] == "dispatch-0001"
        return SpecialistResult(status=TaskStatus.COMPLETED, summary="done", next_manager_action="record_handoff")


class Workflow:
    def __init__(self):
        self.events = []

    def dispatch(self, dispatch_id, story_ref, request):
        self.events.append((dispatch_id, "dispatch", story_ref, request))

    def handoff(self, dispatch_id, status, result):
        self.events.append((dispatch_id, status, result))


def test_polling_service_claims_only_then_admits_and_records_a_durable_handoff():
    board, workflow = Board(), Workflow()
    service = PollingService(board, Manager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})

    assert service.tick() == 1
    assert board.claimed == ["ready-story"]
    assert workflow.events[0][:3] == ("dispatch-0001", "dispatch", "#19")
    assert workflow.events[1][:2] == ("dispatch-0001", "completed")


def test_no_eligible_model_blocks_the_claimed_story_after_recording_the_handoff():
    class BlockedManager:
        def admit(self, _task):
            return SpecialistResult(status=TaskStatus.BLOCKED, summary="No eligible assessed model.", next_manager_action="create_blocked_story")

    board, workflow = Board(), Workflow()
    service = PollingService(board, BlockedManager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})

    assert service.tick() == 1
    assert workflow.events[0][:3] == ("dispatch-0001", "dispatch", "#19")
    assert workflow.events[1][:2] == ("dispatch-0001", "blocked")
    assert board.blocked == [("ready-story", "dispatch-0001", "No eligible assessed model.")]


def test_dispatch_failure_after_claim_becomes_a_visible_blocked_recovery():
    class FailingManager:
        def admit(self, _task):
            raise OSError("local runtime unavailable")

    board, workflow = Board(), Workflow()
    service = PollingService(board, FailingManager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})

    assert service.tick() == 0
    assert board.blocked == [("ready-story", "dispatch-0001", "Dispatch failed: OSError")]


def test_polling_service_runs_serial_ticks_until_the_host_stops_it():
    board, workflow = Board(), Workflow()
    service = PollingService(board, Manager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})
    stops = iter([False, False, True])
    waits = []

    service.run_forever(interval_seconds=5, should_stop=lambda: next(stops), wait=waits.append)

    assert waits == [5]
    assert len(workflow.events) == 2


def test_passive_worker_does_not_read_or_write_when_another_worker_holds_the_lease():
    board, workflow = Board(), Workflow()
    service = PollingService(board, Manager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})

    class Lease:
        def acquire(self): return False

    assert LeasedPollingWorker(service, Lease()).tick() == 0
    assert board.claimed == []
    assert workflow.events == []


def test_control_polling_failure_is_durable_visible_and_does_not_stop_ready_dispatch(tmp_path):
    board, workflow = Board(), Workflow()
    polling = PollingService(board, Manager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    published = []
    health = ControlPollingHealth(record, lambda incident, evidence: published.append((incident, evidence)))

    class Lease:
        def acquire(self): return True

    worker = LeasedPollingWorker(polling, Lease(), lambda: (_ for _ in ()).throw(ConnectionError("token=secret")), control_health=health)

    for _ in range(2):
        assert worker.tick() == 1

    restarted_health = ControlPollingHealth(record, lambda incident, evidence: published.append((incident, evidence)))
    worker = LeasedPollingWorker(polling, Lease(), lambda: (_ for _ in ()).throw(ConnectionError("token=secret")), control_health=restarted_health)
    assert worker.tick() == 1

    assert len(workflow.events) == 6
    assert published == [("incident:control_intake_poll", published[0][1])]
    assert published[0][1].startswith("sha256:")
    assert "secret" not in published[0][1]


def test_control_polling_closes_an_open_incident_after_24_healthy_hours(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    now = [datetime(2026, 7, 22, tzinfo=timezone.utc)]
    published = []
    health = ControlPollingHealth(record, lambda incident, evidence: published.append((incident, evidence)), now=lambda: now[0])

    for _ in range(3):
        health.record_failure(ConnectionError())
    health.record_success()
    now[0] += timedelta(hours=23, minutes=59)
    health.record_success()
    assert published == [("incident:control_intake_poll", published[0][1])]

    now[0] += timedelta(minutes=1)
    health.record_success()

    assert published[-1] == ("incident:control_intake_poll", "recovered")


def test_healthy_control_tick_resets_transient_failure_count(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    health = ControlPollingHealth(record, lambda _incident, _evidence: None)

    health.record_failure(ConnectionError())
    health.record_failure(ConnectionError())
    health.record_success()

    assert health.record_failure(ConnectionError()) is None

def test_task_builder_accepts_only_the_structured_issue_block():
    raw = {"control_issue_ref":"#1","story_ref":"#20","specialist":"research","objective":"x","acceptance_criteria":["y"],"requested_by":"andrew","deadline":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"budget_steps":1}
    task = task_from_issue_body("```adk-task\n" + json.dumps(raw) + "\n```", "dispatch-0003")
    assert task["dispatch_id"] == "dispatch-0003"
