"""Small, single-process GitHub Project claim adapter for the local app."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .contracts import SpecialistResult, TaskStatus
from .manager import AdmissionDenied, Manager


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


@dataclass(frozen=True)
class BoardComment:
    comment_id: str
    body: str


class ResearchBlockCause(str, Enum):
    """The two bounded Research failures surfaced by the MVP task-board flow."""

    ADMISSION_DENIED = "admission_denied"
    RUNTIME_RETRY_EXHAUSTED = "runtime_retry_exhausted"


@dataclass(frozen=True)
class ResearchBlock:
    """A redacted proposed block, containing only a known cause and evidence digests."""

    cause: ResearchBlockCause
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(re.fullmatch(r"sha256:[0-9a-f]{64}", ref) is None for ref in self.evidence_refs):
            raise ValueError("Research block evidence must use sha256 references")

    @classmethod
    def from_admission_denial(cls, denial: SpecialistResult) -> "ResearchBlock":
        """Convert the Manager's structured admission denial into a board-safe block."""
        return cls._from_result(ResearchBlockCause.ADMISSION_DENIED, denial)

    @classmethod
    def from_runtime_retry_exhaustion(cls, result: SpecialistResult) -> "ResearchBlock":
        """Convert the bounded Research runtime result into a board-safe block."""
        return cls._from_result(ResearchBlockCause.RUNTIME_RETRY_EXHAUSTED, result)

    @classmethod
    def _from_result(cls, cause: ResearchBlockCause, result: SpecialistResult) -> "ResearchBlock":
        if result.status is not TaskStatus.BLOCKED or result.next_manager_action != "block_story":
            raise ValueError("only a blocked specialist result can block a Research story")
        return cls(cause, tuple(result.evidence_refs))


class ResearchBlockCoordinator:
    """The narrow handoff from Manager/Research terminal results to the task board."""

    def __init__(self, board: "TaskBoardAdapter") -> None:
        self._board = board

    def surface_manager_admission_denial(self, story: ProjectStory, denial: SpecialistResult) -> bool:
        return self._board.block_research_story(story, ResearchBlock.from_admission_denial(denial))

    def surface_runtime_retry_exhaustion(self, story: ProjectStory, result: SpecialistResult) -> bool:
        return self._board.block_research_story(story, ResearchBlock.from_runtime_retry_exhaustion(result))


class ResearchTaskBoardHandoff:
    """Consumes actual Manager/Research terminal outcomes and surfaces required blocks."""

    def __init__(self, manager: Manager, blocks: ResearchBlockCoordinator) -> None:
        self._manager = manager
        self._blocks = blocks

    def dispatch(self, raw_task: dict[str, object], story: ProjectStory) -> SpecialistResult:
        try:
            result = self._manager.admit(raw_task)
        except AdmissionDenied as error:
            if error.denial is None:
                raise
            self._blocks.surface_manager_admission_denial(story, error.denial)
            return error.denial
        if result.status is TaskStatus.BLOCKED and result.next_manager_action == "block_story":
            self._blocks.surface_runtime_retry_exhaustion(story, result)
        return result


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
                    comment_id TEXT,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(project_item_id, ready_generation)
                );
                """
            )

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
                "SELECT dispatch_id, ready_generation FROM board_dispatch WHERE project_item_id = ? AND ready_generation = ?",
                (story.project_item_id, generation),
            ).fetchone()
            if row is None:
                dispatch_id = _uuid7()
                db.execute(
                    "INSERT INTO board_dispatch(dispatch_id, project_item_id, ready_generation) VALUES (?, ?, ?)",
                    (dispatch_id, story.project_item_id, generation),
                )
                return Dispatch(dispatch_id, story.project_item_id, generation)
            return Dispatch(row["dispatch_id"], story.project_item_id, row["ready_generation"])

    def record_comment(self, dispatch_id: str, comment_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE board_dispatch SET comment_id = ? WHERE dispatch_id = ?", (comment_id, dispatch_id))

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
                "SELECT dispatch_id, ready_generation FROM board_dispatch WHERE dispatch_id = ? AND project_item_id = ?",
                (story.dispatch_id, story.project_item_id),
            ).fetchone()
        return None if row is None else Dispatch(row["dispatch_id"], story.project_item_id, row["ready_generation"])

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
            if not self._store.has_comment(dispatch.dispatch_id):
                comment_id = next(
                    (
                        comment.comment_id
                        for comment in self._gateway.list_comments(current.issue_node_id)
                        if f'"dispatch_id":"{dispatch.dispatch_id}"' in comment.body
                    ),
                    None,
                )
                if comment_id is None:
                    comment_id = self._gateway.add_comment(current.issue_node_id, _claim_comment(dispatch, current))
                self._store.record_comment(dispatch.dispatch_id, comment_id)
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

    def block_research_story(self, candidate: ProjectStory, block: ResearchBlock) -> bool:
        """Record one bounded Research failure and move only an eligible story to Blocked."""
        with self._claim_lock:
            if not self._is_research_story(candidate):
                return False
            current = self._gateway.get_story(candidate.project_item_id)
            if not self._is_blockable(current):
                return False
            block_id = _research_block_id(current, block)
            if not any(
                f'"block_id":"{block_id}"' in comment.body
                for comment in self._gateway.list_comments(current.issue_node_id)
            ):
                self._gateway.add_comment(current.issue_node_id, _research_block_comment(current, block, block_id))
            current = self._gateway.get_story(candidate.project_item_id)
            if not self._is_blockable(current):
                return False
            self._gateway.set_status(current.project_item_id, self._config.blocked_option_id)
            final = self._gateway.get_story(candidate.project_item_id)
            return final.status_option_id == self._config.blocked_option_id

    def _is_managed(self, story: ProjectStory) -> bool:
        return (
            story.project_id == self._config.project_id and story.owner == self._config.owner
            and story.repository == self._config.repository and story.is_open and "adk:story" in story.labels
        )

    def _is_ready(self, story: ProjectStory) -> bool:
        return self._is_managed(story) and story.status_option_id == self._config.ready_option_id and story.primary_specialist is not None

    def _is_research_story(self, story: ProjectStory) -> bool:
        return self._is_managed(story) and story.primary_specialist == "Research"

    def _is_blockable(self, story: ProjectStory) -> bool:
        return self._is_research_story(story) and story.status_option_id == self._config.in_progress_option_id


def _claim_comment(dispatch: Dispatch, story: ProjectStory) -> str:
    event = {
        "event_id": _uuid7(), "dispatch_id": dispatch.dispatch_id, "kind": "dispatch.claimed",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "schema_version": 1,
        "payload": {"project_item_id": story.project_item_id, "status": "In Progress"},
    }
    return "<!-- adk-event:v1\n" + json.dumps(event, separators=(",", ":")) + "\n-->\n## Agent update · In Progress\n\nClaim recorded."


def _research_block_id(story: ProjectStory, block: ResearchBlock) -> str:
    return f"{story.dispatch_id or story.project_item_id}:{block.cause.value}"


def _research_block_comment(story: ProjectStory, block: ResearchBlock, block_id: str) -> str:
    reason = {
        ResearchBlockCause.ADMISSION_DENIED: "Research admission is missing or inactive.",
        ResearchBlockCause.RUNTIME_RETRY_EXHAUSTED: "Research runtime retry was exhausted.",
    }[block.cause]
    event = {
        "event_id": _uuid7(),
        "kind": "research.blocked",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "payload": {
            "project_item_id": story.project_item_id,
            "block_id": block_id,
            "cause": block.cause.value,
            "evidence_refs": list(block.evidence_refs),
        },
    }
    evidence = ", ".join(block.evidence_refs) if block.evidence_refs else "None available."
    return (
        "<!-- adk-event:v1\n"
        + json.dumps(event, separators=(",", ":"))
        + "\n-->\n## Agent update · Blocked\n\n"
        + f"Reason: {reason}\n\nEvidence: {evidence}"
    )
