from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.error import URLError

import adk_agents.control_intake as control_intake
import pytest
from adk_agents.control_intake import GitHubStoryBoard
from adk_agents.github_project_reader import GitHubRateLimitError


def _item(number: int, *, status_field: str = "status", primary_field: str = "primary") -> dict:
    return {
        "id": f"item-{number}",
        "content": {"number": number},
        "fieldValues": {"nodes": [
            {"field": {"id": "wrong-status"}, "name": "Ready"},
            {"field": {"id": status_field}, "name": "Backlog"},
            {"field": {"id": "wrong-primary"}, "name": "Coding"},
            {"field": {"id": primary_field}, "name": "Research"},
        ]},
    }


def _field_values() -> dict:
    return {"data": {"node": {"fieldValues": {"nodes": _item(0)["fieldValues"]["nodes"], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}


def test_project_reconciliation_finds_a_story_on_a_later_graphql_page():
    calls: list[dict] = []

    def graphql(_query, variables):
        calls.append(variables)
        if "item" in variables:
            return _field_values()
        if variables["after"] is None:
            return {"data": {"node": {"items": {"nodes": [_item(1)], "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"}}}}}
        return {"data": {"node": {"items": {"nodes": [_item(57)], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}

    board = GitHubStoryBoard("token", "owner", "repo", "project", graphql, "status", "primary", "backlog", {"Research": "research"}, wait=lambda _seconds: None)
    board._request = lambda *_args: {"labels": []}

    state = board.publication_state(type("Story", (), {"number": 57})())

    assert state.is_on_project is True
    assert state.status == "Backlog"
    assert state.primary_specialist == "Research"
    assert calls == [{"project": "project", "after": None}, {"project": "project", "after": "cursor-1"}, {"item": "item-57", "after": None}]


def test_graphql_project_lookup_retries_a_transient_failure_with_a_bound():
    attempts = 0

    def graphql(_query, variables):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise URLError("temporary")
        if "item" in variables:
            return _field_values()
        return {"data": {"node": {"items": {"nodes": [_item(57)], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}

    board = GitHubStoryBoard("token", "owner", "repo", "project", graphql, "status", "primary", "backlog", {"Research": "research"}, wait=lambda _seconds: None)

    assert board._project_item(57) == {"id": "item-57", "status": "Backlog", "primary": "Research"}
    assert attempts == 4


def test_graphql_project_lookup_stops_after_the_bounded_retry_budget():
    attempts = 0

    def graphql(_query, _variables):
        nonlocal attempts
        attempts += 1
        raise URLError("temporary")

    board = GitHubStoryBoard("token", "owner", "repo", "project", graphql, "status", "primary", "backlog", {}, wait=lambda _seconds: None)

    with pytest.raises(URLError):
        board._project_item(57)
    assert attempts == 3


def test_graphql_project_lookup_retries_a_rate_limit_response():
    attempts = 0

    def graphql(_query, _variables):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise HTTPError("https://api.github.test/graphql", 403, "rate limited", {"Retry-After": "1"}, BytesIO())
        return _field_values() if "item" in _variables else {"data": {"node": {"items": {"nodes": [_item(57)], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}

    board = GitHubStoryBoard("token", "owner", "repo", "project", graphql, "status", "primary", "backlog", {}, wait=lambda _seconds: None)

    assert board._project_item(57)["id"] == "item-57"
    assert attempts == 4


def test_graphql_project_lookup_retries_a_rate_limit_error_from_the_transport():
    attempts = 0

    def graphql(_query, variables):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise GitHubRateLimitError("rate limited")
        return _field_values() if "item" in variables else {"data": {"node": {"items": {"nodes": [_item(57)], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}

    board = GitHubStoryBoard("token", "owner", "repo", "project", graphql, "status", "primary", "backlog", {}, wait=lambda _seconds: None)

    assert board._project_item(57)["id"] == "item-57"
    assert attempts == 3


def test_project_reconciliation_reads_pinned_field_values_on_a_later_page():
    def graphql(_query, variables):
        if "project" in variables:
            return {"data": {"node": {"items": {"nodes": [_item(57)], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        if variables["after"] is None:
            return {"data": {"node": {"fieldValues": {"nodes": [
                {"field": {"id": "wrong-status"}, "name": "Ready"},
                {"field": {"id": "wrong-primary"}, "name": "Coding"},
            ], "pageInfo": {"hasNextPage": True, "endCursor": "field-cursor"}}}}}
        return {"data": {"node": {"fieldValues": {"nodes": [
            {"field": {"id": "status"}, "name": "Backlog"},
            {"field": {"id": "primary"}, "name": "Research"},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}

    board = GitHubStoryBoard("token", "owner", "repo", "project", graphql, "status", "primary", "backlog", {}, wait=lambda _seconds: None)

    assert board._project_item(57) == {"id": "item-57", "status": "Backlog", "primary": "Research"}


def test_project_field_writes_use_the_configured_pinned_identifiers():
    calls: list[tuple[str, dict]] = []

    def graphql(query, variables):
        calls.append((query, variables))
        return {"data": {}}

    board = GitHubStoryBoard("token", "owner", "repo", "project", graphql, "status-id", "primary-id", "backlog-id", {"Research": "research-id"}, wait=lambda _seconds: None)
    board._project_item = lambda _number: {"id": "item-57", "status": "Backlog", "primary": "Research"}

    board.set_backlog(type("Story", (), {"number": 57})())
    board.set_primary_specialist(type("Story", (), {"number": 57})(), "Research")

    assert [variables["field"] for _query, variables in calls] == ["status-id", "primary-id"]
    assert [variables["value"] for _query, variables in calls] == [
        {"singleSelectOptionId": "backlog-id"},
        {"singleSelectOptionId": "research-id"},
    ]


def test_story_lookup_follows_rest_link_pages_and_retries(monkeypatch):
    class Response:
        def __init__(self, payload, link=None):
            self._payload = payload
            self.headers = {} if link is None else {"Link": link}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, *_):
            return json.dumps(self._payload).encode()

    responses = iter([
        URLError("temporary"),
        Response([], '<https://api.github.test/page-2>; rel="next"'),
        Response([{"number": 57, "html_url": "https://github.test/issues/57", "body": "<!-- adk-intake:v1 intake-1 -->"}]),
    ])
    monkeypatch.setattr(control_intake, "urlopen", lambda *_args, **_kwargs: _next(responses))
    board = GitHubStoryBoard("token", "owner", "repo", "project", lambda *_: {}, "status", "primary", "backlog", {}, wait=lambda _seconds: None)

    story = board.find_story("<!-- adk-intake:v1 intake-1 -->")

    assert story is not None and story.number == 57


def _next(responses):
    value = next(responses)
    if isinstance(value, Exception):
        raise value
    return value
