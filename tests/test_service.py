from adk_agents.service import build_assessment_gated_manager, build_live_polling_worker, build_mock_manager
from adk_agents.service import build_task_for
from adk_agents.service import build_polling_service
from datetime import datetime, timedelta, timezone
import json
import adk_agents.service as service_module
from adk_agents.contracts import SpecialistType, TaskStatus


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
        def dispatch(self, *event): events.append(("dispatch",) + event)
        def handoff(self, *event): events.append(event)

    service = build_polling_service(Board(), build_mock_manager(tmp_path), Workflow(), ProjectReader(), IssueReader())

    assert service.tick() == 1
    assert events[1][:2] == ("dispatch-1", "completed")


def test_mock_polling_loop_never_supplies_a_candidate(monkeypatch):
    observed = {}

    class Poller:
        def __init__(self, _board, _manager, _workflow, candidates, _task_for):
            observed["candidates"] = list(candidates())
        def run_forever(self, *, interval_seconds):
            observed["interval"] = interval_seconds

    monkeypatch.setattr(service_module, "PollingService", Poller)
    service_module.run_mock_polling_loop()

    assert observed == {"candidates": [], "interval": 60}


def test_assessment_gated_manager_has_no_eligible_model_before_assessment(tmp_path):
    record = service_module.OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    manager = build_assessment_gated_manager(record, tmp_path)

    task = {
        "control_issue_ref": "#1", "story_ref": "#20", "dispatch_id": "dispatch-0001",
        "specialist": "research", "objective": "Bounded research.",
        "acceptance_criteria": ["Return one finding."], "requested_by": "andrew",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "budget_steps": 1,
    }

    assert manager.admit(task).status is TaskStatus.BLOCKED


def test_live_worker_composes_only_when_both_github_credentials_and_ids_are_present(monkeypatch, tmp_path):
    for name, value in {
        "ADK_AGENTS_DATA_DIR": str(tmp_path), "ADK_AGENTS_GITHUB_PROJECT_ID": "PVT_1",
        "ADK_AGENTS_GITHUB_OWNER": "owner", "ADK_AGENTS_GITHUB_REPOSITORY": "repo",
        "ADK_AGENTS_READY_OPTION_ID": "ready", "ADK_AGENTS_IN_PROGRESS_OPTION_ID": "progress",
        "ADK_AGENTS_BLOCKED_OPTION_ID": "blocked", "ADK_AGENTS_GITHUB_STATUS_FIELD_ID": "status",
        "ADK_AGENTS_GITHUB_DISPATCH_FIELD_ID": "dispatch", "GITHUB_TOKEN": "project-token",
        "ADK_AGENTS_GITHUB_ISSUES_TOKEN": "issues-token",
    }.items():
        monkeypatch.setenv(name, value)
    config = service_module.ServiceConfig.from_environment()
    record = service_module.OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()

    assert isinstance(build_live_polling_worker(config, record), service_module.LeasedPollingWorker)
