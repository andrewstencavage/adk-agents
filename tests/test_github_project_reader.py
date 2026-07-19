import json

import pytest

from adk_agents.github_project_reader import GitHubIssueComments, GitHubProjectFieldWriter, GitHubProjectReader, GitHubTaskBoardGateway
import adk_agents.github_project_reader as project_reader_module
from adk_agents.task_board import BoardComment, BoardConfig, ProjectStory


class FakeGraphQL:
    def execute(self, _query, _variables):
        return {"data": {"node": {"items": {"nodes": [
            {"id": "item-1", "updatedAt": "2026-07-18T00:00:00Z", "content": {"id": "issue-1", "number": 15, "closed": False, "labels": {"nodes": [{"name": "adk:story"}]}}, "fieldValues": {"nodes": [
                {"field": {"id": "status-field", "name": "Status"}, "optionId": "ready"},
                {"field": {"id": "primary-field", "name": "Primary Specialist"}, "name": "Research"},
                {"field": {"id": "dispatch-field", "name": "Dispatch ID"}, "text": "existing-dispatch"},
            ]}},
            {"id": "item-2", "updatedAt": "2026-07-18T00:00:00Z", "content": {"id": "issue-2", "number": 16, "closed": False, "labels": {"nodes": [{"name": "adk:story"}]}}, "fieldValues": {"nodes": [{"field": {"name": "Status"}, "optionId": "blocked"}]}}
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}


def test_reader_returns_only_open_managed_ready_story_candidates():
    config = BoardConfig("project", "owner", "repo", "ready", "progress", "blocked")
    stories = GitHubProjectReader(config, FakeGraphQL(), status_field_id="status-field", primary_specialist_field_id="primary-field", dispatch_field_id="dispatch-field").list_ready_stories()

    assert len(stories) == 1
    assert stories[0].issue_number == 15
    assert stories[0].primary_specialist == "Research"
    assert stories[0].dispatch_id == "existing-dispatch"


def test_reader_pages_until_every_ready_story_is_observed():
    def item(number):
        return {"id": f"item-{number}", "updatedAt": "now", "content": {"id": f"issue-{number}", "number": number, "closed": False, "labels": {"nodes": [{"name": "adk:story"}]}}, "fieldValues": {"nodes": [{"field": {"id": "status", "name": "Status"}, "optionId": "ready"}, {"field": {"id": "primary", "name": "Primary Specialist"}, "name": "Research"}]}}

    class PagedGraphQL:
        def __init__(self): self.calls = []
        def execute(self, _query, variables):
            self.calls.append(variables)
            if variables["after"] is None:
                return {"data": {"node": {"items": {"nodes": [item(1)], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}}}
            return {"data": {"node": {"items": {"nodes": [item(2)], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}

    graphql = PagedGraphQL()
    stories = GitHubProjectReader(BoardConfig("project", "owner", "repo", "ready", "progress", "blocked"), graphql).list_ready_stories()

    assert [story.issue_number for story in stories] == [1, 2]
    assert graphql.calls == [{"project": "project", "after": None}, {"project": "project", "after": "next"}]


def test_project_items_query_requests_page_info_from_the_items_connection():
    query = " ".join(project_reader_module._PROJECT_ITEMS_QUERY.split())

    assert "fieldValues(first: 30) { nodes" in query
    assert "} } } } pageInfo { hasNextPage endCursor }" in query
    assert "nodes { id updatedAt" in query


class RecordingGraphQL:
    def __init__(self): self.calls = []
    def execute(self, query, variables):
        self.calls.append((query, variables))
        return {"data": {}}


def test_project_field_writer_allows_only_dispatch_and_safe_agent_statuses():
    graphql = RecordingGraphQL()
    writer = GitHubProjectFieldWriter(graphql, project_id="project", status_field_id="status", dispatch_field_id="dispatch", agent_summary_field_id="summary", in_progress_option_id="progress", blocked_option_id="blocked")

    writer.set_dispatch_id("item", "dispatch-0001")
    writer.set_status("item", "progress")

    assert graphql.calls[0][1]["value"] == {"text": "dispatch-0001"}
    assert graphql.calls[1][1]["value"] == {"singleSelectOptionId": "progress"}
    with pytest.raises(PermissionError):
        writer.set_status("item", "ready")


def test_concrete_gateway_composes_pinned_project_and_issue_operations():
    story = ProjectStory("project", "owner", "repo", "item", "issue", 15, True, frozenset({"adk:story"}), "ready", "now", "now", "Research")

    class Reader:
        def get_story(self, item):
            assert item == "item"
            return story
        def issue_number(self, issue):
            assert issue == "issue"
            return 15

    class Writer:
        def __init__(self): self.calls = []
        def set_dispatch_id(self, item, dispatch): self.calls.append((item, dispatch))
        def set_status(self, item, status): self.calls.append((item, status))

    class Comments:
        def list(self, number):
            assert number == 15
            return [BoardComment("1", "existing")]
        def add(self, number, body):
            assert number == 15
            assert body == "claim"
            return "2"

    writer = Writer()
    gateway = GitHubTaskBoardGateway(Reader(), writer, Comments())

    assert gateway.get_story("item") == story
    assert gateway.list_comments("issue") == [BoardComment("1", "existing")]
    assert gateway.add_comment("issue", "claim") == "2"
    gateway.set_dispatch_id("item", "dispatch")
    gateway.set_status("item", "progress")
    assert writer.calls == [("item", "dispatch"), ("item", "progress")]


def test_issue_comment_reader_follows_github_next_page_links(monkeypatch):
    payloads = [
        ([{"id": 1, "body": "first"}], '<https://api.github.com/repos/owner/repo/issues/15/comments?page=2>; rel="next"'),
        ([{"id": 2, "body": "second"}], None),
    ]

    class Response:
        def __init__(self, payload, link):
            self._payload, self.headers = payload, {"Link": link} if link else {}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps(self._payload).encode()

    calls = []
    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return Response(*payloads.pop(0))
    monkeypatch.setattr(project_reader_module, "urlopen", fake_urlopen)

    comments = GitHubIssueComments("token", "owner", "repo").list(15)

    assert [comment.body for comment in comments] == ["first", "second"]
    assert calls[1][0].endswith("page=2")
