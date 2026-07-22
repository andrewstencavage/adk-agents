"""GitHub adapters and polling worker for Control-issue Story intake."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import Request, urlopen

from .story_intake import ControlComment, PublishedStory, StoryAssessment, StoryIntakeService, StoryPublicationState


def resolve_story_intake_options(graphql: Callable[[str, dict[str, Any]], dict[str, Any]], project_id: str, status_field_id: str, primary_field_id: str) -> tuple[str, dict[str, str]]:
    """Resolve the user-visible Backlog and Primary-specialist options at startup."""
    response = graphql(_PROJECT_FIELDS, {"project": project_id})
    fields = response["data"]["node"]["fields"]["nodes"]
    status = next(field for field in fields if field.get("id") == status_field_id)
    primary = next(field for field in fields if field.get("id") == primary_field_id)
    return next(option["id"] for option in status["options"] if option["name"] == "Backlog"), {option["name"]: option["id"] for option in primary["options"]}


class GitHubControlIssue:
    """Reads and replies to one configured Control issue through the Issues API."""

    def __init__(self, token: str, owner: str, repository: str, issue_number: int) -> None:
        self._token, self._owner, self._repository, self._issue_number = token, owner, repository, issue_number

    def comments(self) -> list[ControlComment]:
        url = f"https://api.github.com/repos/{self._owner}/{self._repository}/issues/{self._issue_number}/comments?per_page=100"
        comments: list[ControlComment] = []
        while url:
            request = Request(url, headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"})
            with urlopen(request, timeout=30) as response:
                comments.extend(ControlComment(str(item["id"]), item["user"]["login"], item.get("body") or "") for item in json.load(response))
                url = _next_link(response.headers.get("Link"))
        return comments

    def reply(self, _comment: ControlComment, body: str) -> str:
        return str(self._request("POST", f"/issues/{self._issue_number}/comments", {"body": body})["id"])

    def find_reply(self, _comment: ControlComment, event_id: str) -> str | None:
        return next((comment.comment_id for comment in self.comments() if event_id in comment.body), None)

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> Any:
        request = Request(
            f"https://api.github.com/repos/{self._owner}/{self._repository}{path}",
            data=None if payload is None else json.dumps(payload).encode(), method=method,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=30) as response:
            return json.load(response)


class ControlIntakeWorker:
    """Runs one idempotent pass over immutable Control-issue comments."""

    def __init__(self, control_issue: GitHubControlIssue, intake: StoryIntakeService) -> None:
        self._control_issue, self._intake = control_issue, intake

    def tick(self) -> int:
        handled = 0
        for comment in self._control_issue.comments():
            outcome = self._intake.create(comment)
            if outcome.kind.value != "ignored":
                handled += 1
        return handled


class GitHubStoryBoard:
    """Publishes and reconciles Specialist stories using GitHub Issue/Project APIs."""

    def __init__(self, token: str, owner: str, repository: str, project_id: str, graphql: Callable[[str, dict[str, Any]], dict[str, Any]], status_field_id: str, primary_field_id: str, backlog_option_id: str, primary_options: dict[str, str]) -> None:
        self._token, self._owner, self._repository, self._project = token, owner, repository, project_id
        self._graphql, self._status_field, self._primary_field = graphql, status_field_id, primary_field_id
        self._backlog, self._primary_options = backlog_option_id, primary_options

    def create_issue(self, title: str, body: str) -> PublishedStory:
        issue = self._request("POST", "/issues", {"title": title, "body": body})
        return PublishedStory(issue["number"], issue["html_url"], "")

    def find_story(self, marker: str) -> PublishedStory | None:
        return next((self._story(issue) for issue in self._issues() if marker in (issue.get("body") or "")), None)

    def find_likely_duplicate(self, assessment: StoryAssessment) -> PublishedStory | None:
        return None

    def publication_state(self, story: PublishedStory) -> StoryPublicationState:
        issue = self._request("GET", f"/issues/{story.number}")
        item = self._project_item(story.number)
        return StoryPublicationState("adk:story" in {label["name"] for label in issue["labels"]}, item is not None, None if item is None else item["status"], None if item is None else item["primary"])

    def add_label(self, story: PublishedStory, label: str) -> None:
        self._request("POST", f"/issues/{story.number}/labels", {"labels": [label]})

    def add_to_project(self, story: PublishedStory) -> None:
        self._mutation("addProjectV2ItemById", {"projectId": self._project, "contentId": self._issue_node_id(story.number)})

    def set_backlog(self, story: PublishedStory) -> None:
        self._set_project_field(self._required_item(story.number), self._status_field, {"singleSelectOptionId": self._backlog})

    def set_primary_specialist(self, story: PublishedStory, specialist: str) -> None:
        self._set_project_field(self._required_item(story.number), self._primary_field, {"singleSelectOptionId": self._primary_options[specialist]})

    def _issues(self) -> list[dict[str, Any]]:
        return self._request("GET", "/issues?state=open&per_page=100")

    @staticmethod
    def _story(issue: dict[str, Any]) -> PublishedStory:
        return PublishedStory(issue["number"], issue["html_url"], "")

    def _issue_node_id(self, number: int) -> str:
        return self._request("GET", f"/issues/{number}")["node_id"]

    def _required_item(self, number: int) -> str:
        item = self._project_item(number)
        if item is None:
            raise RuntimeError("published story is not on the configured Project")
        return item["id"]

    def _project_item(self, issue_number: int) -> dict[str, str] | None:
        response = self._graphql(_PROJECT_ITEMS, {"project": self._project})
        for item in response["data"]["node"]["items"]["nodes"]:
            content = item.get("content") or {}
            if content.get("number") != issue_number:
                continue
            values = item["fieldValues"]["nodes"]
            status = next((value.get("name") for value in values if value.get("field", {}).get("id") == self._status_field), None)
            primary = next((value.get("name") for value in values if value.get("field", {}).get("id") == self._primary_field), None)
            return {"id": item["id"], "status": status, "primary": primary}
        return None

    def _set_project_field(self, item: str, field: str, value: dict[str, str]) -> None:
        self._graphql(_SET_PROJECT_FIELD, {"project": self._project, "item": item, "field": field, "value": value})

    def _mutation(self, name: str, values: dict[str, Any]) -> None:
        fields = ", ".join(f"{key}: ${key}" for key in values)
        variables = ", ".join(f"${key}: ID!" for key in values)
        self._graphql(f"mutation({variables}) {{ {name}(input: {{{fields}}}) {{ __typename }} }}", values)

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> Any:
        request = Request(f"https://api.github.com/repos/{self._owner}/{self._repository}{path}", data=None if payload is None else json.dumps(payload).encode(), method=method, headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)


_PROJECT_ITEMS = """query($project: ID!) { node(id: $project) { ... on ProjectV2 { items(first: 100) { nodes { id content { ... on Issue { number } } fieldValues(first: 30) { nodes { ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2SingleSelectField { id } } name } } } } } } } }"""
_SET_PROJECT_FIELD = """mutation($project: ID!, $item: ID!, $field: ID!, $value: ProjectV2FieldValue!) { updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: $value}) { projectV2Item { id } } }"""
_PROJECT_FIELDS = """query($project: ID!) { node(id: $project) { ... on ProjectV2 { fields(first: 100) { nodes { ... on ProjectV2SingleSelectField { id options { id name } } } } } } }"""


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        url, marker, _rest = part.strip().partition('; rel="next"')
        if marker and url.startswith("<") and url.endswith(">"):
            return url[1:-1]
    return None
