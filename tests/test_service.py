from adk_agents.service import build_mock_manager
from adk_agents.service import build_task_for
from adk_agents.service import build_polling_service
from datetime import datetime, timedelta, timezone
import json


def test_mock_manager_has_only_the_static_specialist_registry(tmp_path):
    manager = build_mock_manager(tmp_path)
    assert set(manager._specialists) == {"scrum_master", "research", "coding", "review"}


def test_task_factory_reads_only_the_structured_issue_body():
    class Reader:
        def body(self, _number):
            return "```adk-task\n" + json.dumps({"control_issue_ref":"#1","story_ref":"#20","specialist":"research","objective":"x","acceptance_criteria":["y"],"requested_by":"andrew","deadline":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"budget_steps":1}) + "\n```"
    story = type("Story", (), {"issue_number": 20})()
    assert build_task_for(Reader())(story, "dispatch-0004")["dispatch_id"] == "dispatch-0004"


def test_composed_service_reads_ready_stories_then_claims_before_admission(tmp_path):
    class Story:
        issue_number = 20
    class ProjectReader:
        def list_ready_stories(self): return [Story()]
    class IssueReader:
        def body(self, _number):
            return "```adk-task\n" + json.dumps({"control_issue_ref":"#1","story_ref":"#20","specialist":"research","objective":"x","acceptance_criteria":["y"],"requested_by":"andrew","deadline":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"budget_steps":1}) + "\n```"
    class Board:
        def claim_ready_story(self, _story): return type("Claim", (), {"dispatch_id": "dispatch-1"})()
    events = []
    class Workflow:
        def handoff(self, *event): events.append(event)

    service = build_polling_service(Board(), build_mock_manager(tmp_path), Workflow(), ProjectReader(), IssueReader())

    assert service.tick() == 1
    assert events[0][:2] == ("dispatch-1", "completed")
