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
