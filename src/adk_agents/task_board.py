"""Small, single-process GitHub Project claim adapter for the local app."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID


def _uuid7() -> str:
    """Return a time-ordered UUID without requiring a newer Python runtime."""
    import secrets

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
    status_version: str
    primary_specialist: str | None
    dispatch_id: str | None = None


@dataclass(frozen=True)
class Dispatch:
    dispatch_id: str
    project_item_id: str
    ready_generation: int
    event_id: str
    occurred_at: str


@dataclass(frozen=True)
class BoardComment:
    comment_id: str
    body: str


class BoardGateway(Protocol):
    def get_story(self, project_item_id: str) -> ProjectStory: ...
    def list_comments(self, issue_node_id: str) -> list[BoardComment]: ...
    def add_comment(self, issue_node_id: str, body: str) -> str: ...
    def set_dispatch_id(self, project_item_id: str, dispatch_id: str) -> None: ...
    def set_status(self, project_item_id: str, option_id: str) -> None: ...


class DispatchStore:
    """SQLite records enough intent to avoid duplicate claims after a restart."""

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS board_observation (
                    project_item_id TEXT PRIMARY KEY,
                    last_status TEXT NOT NULL,
                    ready_generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS board_dispatch (
                    dispatch_id TEXT PRIMARY KEY,
                    project_item_id TEXT NOT NULL,
                    ready_generation INTEGER NOT NULL,
                    event_id TEXT,
                    occurred_at TEXT,
                    comment_id TEXT,
                    comment_digest TEXT,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(project_item_id, ready_generation)
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(board_dispatch)")}
            if "event_id" not in columns:
                db.execute("ALTER TABLE board_dispatch ADD COLUMN event_id TEXT")
            if "occurred_at" not in columns:
                db.execute("ALTER TABLE board_dispatch ADD COLUMN occurred_at TEXT")
            if "comment_digest" not in columns:
                db.execute("ALTER TABLE board_dispatch ADD COLUMN comment_digest TEXT")

    def prepare(self, story: ProjectStory, ready_status: str) -> Dispatch | None:
        with self._connect() as db:
            previous = db.execute(
                "SELECT last_status, ready_generation FROM board_observation WHERE project_item_id = ?",
                (story.project_item_id,),
            ).fetchone()
            generation = 1 if previous is None else previous["ready_generation"]
            if previous is not None and previous["last_status"] != ready_status and story.status_option_id == ready_status:
                generation += 1
            db.execute(
                """INSERT INTO board_observation(project_item_id, last_status, ready_generation) VALUES (?, ?, ?)
                   ON CONFLICT(project_item_id) DO UPDATE SET last_status=excluded.last_status, ready_generation=excluded.ready_generation""",
                (story.project_item_id, story.status_option_id, generation),
            )
            if story.status_option_id != ready_status:
                return None
            row = db.execute(
                "SELECT dispatch_id, ready_generation, event_id, occurred_at FROM board_dispatch WHERE project_item_id = ? AND ready_generation = ?",
                (story.project_item_id, generation),
            ).fetchone()
            if row is None:
                dispatch_id = _uuid7()
                event_id = _uuid7()
                occurred_at = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "INSERT INTO board_dispatch(dispatch_id, project_item_id, ready_generation, event_id, occurred_at) VALUES (?, ?, ?, ?, ?)",
                    (dispatch_id, story.project_item_id, generation, event_id, occurred_at),
                )
                return Dispatch(dispatch_id, story.project_item_id, generation, event_id, occurred_at)
            event_id, occurred_at = row["event_id"], row["occurred_at"]
            if not event_id or not occurred_at:
                event_id, occurred_at = _uuid7(), datetime.now(timezone.utc).isoformat()
                db.execute("UPDATE board_dispatch SET event_id = ?, occurred_at = ? WHERE dispatch_id = ?", (event_id, occurred_at, row["dispatch_id"]))
            return Dispatch(row["dispatch_id"], story.project_item_id, row["ready_generation"], event_id, occurred_at)

    def record_comment(self, dispatch_id: str, comment_id: str, body: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE board_dispatch SET comment_id = ?, comment_digest = ? WHERE dispatch_id = ?",
                (comment_id, hashlib.sha256(body.encode()).hexdigest(), dispatch_id),
            )

    def has_comment(self, dispatch_id: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT comment_id FROM board_dispatch WHERE dispatch_id = ?", (dispatch_id,)).fetchone()["comment_id"] is not None

    def confirm(self, dispatch_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE board_dispatch SET confirmed = 1 WHERE dispatch_id = ?", (dispatch_id,))

    def observe_status(self, project_item_id: str, status: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE board_observation SET last_status = ? WHERE project_item_id = ?", (status, project_item_id))

    def existing(self, story: ProjectStory) -> Dispatch | None:
        if story.dispatch_id is None:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT dispatch_id, ready_generation, event_id, occurred_at FROM board_dispatch WHERE dispatch_id = ? AND project_item_id = ?",
                (story.dispatch_id, story.project_item_id),
            ).fetchone()
        if row is None:
            return None
        event_id, occurred_at = row["event_id"], row["occurred_at"]
        if not event_id or not occurred_at:
            return None
        return Dispatch(row["dispatch_id"], story.project_item_id, row["ready_generation"], event_id, occurred_at)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        return db


class TaskBoardAdapter:
    """One-process claim flow. GitHub remains the visible lifecycle authority."""

    _claim_lock = threading.Lock()

    def __init__(self, config: BoardConfig, gateway: BoardGateway, store: DispatchStore) -> None:
        self._config, self._gateway, self._store = config, gateway, store

    def claim_ready_story(self, candidate: ProjectStory) -> Dispatch | None:
        with self._claim_lock:
            existing = self._store.existing(candidate)
            if existing is not None and candidate.status_option_id == self._config.in_progress_option_id:
                self._store.confirm(existing.dispatch_id)
                self._store.observe_status(candidate.project_item_id, candidate.status_option_id)
                return existing
            if not self._is_managed(candidate):
                return None
            dispatch = self._store.prepare(candidate, self._config.ready_option_id)
            if dispatch is None or not self._is_ready(candidate):
                return None
            current = self._gateway.get_story(candidate.project_item_id)
            if not self._is_ready(current):
                return None
            comment = next(
                (
                    comment
                    for comment in self._gateway.list_comments(current.issue_node_id)
                    if _is_claim_comment(comment.body, dispatch, current)
                ),
                None,
            )
            if comment is None:
                body = _claim_comment(dispatch, current)
                comment = BoardComment(self._gateway.add_comment(current.issue_node_id, body), body)
            self._store.record_comment(dispatch.dispatch_id, comment.comment_id, comment.body)
            current = self._gateway.get_story(candidate.project_item_id)
            if not self._is_ready(current):
                return None
            if current.dispatch_id != dispatch.dispatch_id:
                self._gateway.set_dispatch_id(current.project_item_id, dispatch.dispatch_id)
            current = self._gateway.get_story(candidate.project_item_id)
            if self._is_ready(current):
                self._gateway.set_status(current.project_item_id, self._config.in_progress_option_id)
            final = self._gateway.get_story(candidate.project_item_id)
            if final.status_option_id == self._config.in_progress_option_id and final.dispatch_id == dispatch.dispatch_id:
                self._store.confirm(dispatch.dispatch_id)
                self._store.observe_status(final.project_item_id, final.status_option_id)
                return dispatch
            return None

    def block_claimed_story(self, candidate: ProjectStory, dispatch: Dispatch, summary: str) -> bool:
        """Make an assessment gate refusal visible without overriding a user change."""
        with self._claim_lock:
            current = self._gateway.get_story(candidate.project_item_id)
            if not self._is_managed(current) or current.status_option_id != self._config.in_progress_option_id:
                return False
            if current.dispatch_id != dispatch.dispatch_id:
                return False
            self._gateway.add_comment(current.issue_node_id, _blocked_comment(dispatch, current, summary))
            current = self._gateway.get_story(candidate.project_item_id)
            if current.status_option_id != self._config.in_progress_option_id or current.dispatch_id != dispatch.dispatch_id:
                return False
            self._gateway.set_status(current.project_item_id, self._config.blocked_option_id)
            self._store.observe_status(current.project_item_id, self._config.blocked_option_id)
            return True

    def _is_managed(self, story: ProjectStory) -> bool:
        return (
            story.project_id == self._config.project_id and story.owner == self._config.owner
            and story.repository == self._config.repository and story.is_open and "adk:story" in story.labels
        )

    def _is_ready(self, story: ProjectStory) -> bool:
        return self._is_managed(story) and story.status_option_id == self._config.ready_option_id and story.primary_specialist is not None


def _claim_comment(dispatch: Dispatch, story: ProjectStory) -> str:
    event = {
        "event_id": dispatch.event_id, "dispatch_id": dispatch.dispatch_id, "kind": "dispatch.claimed",
        "occurred_at": dispatch.occurred_at, "schema_version": 1,
        "payload": {"project_item_id": story.project_item_id, "status": "In Progress"},
    }
    return "<!-- adk-event:v1\n" + json.dumps(event, separators=(",", ":")) + "\n-->\n## Agent update · In Progress\n\nClaim recorded."


def _is_claim_comment(body: str, dispatch: Dispatch, story: ProjectStory) -> bool:
    """Accept only the exact structured claim envelope, never a user substring."""
    prefix = "<!-- adk-event:v1\n"
    if not body.startswith(prefix):
        return False
    encoded, marker, _summary = body[len(prefix):].partition("\n-->")
    if not marker:
        return False
    try:
        event = json.loads(encoded)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(event, dict)
        and event.get("kind") == "dispatch.claimed"
        and event.get("schema_version") == 1
        and event.get("event_id") == dispatch.event_id
        and event.get("dispatch_id") == dispatch.dispatch_id
        and event.get("payload") == {"project_item_id": story.project_item_id, "status": "In Progress"}
        and isinstance(event.get("event_id"), str)
        and isinstance(event.get("occurred_at"), str)
    )


def _blocked_comment(dispatch: Dispatch, story: ProjectStory, summary: str) -> str:
    event = {
        "event_id": _uuid7(), "dispatch_id": dispatch.dispatch_id, "kind": "story.blocked",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "schema_version": 1,
        "payload": {"project_item_id": story.project_item_id, "status": "Blocked"},
    }
    return "<!-- adk-event:v1\n" + json.dumps(event, separators=(",", ":")) + "\n-->\n## Agent update · Blocked\n\n" + summary
