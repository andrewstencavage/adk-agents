"""Read-only GitHub Project V2 reader behind the typed task-board boundary."""

from __future__ import annotations

from typing import Any, Protocol
import json
from urllib.request import Request, urlopen

from .task_board import BoardConfig, ProjectStory


class GraphQLTransport(Protocol):
    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...


class GitHubGraphQLTransport:
    """Small authenticated transport; callers supply a token without logging it."""

    def __init__(self, token: str) -> None:
        self._token = token

    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode()
        request = Request("https://api.github.com/graphql", body, {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if payload.get("errors"):
            raise PermissionError("GitHub Project query was rejected")
        return payload


class GitHubProjectReader:
    def __init__(self, config: BoardConfig, graphql: GraphQLTransport) -> None:
        self._config, self._graphql = config, graphql

    def list_ready_stories(self) -> list[ProjectStory]:
        response = self._graphql.execute(_PROJECT_ITEMS_QUERY, {"project": self._config.project_id})
        nodes = response["data"]["node"]["items"]["nodes"]
        stories = [self._story(item) for item in nodes]
        return [story for story in stories if story is not None and story.status_option_id == self._config.ready_option_id and story.primary_specialist]

    def _story(self, item: dict[str, Any]) -> ProjectStory | None:
        issue = item.get("content")
        if not issue or issue.get("closed") or "adk:story" not in {label["name"] for label in issue.get("labels", {}).get("nodes", [])}:
            return None
        values = {value.get("field", {}).get("name"): value for value in item["fieldValues"]["nodes"]}
        status = values.get("Status", {}).get("optionId")
        primary = values.get("Primary Specialist", {}).get("name")
        return ProjectStory(self._config.project_id, self._config.owner, self._config.repository, item["id"], issue["id"], issue["number"], True, frozenset({"adk:story"}), status, item["updatedAt"], item["updatedAt"], primary)


_PROJECT_ITEMS_QUERY = """query($project: ID!) { node(id: $project) { ... on ProjectV2 { items(first: 100) { nodes { id updatedAt content { ... on Issue { id number closed labels(first: 20) { nodes { name } } } } fieldValues(first: 30) { nodes { ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2SingleSelectField { name } } optionId name } } } } } } } }"""
