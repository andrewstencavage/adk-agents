from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from adk_agents.contracts import SpecialistResult, SpecialistType, TaskStatus
from adk_agents.manager import Manager, accepted_result
from adk_agents.research import ResearchAgent, ResearchRuntimeFailure
from adk_agents.research_admission import AdmissionState, ResearchModelFingerprint
from adk_agents.task_board import (
    BoardConfig,
    BoardComment,
    DispatchStore,
    ProjectStory,
    ResearchBlock,
    ResearchBlockCause,
    ResearchBlockCoordinator,
    ResearchTaskBoardHandoff,
    TaskBoardAdapter,
)
from adk_agents.trace import TraceStore


CONFIG = BoardConfig(
    project_id="PVT_1",
    owner="andrewstencavage",
    repository="adk-agents",
    ready_option_id="ready",
    in_progress_option_id="in-progress",
    blocked_option_id="blocked",
)


class FakeBoardGateway:
    def __init__(self, story: ProjectStory) -> None:
        self.story = story
        self.comments: list[BoardComment] = []
        self.status_writes: list[str] = []
        self.dispatch_writes: list[str] = []

    def get_story(self, project_item_id: str) -> ProjectStory:
        assert project_item_id == self.story.project_item_id
        return self.story

    def list_comments(self, issue_node_id: str) -> list[BoardComment]:
        assert issue_node_id == self.story.issue_node_id
        return self.comments

    def add_comment(self, issue_node_id: str, body: str) -> str:
        assert issue_node_id == self.story.issue_node_id
        comment_id = f"comment-{len(self.comments) + 1}"
        self.comments.append(BoardComment(comment_id, body))
        return comment_id

    def set_dispatch_id(self, project_item_id: str, dispatch_id: str) -> None:
        assert project_item_id == self.story.project_item_id
        self.dispatch_writes.append(dispatch_id)
        self.story = replace(self.story, dispatch_id=dispatch_id)

    def set_status(self, project_item_id: str, option_id: str) -> None:
        assert project_item_id == self.story.project_item_id
        self.status_writes.append(option_id)
        self.story = replace(self.story, status_option_id=option_id)


def ready_story() -> ProjectStory:
    return ProjectStory(
        project_id="PVT_1",
        owner="andrewstencavage",
        repository="adk-agents",
        project_item_id="PVTI_1",
        issue_node_id="I_12",
        issue_number=12,
        is_open=True,
        labels=frozenset({"adk:story"}),
        status_option_id="ready",
        updated_at="2026-07-17T12:00:00+00:00",
        status_version="updated-at:2026-07-17T12:00:00+00:00",
        primary_specialist="Research",
    )


def test_claims_only_a_ready_open_managed_story_and_confirms_after_final_read(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))

    dispatch = adapter.claim_ready_story(gateway.story)

    assert dispatch is not None
    assert gateway.status_writes == ["in-progress"]
    assert gateway.dispatch_writes == [dispatch.dispatch_id]
    assert "dispatch.claimed" in gateway.comments[0].body
    assert gateway.story.dispatch_id == dispatch.dispatch_id


def test_restart_reuses_intent_and_does_not_duplicate_claim_comment(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    database = tmp_path / "record.sqlite3"
    first = TaskBoardAdapter(CONFIG, gateway, DispatchStore(database))

    claimed = first.claim_ready_story(gateway.story)
    restarted = TaskBoardAdapter(CONFIG, gateway, DispatchStore(database))
    recovered = restarted.claim_ready_story(gateway.story)

    assert claimed is not None
    assert recovered is not None
    assert recovered.dispatch_id == claimed.dispatch_id
    assert len(gateway.comments) == 1
    assert gateway.status_writes == ["in-progress"]


def test_user_movement_away_from_ready_before_claim_prevents_all_writes(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))
    candidate = gateway.story
    gateway.story = replace(gateway.story, status_option_id="blocked")

    assert adapter.claim_ready_story(candidate) is None
    assert gateway.comments == []
    assert gateway.dispatch_writes == []
    assert gateway.status_writes == []


def test_user_movement_after_claim_comment_prevents_stale_field_or_status_write(tmp_path):
    class MovingGateway(FakeBoardGateway):
        def add_comment(self, issue_node_id: str, body: str) -> str:
            comment_id = super().add_comment(issue_node_id, body)
            self.story = replace(self.story, status_option_id="blocked")
            return comment_id

    gateway = MovingGateway(ready_story())
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))

    assert adapter.claim_ready_story(gateway.story) is None
    assert len(gateway.comments) == 1
    assert gateway.dispatch_writes == []
    assert gateway.status_writes == []


def test_rejects_label_only_story_that_is_not_ready(tmp_path):
    gateway = FakeBoardGateway(replace(ready_story(), status_option_id="blocked"))
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))

    assert adapter.claim_ready_story(gateway.story) is None
    assert gateway.comments == []


def test_rejects_ready_story_without_a_user_selected_primary_specialist(tmp_path):
    gateway = FakeBoardGateway(replace(ready_story(), primary_specialist=None))
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))

    assert adapter.claim_ready_story(gateway.story) is None
    assert gateway.comments == []


def test_rejects_story_outside_the_configured_repository(tmp_path):
    gateway = FakeBoardGateway(replace(ready_story(), repository="other-repository"))
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))

    assert adapter.claim_ready_story(gateway.story) is None
    assert gateway.comments == []


def test_restart_confirms_a_persisted_in_progress_claim_intent(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    database = tmp_path / "record.sqlite3"
    first = TaskBoardAdapter(CONFIG, gateway, DispatchStore(database))
    dispatch = first.claim_ready_story(gateway.story)
    assert dispatch is not None

    recovered = TaskBoardAdapter(CONFIG, gateway, DispatchStore(database)).claim_ready_story(gateway.story)

    assert recovered == dispatch
    gateway.story = replace(gateway.story, status_option_id="ready", dispatch_id=None)
    next_dispatch = TaskBoardAdapter(CONFIG, gateway, DispatchStore(database)).claim_ready_story(gateway.story)
    assert next_dispatch is not None
    assert next_dispatch.dispatch_id != dispatch.dispatch_id


def test_a_later_ready_transition_gets_a_new_dispatch(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))
    first = adapter.claim_ready_story(gateway.story)
    assert first is not None
    gateway.story = replace(gateway.story, status_option_id="ready", dispatch_id=None)

    second = adapter.claim_ready_story(gateway.story)

    assert second is not None
    assert second.dispatch_id != first.dispatch_id


def test_blocks_in_progress_research_story_for_missing_admission_with_redacted_evidence(tmp_path):
    gateway = FakeBoardGateway(replace(ready_story(), status_option_id="in-progress"))
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))
    block = ResearchBlock(
        cause=ResearchBlockCause.ADMISSION_DENIED,
        evidence_refs=("sha256:" + "a" * 64,),
    )

    assert adapter.block_research_story(gateway.story, block)

    assert gateway.story.status_option_id == "blocked"
    assert gateway.status_writes == ["blocked"]
    assert "Research admission is missing or inactive." in gateway.comments[0].body
    assert "sha256:" + "a" * 64 in gateway.comments[0].body
    assert "secret" not in gateway.comments[0].body


def test_blocks_in_progress_research_story_after_runtime_retry_exhaustion(tmp_path):
    gateway = FakeBoardGateway(replace(ready_story(), status_option_id="in-progress"))
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))
    block = ResearchBlock(
        cause=ResearchBlockCause.RUNTIME_RETRY_EXHAUSTED,
        evidence_refs=("sha256:" + "b" * 64, "sha256:" + "c" * 64),
    )

    assert adapter.block_research_story(gateway.story, block)

    assert gateway.story.status_option_id == "blocked"
    assert "Research runtime retry was exhausted." in gateway.comments[0].body


def test_blocking_never_reopens_a_user_moved_story(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))
    block = ResearchBlock(ResearchBlockCause.ADMISSION_DENIED, ())

    assert not adapter.block_research_story(gateway.story, block)

    assert gateway.status_writes == []


def test_rejects_non_digest_evidence_before_it_can_reach_the_task_board():
    with pytest.raises(ValueError, match="sha256"):
        ResearchBlock(ResearchBlockCause.ADMISSION_DENIED, ("sha256:secret-value",))


def test_converts_manager_and_runtime_block_results_to_their_distinct_board_causes():
    denial = SpecialistResult(
        status=TaskStatus.BLOCKED,
        summary="Admission missing.",
        next_manager_action="block_story",
        evidence_refs=["sha256:" + "a" * 64],
    )
    exhaustion = SpecialistResult(
        status=TaskStatus.BLOCKED,
        summary="Runtime retry exhausted.",
        next_manager_action="block_story",
        evidence_refs=["sha256:" + "b" * 64],
    )

    assert ResearchBlock.from_admission_denial(denial).cause is ResearchBlockCause.ADMISSION_DENIED
    assert ResearchBlock.from_runtime_retry_exhaustion(exhaustion).cause is ResearchBlockCause.RUNTIME_RETRY_EXHAUSTED


def test_coordinator_surfaces_each_producer_result_as_an_in_progress_board_block(tmp_path):
    admission_gateway = FakeBoardGateway(replace(ready_story(), status_option_id="in-progress"))
    runtime_gateway = FakeBoardGateway(replace(ready_story(), status_option_id="in-progress"))
    denial = SpecialistResult(
        status=TaskStatus.BLOCKED,
        summary="Admission missing.",
        next_manager_action="block_story",
        evidence_refs=["sha256:" + "a" * 64],
    )
    exhaustion = SpecialistResult(
        status=TaskStatus.BLOCKED,
        summary="Runtime exhausted.",
        next_manager_action="block_story",
        evidence_refs=["sha256:" + "b" * 64],
    )

    assert ResearchBlockCoordinator(
        TaskBoardAdapter(CONFIG, admission_gateway, DispatchStore(tmp_path / "admission.sqlite3"))
    ).surface_manager_admission_denial(admission_gateway.story, denial)
    assert ResearchBlockCoordinator(
        TaskBoardAdapter(CONFIG, runtime_gateway, DispatchStore(tmp_path / "runtime.sqlite3"))
    ).surface_runtime_retry_exhaustion(runtime_gateway.story, exhaustion)

    assert admission_gateway.story.status_option_id == "blocked"
    assert runtime_gateway.story.status_option_id == "blocked"


def test_retry_after_a_failed_block_status_write_does_not_duplicate_the_comment(tmp_path):
    class FailingOnceGateway(FakeBoardGateway):
        def __init__(self, story: ProjectStory) -> None:
            super().__init__(story)
            self.fail_once = True

        def set_status(self, project_item_id: str, option_id: str) -> None:
            if option_id == "blocked" and self.fail_once:
                self.fail_once = False
                return
            super().set_status(project_item_id, option_id)

    gateway = FailingOnceGateway(replace(ready_story(), status_option_id="in-progress", dispatch_id="dispatch-0001"))
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))
    block = ResearchBlock(ResearchBlockCause.ADMISSION_DENIED, ("sha256:" + "a" * 64,))

    assert not adapter.block_research_story(gateway.story, block)
    assert adapter.block_research_story(gateway.story, block)

    assert len(gateway.comments) == 1
    assert gateway.story.status_option_id == "blocked"


def test_handoff_surfaces_a_real_manager_admission_denial(tmp_path):
    class NoAdmission:
        def active_admission_for(self, _):
            return None

        def candidate_for(self, _):
            return None

    gateway = FakeBoardGateway(replace(ready_story(), status_option_id="in-progress"))
    board = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "board.sqlite3"))
    manager = Manager(
        TraceStore(tmp_path / "record.sqlite3"),
        {role.value: accepted_result for role in SpecialistType},
        NoAdmission(),
    )
    raw_task = {
        "control_issue_ref": "#1",
        "story_ref": "#32",
        "dispatch_id": "dispatch-0001",
        "specialist": "research",
        "objective": "Find one cited fact.",
        "acceptance_criteria": ["Return one finding."],
        "requested_by": "human:andrew",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "budget_steps": 2,
        "research_model_fingerprint": {
            "runtime": "ollama",
            "model": "qwen2.5:7b",
            "model_artifact": "sha256:" + "a" * 64,
            "runtime_config": {"temperature": "0"},
        },
    }

    result = ResearchTaskBoardHandoff(manager, ResearchBlockCoordinator(board)).dispatch(raw_task, gateway.story)

    assert result.status is TaskStatus.BLOCKED
    assert gateway.story.status_option_id == "blocked"


def test_handoff_surfaces_a_real_research_retry_exhaustion(tmp_path):
    class ActiveAdmission:
        def __init__(self, fingerprint: ResearchModelFingerprint) -> None:
            self.candidate = SimpleNamespace(state=AdmissionState.APPROVED, fingerprint=fingerprint)

        def active_admission_for(self, _):
            return self.candidate

        def candidate_for(self, _):
            return self.candidate

    class FailingSearch:
        def search(self, _):
            raise ResearchRuntimeFailure("unavailable")

    class Evidence:
        def __init__(self) -> None:
            self.count = 0

        def write(self, _):
            self.count += 1
            return "sha256:" + f"{self.count:064x}"

    fingerprint = ResearchModelFingerprint(
        runtime="ollama",
        model="qwen2.5:7b",
        model_artifact="sha256:" + "a" * 64,
        runtime_config={"temperature": "0"},
    )
    agent = ResearchAgent(FailingSearch(), Evidence())
    gateway = FakeBoardGateway(replace(ready_story(), status_option_id="in-progress"))
    board = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "board.sqlite3"))
    manager = Manager(
        TraceStore(tmp_path / "record.sqlite3"),
        {role.value: agent.run if role is SpecialistType.RESEARCH else accepted_result for role in SpecialistType},
        ActiveAdmission(fingerprint),
    )
    raw_task = {
        "control_issue_ref": "#1",
        "story_ref": "#32",
        "dispatch_id": "dispatch-0001",
        "specialist": "research",
        "objective": "Find one cited fact.",
        "acceptance_criteria": ["Return one finding."],
        "requested_by": "human:andrew",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "budget_steps": 2,
        "research_model_fingerprint": fingerprint.model_dump(mode="json"),
    }

    result = ResearchTaskBoardHandoff(manager, ResearchBlockCoordinator(board)).dispatch(raw_task, gateway.story)

    assert result.status is TaskStatus.BLOCKED
    assert gateway.story.status_option_id == "blocked"
