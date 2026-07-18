"""Restart-safe, least-privilege GitHub Project claim protocol."""

from __future__ import annotations

import json
import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid7() -> str:
    """Generate a time-ordered UUIDv7 on Python versions before uuid.uuid7."""
    milliseconds = int(time.time() * 1_000)
    value = (milliseconds << 80) | (0x7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    return str(UUID(int=value))


@dataclass(frozen=True)
class BoardConfig:
    project_id: str
    owner: str
    repository: str
    ready_option_id: str
    in_progress_option_id: str
    blocked_option_id: str


@dataclass(frozen=True)
class ProjectStory:
    project_id: str
    owner: str
    repository: str
    project_item_id: str
    issue_node_id: str
    issue_number: int
    is_open: bool
    labels: frozenset[str]
    status_option_id: str
    updated_at: str
    primary_specialist: str | None
    dispatch_id: str | None = None


@dataclass(frozen=True)
class Dispatch:
    dispatch_id: str
    project_item_id: str
    ready_generation: int


@dataclass(frozen=True)
class BoardComment:
    comment_id: str
    body: str


class BoardGateway(Protocol):
    """The only GitHub operations granted to the Scrum Master adapter."""

    def get_story(self, project_item_id: str) -> ProjectStory: ...

    def list_comments(self, issue_node_id: str) -> list[BoardComment]: ...

    def add_comment(self, issue_node_id: str, body: str) -> str: ...

    def set_dispatch_id(self, project_item_id: str, dispatch_id: str) -> None: ...

    def set_status(self, project_item_id: str, option_id: str) -> None: ...


class DispatchStore:
    """Local intent record used only to prevent duplicate external effects."""

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS board_observation (
                    project_id TEXT NOT NULL,
                    project_item_id TEXT NOT NULL,
                    last_status_option_id TEXT NOT NULL,
                    ready_generation INTEGER NOT NULL,
                    PRIMARY KEY(project_id, project_item_id)
                );
                CREATE TABLE IF NOT EXISTS board_dispatch (
                    dispatch_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    project_item_id TEXT NOT NULL,
                    issue_node_id TEXT NOT NULL,
                    ready_generation INTEGER NOT NULL,
                    source_updated_at TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('intent', 'confirmed', 'superseded')),
                    event_id TEXT NOT NULL,
                    comment_id TEXT,
                    comment_digest TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, project_item_id, ready_generation)
                );
                """
            )

    def prepare(self, story: ProjectStory, ready_option_id: str) -> Dispatch | None:
        """Persist/reuse intent, incrementing generation only on a Ready edge."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            observed = connection.execute(
                "SELECT last_status_option_id, ready_generation FROM board_observation "
                "WHERE project_id = ? AND project_item_id = ?",
                (story.project_id, story.project_item_id),
            ).fetchone()
            generation = 1
            if observed is not None:
                generation = observed["ready_generation"]
                if (
                    observed["last_status_option_id"] != ready_option_id
                    and story.status_option_id == ready_option_id
                ):
                    generation += 1
            connection.execute(
                """INSERT INTO board_observation(project_id, project_item_id, last_status_option_id, ready_generation)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(project_id, project_item_id) DO UPDATE SET
                     last_status_option_id = excluded.last_status_option_id,
                     ready_generation = excluded.ready_generation""",
                (story.project_id, story.project_item_id, story.status_option_id, generation),
            )
            if story.status_option_id != ready_option_id:
                return None
            existing = connection.execute(
                "SELECT dispatch_id, project_item_id, ready_generation FROM board_dispatch "
                "WHERE project_id = ? AND project_item_id = ? AND ready_generation = ?",
                (story.project_id, story.project_item_id, generation),
            ).fetchone()
            if existing is None:
                dispatch_id = _uuid7()
                connection.execute(
                    """INSERT INTO board_dispatch
                       (dispatch_id, project_id, project_item_id, issue_node_id, ready_generation, source_updated_at, state, event_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'intent', ?, ?)""",
                    (
                        dispatch_id,
                        story.project_id,
                        story.project_item_id,
                        story.issue_node_id,
                        generation,
                        story.updated_at,
                        _uuid7(),
                        _utc_now(),
                    ),
                )
                return Dispatch(dispatch_id, story.project_item_id, generation)
            return Dispatch(existing["dispatch_id"], existing["project_item_id"], existing["ready_generation"])

    def observe(self, story: ProjectStory, ready_option_id: str) -> None:
        """Record a non-dispatchable status so a later Ready edge gets a new generation."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            observed = connection.execute(
                "SELECT last_status_option_id, ready_generation FROM board_observation "
                "WHERE project_id = ? AND project_item_id = ?",
                (story.project_id, story.project_item_id),
            ).fetchone()
            generation = 1 if observed is None else observed["ready_generation"]
            if observed is not None and observed["last_status_option_id"] != ready_option_id and story.status_option_id == ready_option_id:
                generation += 1
            connection.execute(
                """INSERT INTO board_observation(project_id, project_item_id, last_status_option_id, ready_generation)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(project_id, project_item_id) DO UPDATE SET
                     last_status_option_id = excluded.last_status_option_id,
                     ready_generation = excluded.ready_generation""",
                (story.project_id, story.project_item_id, story.status_option_id, generation),
            )

    def claim_event(self, dispatch_id: str, story: ProjectStory) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT event_id FROM board_dispatch WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown dispatch")
        envelope = {
            "event_id": row["event_id"],
            "dispatch_id": dispatch_id,
            "kind": "dispatch.claimed",
            "occurred_at": _utc_now(),
            "schema_version": 1,
            "payload": {"project_item_id": story.project_item_id, "status": "In Progress"},
        }
        return "<!-- adk-event:v1\n" + json.dumps(envelope, separators=(",", ":")) + "\n-->\n## Agent update · In Progress\n\nClaim recorded."

    def comment_id(self, dispatch_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT comment_id FROM board_dispatch WHERE dispatch_id = ?", (dispatch_id,)
            ).fetchone()
        return None if row is None else row["comment_id"]

    def record_comment(self, dispatch_id: str, comment_id: str, body: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE board_dispatch SET comment_id = ?, comment_digest = ? WHERE dispatch_id = ?",
                (comment_id, _body_digest(body), dispatch_id),
            )

    def confirm(self, dispatch_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE board_dispatch SET state = 'confirmed' WHERE dispatch_id = ?", (dispatch_id,)
            )

    def confirmed_dispatch(self, story: ProjectStory) -> Dispatch | None:
        if story.dispatch_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT dispatch_id, project_item_id, ready_generation FROM board_dispatch
                   WHERE dispatch_id = ? AND project_id = ? AND project_item_id = ? AND state = 'confirmed'""",
                (story.dispatch_id, story.project_id, story.project_item_id),
            ).fetchone()
        return None if row is None else Dispatch(row["dispatch_id"], row["project_item_id"], row["ready_generation"])

    def pending_dispatch(self, story: ProjectStory) -> Dispatch | None:
        if story.dispatch_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT dispatch_id, project_item_id, ready_generation FROM board_dispatch
                   WHERE dispatch_id = ? AND project_id = ? AND project_item_id = ? AND state = 'intent'""",
                (story.dispatch_id, story.project_id, story.project_item_id),
            ).fetchone()
        return None if row is None else Dispatch(row["dispatch_id"], row["project_item_id"], row["ready_generation"])

    def supersede(self, dispatch_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE board_dispatch SET state = 'superseded' WHERE dispatch_id = ?", (dispatch_id,)
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


class TaskBoardAdapter:
    """Claims Ready stories; this adapter has no operation for Ready or Done."""

    def __init__(self, config: BoardConfig, gateway: BoardGateway, store: DispatchStore) -> None:
        self._config = config
        self._gateway = gateway
        self._store = store

    def claim_ready_story(self, candidate: ProjectStory) -> Dispatch | None:
        """Converge an eligible Ready story to one confirmed dispatch or no-op."""
        recovered = self._store.confirmed_dispatch(candidate)
        if (
            recovered is not None
            and candidate.status_option_id == self._config.in_progress_option_id
        ):
            return recovered
        pending = self._store.pending_dispatch(candidate)
        if (
            pending is not None
            and candidate.status_option_id == self._config.in_progress_option_id
            and self._find_claim_comment(candidate.issue_node_id, pending.dispatch_id) is not None
        ):
            self._store.confirm(pending.dispatch_id)
            return pending
        if not self._belongs_to_configured_board(candidate):
            return None
        if not self._is_ready_story(candidate):
            self._store.observe(candidate, self._config.ready_option_id)
            return None
        dispatch = self._store.prepare(candidate, self._config.ready_option_id)
        if dispatch is None:
            return None
        current = self._gateway.get_story(candidate.project_item_id)
        if not self._is_ready_story(current):
            self._store.supersede(dispatch.dispatch_id)
            return None

        if self._store.comment_id(dispatch.dispatch_id) is None:
            comment = self._store.claim_event(dispatch.dispatch_id, current)
            existing = self._find_claim_comment(current.issue_node_id, dispatch.dispatch_id)
            if existing is None:
                comment_id = self._gateway.add_comment(current.issue_node_id, comment)
                comment_body = comment
            else:
                comment_id = existing.comment_id
                comment_body = existing.body
            self._store.record_comment(
                dispatch.dispatch_id,
                comment_id,
                comment_body,
            )

        current = self._gateway.get_story(candidate.project_item_id)
        if not self._is_ready_story(current):
            self._store.supersede(dispatch.dispatch_id)
            return None
        if current.dispatch_id != dispatch.dispatch_id:
            self._gateway.set_dispatch_id(candidate.project_item_id, dispatch.dispatch_id)
        current = self._gateway.get_story(candidate.project_item_id)
        if self._is_ready_story(current):
            self._gateway.set_status(candidate.project_item_id, self._config.in_progress_option_id)
        final = self._gateway.get_story(candidate.project_item_id)
        if (
            final.status_option_id == self._config.in_progress_option_id
            and final.dispatch_id == dispatch.dispatch_id
        ):
            self._store.confirm(dispatch.dispatch_id)
            return dispatch
        return None

    def _is_ready_story(self, story: ProjectStory) -> bool:
        return (
            self._belongs_to_configured_board(story)
            and story.is_open
            and "adk:story" in story.labels
            and story.status_option_id == self._config.ready_option_id
            and story.primary_specialist in {"Scrum Master", "Research", "Coding", "Review"}
        )

    def _belongs_to_configured_board(self, story: ProjectStory) -> bool:
        return (
            story.project_id == self._config.project_id
            and story.owner == self._config.owner
            and story.repository == self._config.repository
        )

    def _find_claim_comment(self, issue_node_id: str, dispatch_id: str) -> BoardComment | None:
        for comment in self._gateway.list_comments(issue_node_id):
            if _is_matching_claim_event(comment.body, dispatch_id):
                return comment
        return None


def _body_digest(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _is_matching_claim_event(body: str, dispatch_id: str) -> bool:
    prefix = "<!-- adk-event:v1\n"
    if not body.startswith(prefix):
        return False
    try:
        encoded = body[len(prefix) :].split("\n-->", maxsplit=1)[0]
        event = json.loads(encoded)
        UUID(event["event_id"])
        UUID(event["dispatch_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return event.get("kind") == "dispatch.claimed" and event["dispatch_id"] == dispatch_id
