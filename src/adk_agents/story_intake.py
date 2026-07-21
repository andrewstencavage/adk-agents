"""Control-issue admission for free-form Specialist story requests."""

from __future__ import annotations

import hashlib
import json
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

    def find_reply(self, comment: ControlComment, event_id: str) -> str | None: ...


@dataclass(frozen=True)
class PublishedStory:
    number: int
    url: str
    project_item_id: str


class StoryBoardGateway(Protocol):
    """The narrow publishing capability for a complete Specialist story."""

    def create_issue(self, title: str, body: str) -> PublishedStory: ...

    def find_story(self, marker: str) -> PublishedStory | None: ...

    def add_label(self, story: PublishedStory, label: str) -> None: ...

    def add_to_project(self, story: PublishedStory) -> None: ...

    def set_backlog(self, story: PublishedStory) -> None: ...

    def set_primary_specialist(self, story: PublishedStory, specialist: str) -> None: ...


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
    REJECTED = "rejected"
    STORY_CREATED = "story_created"


class IntakeState(str, Enum):
    """Persisted states for a Story intake before it becomes a Specialist story."""

    ASSESSING = "assessing"
    AWAITING_CONTINUATION = "awaiting_continuation"
    ASSESSMENT_READY = "assessment_ready"


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

    def __init__(
        self,
        database_path: str | Path,
        control_issue: ControlIssueGateway,
        story_board: StoryBoardGateway | None = None,
    ) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._control_issue = control_issue
        self._story_board = story_board
        with self._connect() as database:
            database.executescript(
                """CREATE TABLE IF NOT EXISTS story_intake (
                    comment_id TEXT PRIMARY KEY,
                    source_digest TEXT NOT NULL,
                    source_request TEXT NOT NULL DEFAULT '',
                    intake_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reply_id TEXT,
                    assessment_json TEXT,
                    published_story_number INTEGER
                );
                CREATE TABLE IF NOT EXISTS story_intake_continuation (
                    comment_id TEXT PRIMARY KEY,
                    intake_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    reply_id TEXT
                )"""
            )
            self._add_source_request_column(database)
            self._add_column(database, "story_intake", "assessment_json TEXT")
            self._add_column(database, "story_intake", "published_story_number INTEGER")
            self._add_column(database, "story_intake_continuation", "reply_id TEXT")

    def handle(self, comment: ControlComment) -> IntakeOutcome:
        source_request = _source_request(comment.body)
        if source_request is not None:
            return self._handle_create(comment, source_request)
        continuation = _continuation_request(comment.body)
        if continuation is not None:
            return self._handle_continuation(comment, *continuation)
        if _first_nonblank_line(comment.body).strip().startswith("/continue"):
            return IntakeOutcome(IntakeOutcomeKind.REJECTED)
        return IntakeOutcome(IntakeOutcomeKind.IGNORED)

    def create(self, comment: ControlComment) -> IntakeOutcome:
        """Publish one complete assessed intake as a Backlog Specialist story."""

        outcome = self.handle(comment)
        if outcome.kind is not IntakeOutcomeKind.ASSESSED or outcome.assessment is None:
            return outcome
        if self._story_board is None:
            raise RuntimeError("Story intake publishing requires a task-board gateway")
        if self._published(outcome.intake_id):
            return IntakeOutcome(IntakeOutcomeKind.STORY_CREATED, assessment=outcome.assessment, intake_id=outcome.intake_id)
        marker = f"<!-- adk-intake:v1 {outcome.intake_id} -->"
        story = self._story_board.find_story(marker)
        if story is None:
            story = self._story_board.create_issue(
                outcome.assessment.title, f"{marker}\n{outcome.assessment.canonical_body}"
            )
        self._story_board.add_label(story, "adk:story")
        self._story_board.add_to_project(story)
        self._story_board.set_backlog(story)
        self._story_board.set_primary_specialist(story, outcome.assessment.primary_specialist)
        confirmation = (
            f"Created {story.url} with proposed Primary specialist {outcome.assessment.primary_specialist}. "
            "It is in Backlog; move it to Ready to approve and dispatch it."
        )
        self._reply_once(comment, outcome.intake_id or comment.comment_id, confirmation)
        self._record_published(outcome.intake_id, story)
        return IntakeOutcome(IntakeOutcomeKind.STORY_CREATED, assessment=outcome.assessment, intake_id=outcome.intake_id)

    def _handle_create(self, comment: ControlComment, source_request: str) -> IntakeOutcome:
        intake_id, reply_sent = self._begin(comment, source_request)
        assessment = _assess(source_request)
        if assessment is not None:
            self._record_assessment(intake_id, assessment)
            return IntakeOutcome(IntakeOutcomeKind.ASSESSED, assessment=assessment, intake_id=intake_id)

        if not reply_sent:
            reply_id = self._reply_once(comment, intake_id, _clarification(intake_id, source_request))
            self._record_reply(comment.comment_id, reply_id)
        return IntakeOutcome(IntakeOutcomeKind.NEEDS_CLARIFICATION, intake_id=intake_id)

    def _handle_continuation(self, comment: ControlComment, intake_id: str, answer: str) -> IntakeOutcome:
        with self._connect() as database:
            continuation = database.execute(
                "SELECT intake_id, answer, reply_id FROM story_intake_continuation WHERE comment_id = ?", (comment.comment_id,)
            ).fetchone()
            intake = database.execute(
                "SELECT source_request, state, assessment_json FROM story_intake WHERE intake_id = ?", (intake_id,)
            ).fetchone()
            if intake is None:
                return IntakeOutcome(IntakeOutcomeKind.REJECTED)
            if continuation is not None and (
                continuation["intake_id"] != intake_id or continuation["answer"] != answer
            ):
                return IntakeOutcome(IntakeOutcomeKind.REJECTED)
            if continuation is not None and intake["state"] == IntakeState.ASSESSMENT_READY.value:
                assessment = _stored_assessment(intake["assessment_json"])
                return IntakeOutcome(IntakeOutcomeKind.ASSESSED, assessment=assessment, intake_id=intake_id)
            if intake["state"] != IntakeState.AWAITING_CONTINUATION.value:
                return IntakeOutcome(IntakeOutcomeKind.REJECTED)
            if continuation is None:
                database.execute(
                    "INSERT INTO story_intake_continuation(comment_id, intake_id, answer) VALUES (?, ?, ?)",
                    (comment.comment_id, intake_id, answer),
                )
            answers = tuple(
                row["answer"]
                for row in database.execute(
                    "SELECT answer FROM story_intake_continuation WHERE intake_id = ? ORDER BY rowid", (intake_id,)
                )
            )
        source_request = "\n\n".join((intake["source_request"], *answers))
        assessment = _assess(source_request)
        if assessment is not None:
            self._record_assessment(intake_id, assessment)
            return IntakeOutcome(IntakeOutcomeKind.ASSESSED, assessment=assessment, intake_id=intake_id)

        reply_id = self._reply_once(comment, intake_id, _clarification(intake_id, source_request))
        with self._connect() as database:
            database.execute(
                "UPDATE story_intake_continuation SET reply_id = ? WHERE comment_id = ?", (reply_id, comment.comment_id)
            )
        return IntakeOutcome(IntakeOutcomeKind.NEEDS_CLARIFICATION, intake_id=intake_id)

    def _reply_once(self, comment: ControlComment, intake_id: str, message: str) -> str:
        event_id = f"{intake_id}:{comment.comment_id}"
        existing = self._control_issue.find_reply(comment, event_id)
        if existing is not None:
            return existing
        body = f"<!-- adk-intake-event:v1 {event_id} -->\n{message}"
        return self._control_issue.reply(comment, body)

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
                "INSERT INTO story_intake(comment_id, source_digest, source_request, intake_id, state) VALUES (?, ?, ?, ?, ?)",
                (comment.comment_id, digest, source_request, intake_id, IntakeState.ASSESSING.value),
            )
            return intake_id, False

    def _record_reply(self, comment_id: str, reply_id: str) -> None:
        with self._connect() as database:
            database.execute(
                "UPDATE story_intake SET state = ?, reply_id = ? WHERE comment_id = ?",
                (IntakeState.AWAITING_CONTINUATION.value, reply_id, comment_id),
            )

    def _record_assessment(self, intake_id: str, assessment: StoryAssessment) -> None:
        with self._connect() as database:
            database.execute(
                "UPDATE story_intake SET state = ?, assessment_json = ? WHERE intake_id = ?",
                (IntakeState.ASSESSMENT_READY.value, _serialize_assessment(assessment), intake_id),
            )

    def _published(self, intake_id: str | None) -> bool:
        if intake_id is None:
            return False
        with self._connect() as database:
            row = database.execute(
                "SELECT published_story_number FROM story_intake WHERE intake_id = ?", (intake_id,)
            ).fetchone()
        return row is not None and row["published_story_number"] is not None

    def _record_published(self, intake_id: str | None, story: PublishedStory) -> None:
        if intake_id is None:
            raise RuntimeError("published Story intake must have an intake ID")
        with self._connect() as database:
            database.execute(
                "UPDATE story_intake SET published_story_number = ? WHERE intake_id = ?", (story.number, intake_id)
            )

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        return database

    @classmethod
    def _add_source_request_column(cls, database: sqlite3.Connection) -> None:
        cls._add_column(database, "story_intake", "source_request TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _add_column(database: sqlite3.Connection, table: str, definition: str) -> None:
        name = definition.split()[0]
        columns = {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            database.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _source_request(body: str) -> str | None:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip():
            return "\n".join(lines[index + 1 :]).strip() if line.strip() == "/create" else None
    return None


def _continuation_request(body: str) -> tuple[str, str] | None:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        match = re.fullmatch(r"/continue\s+(intake-[a-z0-9]+)", line.strip())
        if match is None:
            return None
        answer = "\n".join(lines[index + 1 :]).strip()
        return (match.group(1), answer) if answer else None
    return None


def _first_nonblank_line(body: str) -> str:
    return next((line for line in body.splitlines() if line.strip()), "")


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
    declared_specialist = _named_section(source_request, "Primary specialist")
    content_lines = tuple(
        line
        for line in source_request.splitlines()
        if not re.match(r"(?i)^\s*(Context|Constraints and dependencies|Primary specialist):", line)
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
        primary_specialist=_declared_specialist(declared_specialist)
        if declared_specialist is not None
        else None if objective is None else _primary_specialist(objective),
        context=context,
        constraints=constraints,
    )


def _declared_specialist(value: str | None) -> str | None:
    if value is None:
        return None
    options = {"coding": "Coding", "research": "Research", "review": "Review", "scrum master": "Scrum Master"}
    return options.get(value.strip().lower())


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


def _serialize_assessment(assessment: StoryAssessment) -> str:
    return json.dumps(
        {
            "title": assessment.title,
            "objective": assessment.objective,
            "acceptance_criteria": assessment.acceptance_criteria,
            "primary_specialist": assessment.primary_specialist,
            "canonical_body": assessment.canonical_body,
        },
        separators=(",", ":"),
    )


def _stored_assessment(value: str | None) -> StoryAssessment:
    if value is None:
        raise RuntimeError("ready Story intake is missing its durable assessment")
    data = json.loads(value)
    return StoryAssessment(
        title=data["title"],
        objective=data["objective"],
        acceptance_criteria=tuple(data["acceptance_criteria"]),
        primary_specialist=data["primary_specialist"],
        canonical_body=data["canonical_body"],
    )
