"""Read-only GitHub Project V2 reader behind the typed task-board boundary."""

from __future__ import annotations

from typing import Any, Protocol
import json
from urllib.request import Request, urlopen

from .task_board import BoardComment, BoardConfig, ProjectStory


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


class GitHubProjectFieldWriter:
    """Typed, narrow Project V2 field writer used only by the board adapter.

    The caller must provide pinned field IDs.  This object intentionally has
    no operation for Ready or Done: those remain human-only board transitions.
    """

    def __init__(self, graphql: GraphQLTransport, *, project_id: str, status_field_id: str, dispatch_field_id: str, in_progress_option_id: str, blocked_option_id: str) -> None:
        self._graphql = graphql
        self._project_id = project_id
        self._status_field_id = status_field_id
        self._dispatch_field_id = dispatch_field_id
        self._allowed_statuses = frozenset({in_progress_option_id, blocked_option_id})

    def set_dispatch_id(self, project_item_id: str, dispatch_id: str) -> None:
        self._set(project_item_id, self._dispatch_field_id, {"text": dispatch_id})

    def set_status(self, project_item_id: str, option_id: str) -> None:
        if option_id not in self._allowed_statuses:
            raise PermissionError("the GitHub adapter may write only In Progress or Blocked")
        self._set(project_item_id, self._status_field_id, {"singleSelectOptionId": option_id})

    def _set(self, project_item_id: str, field_id: str, value: dict[str, str]) -> None:
        self._graphql.execute(_UPDATE_PROJECT_FIELD_MUTATION, {"project": self._project_id, "item": project_item_id, "field": field_id, "value": value})


class GitHubIssueBodyReader:
    """Read only the issue body required for the explicit dispatch contract."""

    def __init__(self, token: str, owner: str, repository: str) -> None:
        self._token, self._owner, self._repository = token, owner, repository

    def body(self, issue_number: int) -> str:
        if issue_number < 1:
            raise ValueError("issue number must be positive")
        request = Request(
            f"https://api.github.com/repos/{self._owner}/{self._repository}/issues/{issue_number}",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        body = payload.get("body")
        if not isinstance(body, str):
            raise ValueError("GitHub issue has no textual body")
        return body


class GitHubIssueComments:
    """The only REST write used by the task-board claim adapter."""

    def __init__(self, token: str, owner: str, repository: str) -> None:
        self._token, self._owner, self._repository = token, owner, repository

    def list(self, issue_number: int) -> list[BoardComment]:
        request = Request(
            f"https://api.github.com/repos/{self._owner}/{self._repository}/issues/{issue_number}/comments",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        return [BoardComment(str(comment["id"]), comment["body"]) for comment in payload]

    def add(self, issue_number: int, body: str) -> str:
        request = Request(
            f"https://api.github.com/repos/{self._owner}/{self._repository}/issues/{issue_number}/comments",
            data=json.dumps({"body": body}).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            return str(json.load(response)["id"])


class GitHubProjectReader:
    def __init__(self, config: BoardConfig, graphql: GraphQLTransport) -> None:
        self._config, self._graphql = config, graphql
        self._issues: dict[str, int] = {}

    def list_ready_stories(self) -> list[ProjectStory]:
        response = self._graphql.execute(_PROJECT_ITEMS_QUERY, {"project": self._config.project_id})
        nodes = response["data"]["node"]["items"]["nodes"]
        stories = [self._story(item) for item in nodes]
        return [story for story in stories if story is not None and story.status_option_id == self._config.ready_option_id and story.primary_specialist]

    def get_story(self, project_item_id: str) -> ProjectStory:
        response = self._graphql.execute(_PROJECT_ITEM_QUERY, {"item": project_item_id})
        story = self._story(response["data"]["node"])
        if story is None:
            raise PermissionError("the requested Project item is not an open managed story")
        return story

    def _story(self, item: dict[str, Any]) -> ProjectStory | None:
        issue = item.get("content")
        if not issue or issue.get("closed") or "adk:story" not in {label["name"] for label in issue.get("labels", {}).get("nodes", [])}:
            return None
        values = {value.get("field", {}).get("name"): value for value in item["fieldValues"]["nodes"]}
        status = values.get("Status", {}).get("optionId")
        primary = values.get("Primary Specialist", {}).get("name")
        self._issues[issue["id"]] = issue["number"]
        return ProjectStory(self._config.project_id, self._config.owner, self._config.repository, item["id"], issue["id"], issue["number"], True, frozenset({"adk:story"}), status, item["updatedAt"], item["updatedAt"], primary)

    def issue_number(self, issue_node_id: str) -> int:
        try:
            return self._issues[issue_node_id]
        except KeyError as error:
            raise PermissionError("the task-board adapter did not read this Issue") from error


class GitHubTaskBoardGateway:
    """Concrete claim gateway composed from the narrow Project and Issue adapters."""

    def __init__(self, reader: GitHubProjectReader, writer: GitHubProjectFieldWriter, comments: GitHubIssueComments) -> None:
        self._reader, self._writer, self._comments = reader, writer, comments

    def get_story(self, project_item_id: str) -> ProjectStory:
        return self._reader.get_story(project_item_id)

    def list_comments(self, issue_node_id: str) -> list[BoardComment]:
        return self._comments.list(self._reader.issue_number(issue_node_id))

    def add_comment(self, issue_node_id: str, body: str) -> str:
        return self._comments.add(self._reader.issue_number(issue_node_id), body)

    def set_dispatch_id(self, project_item_id: str, dispatch_id: str) -> None:
        self._writer.set_dispatch_id(project_item_id, dispatch_id)

    def set_status(self, project_item_id: str, option_id: str) -> None:
        self._writer.set_status(project_item_id, option_id)


_PROJECT_ITEMS_QUERY = """query($project: ID!) { node(id: $project) { ... on ProjectV2 { items(first: 100) { nodes { id updatedAt content { ... on Issue { id number closed labels(first: 20) { nodes { name } } } } fieldValues(first: 30) { nodes { ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2SingleSelectField { name } } optionId name } } } } } } } }"""

_PROJECT_ITEM_QUERY = """query($item: ID!) { node(id: $item) { ... on ProjectV2Item { id updatedAt content { ... on Issue { id number closed labels(first: 20) { nodes { name } } } } fieldValues(first: 30) { nodes { ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2SingleSelectField { name } } optionId name } } } } } }"""

_UPDATE_PROJECT_FIELD_MUTATION = """mutation($project: ID!, $item: ID!, $field: ID!, $value: ProjectV2FieldValue!) { updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: $value}) { projectV2Item { id } } }"""
