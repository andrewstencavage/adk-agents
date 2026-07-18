from __future__ import annotations

from dataclasses import dataclass

from adk_agents.contracts import SpecialistResult, TaskStatus
from adk_agents.service_loop import PollingService
from adk_agents.service_loop import task_from_issue_body
from datetime import datetime, timedelta, timezone
import json


@dataclass(frozen=True)
class Dispatch:
    dispatch_id: str


class Board:
    def __init__(self):
        self.claimed = []

    def claim_ready_story(self, candidate):
        self.claimed.append(candidate)
        return Dispatch("dispatch-0001")


class Manager:
    def admit(self, task):
        assert task["dispatch_id"] == "dispatch-0001"
        return SpecialistResult(status=TaskStatus.COMPLETED, summary="done", next_manager_action="record_handoff")


class Workflow:
    def __init__(self):
        self.events = []

    def handoff(self, dispatch_id, status, result):
        self.events.append((dispatch_id, status, result))


def test_polling_service_claims_only_then_admits_and_records_a_durable_handoff():
    board, workflow = Board(), Workflow()
    service = PollingService(board, Manager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})

    assert service.tick() == 1
    assert board.claimed == ["ready-story"]
    assert workflow.events[0][:2] == ("dispatch-0001", "completed")


def test_polling_service_runs_serial_ticks_until_the_host_stops_it():
    board, workflow = Board(), Workflow()
    service = PollingService(board, Manager(), workflow, lambda: ["ready-story"], lambda _candidate, dispatch: {"dispatch_id": dispatch, "story_ref": "#19"})
    stops = iter([False, False, True])
    waits = []

    service.run_forever(interval_seconds=5, should_stop=lambda: next(stops), wait=waits.append)

    assert waits == [5]
    assert len(workflow.events) == 1

def test_task_builder_accepts_only_the_structured_issue_block():
    raw = {"control_issue_ref":"#1","story_ref":"#20","specialist":"research","objective":"x","acceptance_criteria":["y"],"requested_by":"andrew","deadline":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"budget_steps":1}
    task = task_from_issue_body("```adk-task\n" + json.dumps(raw) + "\n```", "dispatch-0003")
    assert task["dispatch_id"] == "dispatch-0003"
