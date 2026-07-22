"""Control-issue admission for free-form Specialist story requests."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .story_intake_store import StoryIntakeStore, StoredStory


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


@dataclass(frozen=True)
class StoryPublicationState:
    """The board state that may have survived an interrupted publish call."""

    has_story_label: bool
    is_on_project: bool
    status: str | None
    primary_specialist: str | None


class StoryBoardGateway(Protocol):
    """The narrow publishing capability for a complete Specialist story."""

    def create_issue(self, title: str, body: str) -> PublishedStory: ...

    def find_story(self, marker: str) -> PublishedStory | None: ...

    def publication_state(self, story: PublishedStory) -> StoryPublicationState: ...

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
    CONFLICT = "conflict"


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
        self._store = StoryIntakeStore(database_path)
        self._control_issue = control_issue
        self._story_board = story_board

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
        intake = self._intake(outcome.intake_id)
        if intake.publication_complete:
            return IntakeOutcome(IntakeOutcomeKind.STORY_CREATED, assessment=outcome.assessment, intake_id=outcome.intake_id)
        conflict_status = intake.publication_conflict_status
        if conflict_status is not None:
            return self._record_conflict(comment, outcome, conflict_status)
        marker = f"<!-- adk-intake:v1 {outcome.intake_id} -->"
        story = self._published_story(intake) or self._story_board.find_story(marker)
        if story is None:
            if intake.issue_create_attempted:
                raise RuntimeError("Story creation response is uncertain; waiting for marker reconciliation")
            self._store.record_issue_create_attempt(self._required_intake_id(outcome.intake_id, "created"))
            story = self._story_board.create_issue(
                outcome.assessment.title, f"{marker}\n{outcome.assessment.canonical_body}"
            )
        self._store.record_story(self._required_intake_id(outcome.intake_id, "published"), self._stored(story))
        publication = self._story_board.publication_state(story)
        if publication.status not in (None, "Backlog"):
            return self._record_conflict(comment, outcome, publication.status)
        if not publication.has_story_label:
            self._story_board.add_label(story, "adk:story")
        if not publication.is_on_project:
            self._story_board.add_to_project(story)
        publication = self._story_board.publication_state(story)
        if publication.status not in (None, "Backlog"):
            return self._record_conflict(comment, outcome, publication.status)
        if publication.status != "Backlog":
            self._story_board.set_backlog(story)
        publication = self._story_board.publication_state(story)
        if publication.status != "Backlog":
            return self._record_conflict(comment, outcome, publication.status or "an unknown status")
        if publication.primary_specialist != outcome.assessment.primary_specialist:
            self._story_board.set_primary_specialist(story, outcome.assessment.primary_specialist)
        confirmation = (
            f"Created {story.url} with proposed Primary specialist {outcome.assessment.primary_specialist}. "
            "It is in Backlog; move it to Ready to approve and dispatch it."
        )
        self._reply_once(comment, outcome.intake_id or comment.comment_id, confirmation)
        self._store.record_published(self._required_intake_id(outcome.intake_id, "published"), self._stored(story))
        return IntakeOutcome(IntakeOutcomeKind.STORY_CREATED, assessment=outcome.assessment, intake_id=outcome.intake_id)

    def _record_conflict(
        self, comment: ControlComment, outcome: IntakeOutcome, status: str
    ) -> IntakeOutcome:
        self._store.record_conflict(self._required_intake_id(outcome.intake_id, "conflicted"), status)
        self._reply_once(
            comment,
            outcome.intake_id or comment.comment_id,
            f"Story intake stopped because a user changed its status to {status}. No stale board updates were applied.",
        )
        return IntakeOutcome(IntakeOutcomeKind.CONFLICT, assessment=outcome.assessment, intake_id=outcome.intake_id)

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
        continuation = self._store.continuation_for_comment(comment.comment_id)
        intake = self._store.intake(intake_id)
        if intake is None:
            return IntakeOutcome(IntakeOutcomeKind.REJECTED)
        if continuation is not None and (continuation.intake_id != intake_id or continuation.answer != answer):
            return IntakeOutcome(IntakeOutcomeKind.REJECTED)
        if continuation is not None and intake.state == IntakeState.ASSESSMENT_READY.value:
            assessment = _stored_assessment(intake.assessment_json)
            return IntakeOutcome(IntakeOutcomeKind.ASSESSED, assessment=assessment, intake_id=intake_id)
        if intake.state != IntakeState.AWAITING_CONTINUATION.value:
            return IntakeOutcome(IntakeOutcomeKind.REJECTED)
        if continuation is None:
            self._store.record_continuation(comment.comment_id, intake_id, answer)
        source_request = "\n\n".join((intake.source_request, *self._store.continuation_answers(intake_id)))
        assessment = _assess(source_request)
        if assessment is not None:
            self._record_assessment(intake_id, assessment)
            return IntakeOutcome(IntakeOutcomeKind.ASSESSED, assessment=assessment, intake_id=intake_id)

        reply_id = self._reply_once(comment, intake_id, _clarification(intake_id, source_request))
        self._store.record_continuation_reply(comment.comment_id, reply_id)
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
        intake = self._store.begin(comment_id=comment.comment_id, source_digest=digest, source_request=source_request,
                                  intake_id=f"intake-{secrets.token_hex(4)}", state=IntakeState.ASSESSING.value)
        if intake.source_digest != digest:
            raise ValueError("Control comment body changed after Story intake began")
        return intake.intake_id, intake.reply_id is not None

    def _record_reply(self, comment_id: str, reply_id: str) -> None:
        self._store.record_reply(comment_id, IntakeState.AWAITING_CONTINUATION.value, reply_id)

    def _record_assessment(self, intake_id: str, assessment: StoryAssessment) -> None:
        self._store.record_assessment(intake_id, IntakeState.ASSESSMENT_READY.value, _serialize_assessment(assessment))

    def _intake(self, intake_id: str | None):
        if intake_id is None:
            raise RuntimeError("Story intake outcome must have an intake ID")
        intake = self._store.intake(intake_id)
        if intake is None:
            raise RuntimeError("Story intake record does not exist")
        return intake

    @staticmethod
    def _required_intake_id(intake_id: str | None, action: str) -> str:
        if intake_id is None:
            raise RuntimeError(f"{action} Story intake must have an intake ID")
        return intake_id

    @staticmethod
    def _stored(story: PublishedStory) -> StoredStory:
        return StoredStory(story.number, story.url, story.project_item_id)

    @staticmethod
    def _published_story(intake) -> PublishedStory | None:
        if intake.published_story_number is None:
            return None
        return PublishedStory(intake.published_story_number, intake.published_story_url or "", intake.published_project_item_id or "")


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
