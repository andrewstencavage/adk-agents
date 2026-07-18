from adk_agents.github_project_reader import GitHubProjectReader
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
