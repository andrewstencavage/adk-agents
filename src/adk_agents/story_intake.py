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


class IntakeState(str, Enum):
    """Persisted states for a Story intake before it becomes a Specialist story."""

    ASSESSING = "assessing"
    AWAITING_CONTINUATION = "awaiting_continuation"


@dataclass(frozen=True)
class IntakeOutcome:
    kind: IntakeOutcomeKind
    assessment: StoryAssessment | None = None
    intake_id: str | None = None


@dataclass(frozen=True)
class _RequestDetails:
    objective: str | None
    acceptance_criteria: tuple[str, ...]
    primary_specialist: str | None
    context: str | None
    constraints: str | None


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
                (comment.comment_id, digest, intake_id, IntakeState.ASSESSING.value),
            )
            return intake_id, False

    def _record_reply(self, comment_id: str, reply_id: str) -> None:
        with self._connect() as database:
            database.execute(
                "UPDATE story_intake SET state = ?, reply_id = ? WHERE comment_id = ?",
                (IntakeState.AWAITING_CONTINUATION.value, reply_id, comment_id),
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
        return "\n".join(lines[index + 1 :]).strip()
    return None


def _assess(source_request: str) -> StoryAssessment | None:
    details = _request_details(source_request)
    if details.objective is None:
        return None
    if not details.acceptance_criteria or details.primary_specialist is None:
        return None
    return StoryAssessment(
        title=details.objective.rstrip(".!?")[:120],
        objective=details.objective,
        acceptance_criteria=details.acceptance_criteria,
        primary_specialist=details.primary_specialist,
        canonical_body=_canonical_body(
            details.objective,
            details.acceptance_criteria,
            source_request,
            context=details.context,
            constraints=details.constraints,
        ),
    )


def _primary_specialist(objective: str) -> str | None:
    value = objective.lower()
    matches: set[str] = set()
    if any(word in value for word in ("research", "investigate", "find sources")):
        matches.add("Research")
    if any(word in value for word in ("review", "audit")):
        matches.add("Review")
    if any(word in value for word in ("plan", "prioritize", "backlog")):
        matches.add("Scrum Master")
    if any(word in value for word in ("add", "build", "create", "fix", "implement", "update")):
        matches.add("Coding")
    return next(iter(matches)) if len(matches) == 1 else None


def _is_testable_criterion(sentence: str) -> bool:
    if re.search(r"\b(good|better|correct|work|works|working)\b", sentence, re.IGNORECASE) is not None:
        return False
    if re.search(r"\b(must|should|ensure)\b", sentence, re.IGNORECASE) is not None:
        return True
    return (
        re.search(r"\b(that|with|including|which|where)\b", sentence, re.IGNORECASE) is not None
        and re.search(r"\b(export|include|preserve|display|create|return|save|send)\b", sentence, re.IGNORECASE) is not None
    )


def _named_section(source_request: str, name: str) -> str | None:
    match = re.search(rf"(?im)^{re.escape(name)}:\s*(.+)$", source_request)
    return None if match is None else match.group(1).strip()


def _request_details(source_request: str) -> _RequestDetails:
    context = _named_section(source_request, "Context")
    constraints = _named_section(source_request, "Constraints and dependencies")
    content_lines = tuple(
        line
        for line in source_request.splitlines()
        if not re.match(r"(?i)^\s*(Context|Constraints and dependencies):", line)
    )
    sentences = tuple(
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", "\n".join(content_lines))
        if sentence.strip()
    )
    objective = sentences[0] if sentences else None
    return _RequestDetails(
        objective=objective,
        acceptance_criteria=tuple(sentence for sentence in sentences if _is_testable_criterion(sentence)),
        primary_specialist=None if objective is None else _primary_specialist(objective),
        context=context,
        constraints=constraints,
    )


def _canonical_body(
    objective: str,
    criteria: tuple[str, ...],
    source_request: str,
    *,
    context: str | None,
    constraints: str | None,
) -> str:
    criteria_body = "\n".join(f"- {criterion}" for criterion in criteria)
    context_body = context or "None stated."
    constraints_body = constraints or "None stated."
    return (
        f"## Objective\n\n{objective}\n\n"
        f"## Context\n\n{context_body}\n\n"
        f"## Acceptance criteria\n\n{criteria_body}\n\n"
        f"## Constraints and dependencies\n\n{constraints_body}\n\n"
        f"## Source request\n\n{source_request}\n"
    )


def _clarification(intake_id: str, source_request: str) -> str:
    details = _request_details(source_request)
    if details.objective is None:
        question = "What outcome should this story achieve?"
    elif not details.acceptance_criteria:
        question = "What observable behavior will show that the story is complete?"
    elif details.primary_specialist is None:
        question = "Which Primary specialist should own this story?"
    else:
        question = "What outcome should this story achieve?"
    return (
        f"{question}\n\n"
        "Continue this intake with:\n\n"
        f"`/continue {intake_id}`\n"
        "<your answer>"
    )
