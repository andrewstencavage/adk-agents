import pytest

from adk_agents.github_project_reader import GitHubProjectFieldWriter, GitHubProjectReader
from adk_agents.task_board import BoardConfig


class FakeGraphQL:
    def execute(self, _query, _variables):
        return {"data": {"node": {"items": {"nodes": [
            {"id": "item-1", "updatedAt": "2026-07-18T00:00:00Z", "content": {"id": "issue-1", "number": 15, "closed": False, "labels": {"nodes": [{"name": "adk:story"}]}}, "fieldValues": {"nodes": [
                {"field": {"name": "Status"}, "optionId": "ready"},
                {"field": {"name": "Primary Specialist"}, "name": "Research"},
            ]}},
            {"id": "item-2", "updatedAt": "2026-07-18T00:00:00Z", "content": {"id": "issue-2", "number": 16, "closed": False, "labels": {"nodes": [{"name": "adk:story"}]}}, "fieldValues": {"nodes": [{"field": {"name": "Status"}, "optionId": "blocked"}]}}
        ]}}}}


def test_reader_returns_only_open_managed_ready_story_candidates():
    config = BoardConfig("project", "owner", "repo", "ready", "progress", "blocked")
    stories = GitHubProjectReader(config, FakeGraphQL()).list_ready_stories()

    assert len(stories) == 1
    assert stories[0].issue_number == 15
    assert stories[0].primary_specialist == "Research"


class RecordingGraphQL:
    def __init__(self): self.calls = []
    def execute(self, query, variables):
        self.calls.append((query, variables))
        return {"data": {}}


def test_project_field_writer_allows_only_dispatch_and_safe_agent_statuses():
    graphql = RecordingGraphQL()
    writer = GitHubProjectFieldWriter(graphql, project_id="project", status_field_id="status", dispatch_field_id="dispatch", in_progress_option_id="progress", blocked_option_id="blocked")

    writer.set_dispatch_id("item", "dispatch-0001")
    writer.set_status("item", "progress")

    assert graphql.calls[0][1]["value"] == {"text": "dispatch-0001"}
    assert graphql.calls[1][1]["value"] == {"singleSelectOptionId": "progress"}
    with pytest.raises(PermissionError):
        writer.set_status("item", "ready")
