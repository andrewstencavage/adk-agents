from __future__ import annotations

from dataclasses import dataclass

from adk_agents.story_intake import (
    ControlComment,
    IntakeOutcomeKind,
    StoryIntakeService,
)


@dataclass
class FakeControlIssue:
    replies: list[tuple[str, str]]

    def reply(self, comment: ControlComment, body: str) -> str:
        self.replies.append((comment.comment_id, body))
        return f"reply-{len(self.replies)}"


def comment(comment_id: str, body: str) -> ControlComment:
    return ControlComment(comment_id=comment_id, author_login="andrew", body=body)


def test_ignores_an_ordinary_control_issue_comment(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)

    outcome = service.handle(comment("comment-1", "What is the service status?"))

    assert outcome.kind is IntakeOutcomeKind.IGNORED
    assert control_issue.replies == []


def test_assesses_a_complete_create_request_into_canonical_story_content(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    request = """/create
Add CSV export to the reporting screen. The CSV must export the currently filtered rows with visible headers."""

    outcome = service.handle(comment("comment-1", request))

    assert outcome.kind is IntakeOutcomeKind.ASSESSED
    assert outcome.assessment is not None
    assert outcome.assessment.title == "Add CSV export to the reporting screen"
    assert outcome.assessment.primary_specialist == "Coding"
    assert "## Objective\n\nAdd CSV export to the reporting screen." in outcome.assessment.canonical_body
    assert "- The CSV must export the currently filtered rows with visible headers." in outcome.assessment.canonical_body
    assert "## Source request\n\nAdd CSV export to the reporting screen. The CSV must export the currently filtered rows with visible headers." in outcome.assessment.canonical_body
    assert control_issue.replies == []


def test_asks_one_focused_question_once_for_an_incomplete_create_request(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    request = "/create\nAdd CSV export to the reporting screen."

    first = service.handle(comment("comment-1", request))
    replay = service.handle(comment("comment-1", request))

    assert first.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert replay.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert len(control_issue.replies) == 1
    assert "What observable behavior will show that the story is complete?" in control_issue.replies[0][1]
    assert "intake-" in control_issue.replies[0][1]


def test_asks_for_an_objective_when_create_has_no_source_request(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)

    outcome = service.handle(comment("comment-1", "/create"))

    assert outcome.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert "What outcome should this story achieve?" in control_issue.replies[0][1]


def test_asks_for_a_testable_criterion_when_request_only_says_it_must_be_good(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)

    outcome = service.handle(comment("comment-1", "/create\nAdd CSV export. It must be good."))

    assert outcome.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert "What observable behavior will show that the story is complete?" in control_issue.replies[0][1]


def test_asks_for_a_specialist_when_routing_is_ambiguous(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    request = "/create\nResearch and implement an export format. It must include visible headers."

    outcome = service.handle(comment("comment-1", request))

    assert outcome.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert "Which Primary specialist should own this story?" in control_issue.replies[0][1]


def test_renders_stated_context_and_constraints_instead_of_none_stated(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    request = """/create
Add CSV export to the reporting screen. The CSV must export visible headers.
Context: Operators use the reporting screen during monthly close.
Constraints and dependencies: Preserve the current filters."""

    outcome = service.handle(comment("comment-1", request))

    assert outcome.kind is IntakeOutcomeKind.ASSESSED
    assert outcome.assessment is not None
    assert "## Context\n\nOperators use the reporting screen during monthly close." in outcome.assessment.canonical_body
    assert "## Constraints and dependencies\n\nPreserve the current filters." in outcome.assessment.canonical_body


def test_accepts_regular_prose_with_an_observable_completion_condition(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    request = "/create\nAdd CSV export that exports currently filtered rows with visible headers."

    outcome = service.handle(comment("comment-1", request))

    assert outcome.kind is IntakeOutcomeKind.ASSESSED
    assert outcome.assessment is not None
    assert "- Add CSV export that exports currently filtered rows with visible headers." in outcome.assessment.canonical_body


def test_does_not_treat_a_declared_constraint_as_an_acceptance_criterion(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    request = """/create
Add CSV export. The CSV must include visible headers.
Constraints and dependencies: It must preserve current filters."""

    outcome = service.handle(comment("comment-1", request))

    assert outcome.kind is IntakeOutcomeKind.ASSESSED
    assert outcome.assessment is not None
    assert "- The CSV must include visible headers." in outcome.assessment.canonical_body
    assert "- Constraints and dependencies: It must preserve current filters." not in outcome.assessment.canonical_body


def test_continuation_combines_with_pending_source_request_to_complete_assessment(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    pending = service.handle(comment("comment-1", "/create\nAdd CSV export to the reporting screen."))

    outcome = service.handle(
        comment("comment-2", f"/continue {pending.intake_id}\nIt must export currently filtered rows with visible headers.")
    )

    assert outcome.kind is IntakeOutcomeKind.ASSESSED
    assert outcome.assessment is not None
    assert "Add CSV export to the reporting screen.\n\nIt must export currently filtered rows with visible headers." in outcome.assessment.canonical_body
    assert len(control_issue.replies) == 1


def test_incomplete_continuation_posts_only_its_next_focused_question(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    pending = service.handle(comment("comment-1", "/create\nResearch and implement CSV export."))

    outcome = service.handle(comment("comment-2", f"/continue {pending.intake_id}\nIt must include visible headers."))

    assert outcome.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert len(control_issue.replies) == 2
    assert "Which Primary specialist should own this story?" in control_issue.replies[-1][1]


def test_rejects_unknown_malformed_replayed_and_closed_continuations_without_replying(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    pending = service.handle(comment("comment-1", "/create\nAdd CSV export."))

    unknown = service.handle(comment("comment-2", "/continue intake-unknown\nIt must include headers."))
    malformed = service.handle(comment("comment-3", "/continue\nIt must include headers."))
    complete = service.handle(comment("comment-4", f"/continue {pending.intake_id}\nIt must include headers."))
    replayed = service.handle(comment("comment-4", f"/continue {pending.intake_id}\nIt must include headers."))
    closed = service.handle(comment("comment-5", f"/continue {pending.intake_id}\nIt must export filtered rows."))

    assert unknown.kind is IntakeOutcomeKind.REJECTED
    assert malformed.kind is IntakeOutcomeKind.REJECTED
    assert complete.kind is IntakeOutcomeKind.ASSESSED
    assert replayed.kind is IntakeOutcomeKind.REJECTED
    assert closed.kind is IntakeOutcomeKind.REJECTED
    assert len(control_issue.replies) == 1


def test_continuation_survives_restart_and_can_resolve_the_next_focused_question(tmp_path):
    database = tmp_path / "record.sqlite3"
    first_control_issue = FakeControlIssue([])
    first_service = StoryIntakeService(database, first_control_issue)
    pending = first_service.handle(comment("comment-1", "/create\nResearch and implement CSV export."))

    second_control_issue = FakeControlIssue([])
    restarted_service = StoryIntakeService(database, second_control_issue)
    still_ambiguous = restarted_service.handle(
        comment("comment-2", f"/continue {pending.intake_id}\nIt must include visible headers.")
    )
    complete = restarted_service.handle(
        comment("comment-3", f"/continue {pending.intake_id}\nPrimary specialist: Coding")
    )

    assert still_ambiguous.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert complete.kind is IntakeOutcomeKind.ASSESSED
    assert complete.assessment is not None
    assert complete.assessment.primary_specialist == "Coding"
