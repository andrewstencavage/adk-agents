"""Control-issue admission for free-form Specialist story requests."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ControlComment:
    """The immutable identity and body of a Control-issue comment."""

    comment_id: str
    author_login: str
    body: str


class ControlIssueGateway(Protocol):
    """The narrow Control-issue reply capability needed during admission."""

    def reply(self, comment: ControlComment, body: str) -> str: ...


@dataclass(frozen=True)
class StoryAssessment:
    """A complete, pre-creation Specialist story proposal."""

    title: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    primary_specialist: str
    canonical_body: str


class IntakeOutcomeKind(str, Enum):
    IGNORED = "ignored"
    ASSESSED = "assessed"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True)
class IntakeOutcome:
    kind: IntakeOutcomeKind
    assessment: StoryAssessment | None = None
    intake_id: str | None = None


class StoryIntakeService:
    """Admits `/create` Control comments without creating task-board work."""

    def __init__(self, database_path: str | Path, control_issue: ControlIssueGateway) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._control_issue = control_issue
        with self._connect() as database:
            database.execute(
                """CREATE TABLE IF NOT EXISTS story_intake (
                    comment_id TEXT PRIMARY KEY,
                    source_digest TEXT NOT NULL,
                    intake_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reply_id TEXT
                )"""
            )

    def handle(self, comment: ControlComment) -> IntakeOutcome:
        source_request = _source_request(comment.body)
        if source_request is None:
            return IntakeOutcome(IntakeOutcomeKind.IGNORED)

        intake_id, reply_sent = self._begin(comment, source_request)
        assessment = _assess(source_request)
        if assessment is not None:
            return IntakeOutcome(IntakeOutcomeKind.ASSESSED, assessment=assessment, intake_id=intake_id)

        if not reply_sent:
            reply_id = self._control_issue.reply(comment, _clarification(intake_id, source_request))
            self._record_reply(comment.comment_id, reply_id)
        return IntakeOutcome(IntakeOutcomeKind.NEEDS_CLARIFICATION, intake_id=intake_id)

    def _begin(self, comment: ControlComment, source_request: str) -> tuple[str, bool]:
        digest = hashlib.sha256(source_request.encode()).hexdigest()
        with self._connect() as database:
            row = database.execute(
                "SELECT source_digest, intake_id, reply_id FROM story_intake WHERE comment_id = ?",
                (comment.comment_id,),
            ).fetchone()
            if row is not None:
                if row["source_digest"] != digest:
                    raise ValueError("Control comment body changed after Story intake began")
                return row["intake_id"], row["reply_id"] is not None
            intake_id = f"intake-{secrets.token_hex(4)}"
            database.execute(
                "INSERT INTO story_intake(comment_id, source_digest, intake_id, state) VALUES (?, ?, ?, ?)",
                (comment.comment_id, digest, intake_id, "assessing"),
            )
            return intake_id, False

    def _record_reply(self, comment_id: str, reply_id: str) -> None:
        with self._connect() as database:
            database.execute(
                "UPDATE story_intake SET state = ?, reply_id = ? WHERE comment_id = ?",
                ("awaiting_continuation", reply_id, comment_id),
            )

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        return database


def _source_request(body: str) -> str | None:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.strip() != "/create":
            return None
        request = "\n".join(lines[index + 1 :]).strip()
        return request or None
    return None


def _assess(source_request: str) -> StoryAssessment | None:
    sentences = tuple(sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", source_request) if sentence.strip())
    if not sentences:
        return None
    objective = sentences[0]
    criteria = tuple(sentence for sentence in sentences if re.search(r"\b(must|should|ensure)\b", sentence, re.IGNORECASE))
    specialist = _primary_specialist(objective)
    if not criteria or specialist is None:
        return None
    return StoryAssessment(
        title=objective.rstrip(".!?")[:120],
        objective=objective,
        acceptance_criteria=criteria,
        primary_specialist=specialist,
        canonical_body=_canonical_body(objective, criteria, source_request),
    )


def _primary_specialist(objective: str) -> str | None:
    value = objective.lower()
    if any(word in value for word in ("research", "investigate", "find sources")):
        return "Research"
    if any(word in value for word in ("review", "audit")):
        return "Review"
    if any(word in value for word in ("plan", "prioritize", "backlog")):
        return "Scrum Master"
    if any(word in value for word in ("add", "build", "create", "fix", "implement", "update")):
        return "Coding"
    return None


def _canonical_body(objective: str, criteria: tuple[str, ...], source_request: str) -> str:
    criteria_body = "\n".join(f"- {criterion}" for criterion in criteria)
    return (
        f"## Objective\n\n{objective}\n\n"
        "## Context\n\nNone stated.\n\n"
        f"## Acceptance criteria\n\n{criteria_body}\n\n"
        "## Constraints and dependencies\n\nNone stated.\n\n"
        f"## Source request\n\n{source_request}\n"
    )


def _clarification(intake_id: str, source_request: str) -> str:
    del source_request
    return (
        "What observable behavior will show that the story is complete?\n\n"
        "Continue this intake with:\n\n"
        f"`/continue {intake_id}`\n"
        "<your answer>"
    )
