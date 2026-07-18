from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from adk_agents.task_board import (
    BoardConfig,
    BoardComment,
    DispatchStore,
    ProjectStory,
    TaskBoardAdapter,
)


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


def test_prepared_dispatch_persists_a_uuid7_claim_event_across_restart(tmp_path):
    database = tmp_path / "record.sqlite3"
    first = DispatchStore(database).prepare(ready_story(), "ready")
    second = DispatchStore(database).prepare(ready_story(), "ready")

    assert first is not None and second is not None
    assert first.event_id == second.event_id
    assert UUID(first.event_id).version == 7


def test_user_comment_with_only_a_dispatch_id_cannot_satisfy_claim_reconciliation(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    database = tmp_path / "record.sqlite3"
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(database))
    prepared = DispatchStore(database).prepare(gateway.story, "ready")
    assert prepared is not None
    gateway.comments.append(BoardComment("user-comment", '{"dispatch_id":"' + prepared.dispatch_id + '"}'))

    claimed = adapter.claim_ready_story(gateway.story)

    assert claimed == prepared
    assert len(gateway.comments) == 2
    assert "dispatch.claimed" in gateway.comments[-1].body


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


def test_unassessed_model_block_is_visible_on_the_claimed_story(tmp_path):
    gateway = FakeBoardGateway(ready_story())
    adapter = TaskBoardAdapter(CONFIG, gateway, DispatchStore(tmp_path / "record.sqlite3"))
    dispatch = adapter.claim_ready_story(gateway.story)
    assert dispatch is not None

    adapter.block_claimed_story(gateway.story, dispatch, "No eligible assessed model.")

    assert gateway.story.status_option_id == "blocked"
    assert gateway.status_writes == ["in-progress", "blocked"]
    assert "story.blocked" in gateway.comments[-1].body
    assert "No eligible assessed model." in gateway.comments[-1].body
