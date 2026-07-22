from __future__ import annotations

from dataclasses import dataclass

from adk_agents.story_intake import (
    ControlComment,
    IntakeOutcomeKind,
    PublishedStory,
    StoryPublicationState,
    StoryIntakeService,
)


@dataclass
class FakeControlIssue:
    replies: list[tuple[str, str]]
    fail_next_reply: bool = False
    lose_next_reply_response: bool = False

    def reply(self, comment: ControlComment, body: str) -> str:
        if self.fail_next_reply:
            self.fail_next_reply = False
            raise RuntimeError("temporary reply failure")
        self.replies.append((comment.comment_id, body))
        if self.lose_next_reply_response:
            self.lose_next_reply_response = False
            raise RuntimeError("lost reply response")
        return f"reply-{len(self.replies)}"

    def find_reply(self, comment: ControlComment, event_id: str) -> str | None:
        return next(
            (f"reply-{index}" for index, (_, body) in enumerate(self.replies, start=1) if event_id in body),
            None,
        )


@dataclass
class FakeStoryBoard:
    created: list[tuple[str, str]]
    labels: list[tuple[int, str]]
    projects: list[int]
    backlog: list[str]
    specialists: list[str]
    stories_by_marker: dict[str, PublishedStory] | None = None
    lose_create_response: bool = False
    fail_next: str | None = None
    statuses: dict[int, str | None] | None = None
    story_labels: dict[int, set[str]] | None = None
    story_projects: set[int] | None = None
    story_specialists: dict[int, str | None] | None = None
    issue_create_calls: int = 0
    likely_duplicate: PublishedStory | None = None

    def create_issue(self, title: str, body: str) -> PublishedStory:
        self.issue_create_calls += 1
        marker = body.split("\n", 1)[0]
        story = PublishedStory(number=57, url="https://github.test/acme/adk-agents/issues/57", project_item_id="item-57")
        if self.stories_by_marker is not None:
            self.stories_by_marker[marker] = story
        if self.lose_create_response:
            self.lose_create_response = False
            raise RuntimeError("lost create response")
        self.created.append((title, body))
        return story

    def find_story(self, marker: str) -> PublishedStory | None:
        return None if self.stories_by_marker is None else self.stories_by_marker.get(marker)

    def find_likely_duplicate(self, assessment) -> PublishedStory | None:
        return self.likely_duplicate

    def add_label(self, story: PublishedStory, label: str) -> None:
        self.labels.append((story.number, label))
        if self.story_labels is None:
            self.story_labels = {}
        self.story_labels.setdefault(story.number, set()).add(label)
        self._fail_after("label")

    def add_to_project(self, story: PublishedStory) -> None:
        self.projects.append(story.number)
        if self.story_projects is None:
            self.story_projects = set()
        self.story_projects.add(story.number)
        self._fail_after("project")

    def set_backlog(self, story: PublishedStory) -> None:
        self.backlog.append(story.project_item_id)
        if self.statuses is None:
            self.statuses = {}
        self.statuses[story.number] = "Backlog"
        self._fail_after("backlog")

    def set_primary_specialist(self, story: PublishedStory, specialist: str) -> None:
        self.specialists.append(specialist)
        if self.story_specialists is None:
            self.story_specialists = {}
        self.story_specialists[story.number] = specialist
        self._fail_after("specialist")

    def publication_state(self, story: PublishedStory) -> StoryPublicationState:
        return StoryPublicationState(
            self.story_labels is not None and "adk:story" in self.story_labels.get(story.number, set()),
            self.story_projects is not None and story.number in self.story_projects,
            None if self.statuses is None else self.statuses.get(story.number),
            None if self.story_specialists is None else self.story_specialists.get(story.number),
        )

    def _fail_after(self, step: str) -> None:
        if self.fail_next == step:
            self.fail_next = None
            raise RuntimeError(f"lost {step} response")


def comment(comment_id: str, body: str) -> ControlComment:
    return ControlComment(comment_id=comment_id, author_login="andrew", body=body)


def test_ignores_an_ordinary_control_issue_comment(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)

    outcome = service.handle(comment("comment-1", "What is the service status?"))

    assert outcome.kind is IntakeOutcomeKind.IGNORED
    assert control_issue.replies == []


def test_publishes_a_complete_assessment_as_a_backlog_specialist_story(tmp_path):
    control_issue = FakeControlIssue([])
    board = FakeStoryBoard([], [], [], [], [])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board)
    request = "/create\nAdd CSV export. It must include visible headers."

    outcome = service.create(comment("comment-1", request))

    assert outcome.kind is IntakeOutcomeKind.STORY_CREATED
    assert board.created[0][0] == "Add CSV export"
    assert "## Source request\n\nAdd CSV export. It must include visible headers." in board.created[0][1]
    assert board.labels == [(57, "adk:story")]
    assert board.projects == [57]
    assert board.backlog == ["item-57"]
    assert board.specialists == ["Coding"]
    assert "https://github.test/acme/adk-agents/issues/57" in control_issue.replies[0][1]
    assert "move it to Ready to approve" in control_issue.replies[0][1]

    replay = service.create(comment("comment-1", request))

    assert replay.kind is IntakeOutcomeKind.STORY_CREATED
    assert len(board.created) == 1


def test_non_duplicate_request_creates_a_story_without_confirmation(tmp_path):
    control_issue = FakeControlIssue([])
    board = FakeStoryBoard([], [], [], [], [])

    outcome = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board).create(
        comment("comment-1", "/create\nAdd CSV export. It must include visible headers.")
    )

    assert outcome.kind is IntakeOutcomeKind.STORY_CREATED
    assert len(board.created) == 1


def test_likely_duplicate_requires_confirmation_before_creating_a_story(tmp_path):
    control_issue = FakeControlIssue([])
    existing = PublishedStory(41, "https://github.test/acme/adk-agents/issues/41", "item-41")
    board = FakeStoryBoard([], [], [], [], [], likely_duplicate=existing)
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board)

    outcome = service.create(comment("comment-1", "/create\nAdd CSV export. It must include visible headers."))

    assert outcome.kind is IntakeOutcomeKind.DUPLICATE_CONFIRMATION_REQUIRED
    assert board.created == []
    assert existing.url in control_issue.replies[0][1]
    assert f"/continue {outcome.intake_id}\nconfirm" in control_issue.replies[0][1]


def test_confirmed_likely_duplicate_creates_one_new_canonical_story(tmp_path):
    control_issue = FakeControlIssue([])
    board = FakeStoryBoard([], [], [], [], [], likely_duplicate=PublishedStory(41, "https://github.test/existing", "item-41"))
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board)
    pending = service.create(comment("comment-1", "/create\nAdd CSV export. It must include visible headers."))

    confirmed = service.create(comment("comment-2", f"/continue {pending.intake_id}\nconfirm"))

    assert confirmed.kind is IntakeOutcomeKind.STORY_CREATED
    assert len(board.created) == 1


def test_declined_likely_duplicate_leaves_the_existing_story_unchanged(tmp_path):
    control_issue = FakeControlIssue([])
    existing = PublishedStory(41, "https://github.test/existing", "item-41")
    board = FakeStoryBoard([], [], [], [], [], likely_duplicate=existing)
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board)
    pending = service.create(comment("comment-1", "/create\nAdd CSV export. It must include visible headers."))

    declined = service.create(comment("comment-2", f"/continue {pending.intake_id}\ndecline"))

    assert declined.kind is IntakeOutcomeKind.DUPLICATE_DECLINED
    assert board.created == []
    assert board.publication_state(existing) == StoryPublicationState(False, False, None, None)


def test_recovers_an_uncertain_issue_creation_from_its_intake_marker(tmp_path):
    control_issue = FakeControlIssue([])
    board = FakeStoryBoard([], [], [], [], [], {}, True)
    database = tmp_path / "record.sqlite3"
    service = StoryIntakeService(database, control_issue, board)
    request = "/create\nAdd CSV export. It must include visible headers."

    try:
        service.create(comment("comment-1", request))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the simulated lost response")
    recovered = StoryIntakeService(database, control_issue, board).create(comment("comment-1", request))

    assert recovered.kind is IntakeOutcomeKind.STORY_CREATED
    assert board.created == []


def test_does_not_retry_an_unreconciled_issue_create_after_restart(tmp_path):
    database = tmp_path / "record.sqlite3"
    board = FakeStoryBoard([], [], [], [], [], lose_create_response=True)
    request = "/create\nAdd CSV export. It must include visible headers."

    try:
        StoryIntakeService(database, FakeControlIssue([]), board).create(comment("comment-1", request))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected lost create response")
    try:
        StoryIntakeService(database, FakeControlIssue([]), board).create(comment("comment-1", request))
    except RuntimeError as error:
        assert "waiting for marker reconciliation" in str(error)
    else:
        raise AssertionError("expected uncertain creation to remain paused")

    assert board.issue_create_calls == 1


def test_recovers_every_partial_board_write_after_restart(tmp_path):
    request = "/create\nAdd CSV export. It must include visible headers."
    for step in ("label", "project", "backlog", "specialist"):
        database = tmp_path / f"{step}.sqlite3"
        board = FakeStoryBoard([], [], [], [], [], {}, False, step, {}, {}, set(), {})
        service = StoryIntakeService(database, FakeControlIssue([]), board)

        try:
            service.create(comment("comment-1", request))
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"expected lost {step} response")
        recovered = StoryIntakeService(database, FakeControlIssue([]), board).create(comment("comment-1", request))

        assert recovered.kind is IntakeOutcomeKind.STORY_CREATED
        assert board.publication_state(PublishedStory(57, "", "item-57")) == StoryPublicationState(True, True, "Backlog", "Coding")


def test_stops_recovery_when_user_moves_story_out_of_backlog(tmp_path):
    control_issue = FakeControlIssue([])
    board = FakeStoryBoard([], [], [], [], [], {}, False, "specialist", {}, {}, set(), {})
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board)
    request = "/create\nAdd CSV export. It must include visible headers."

    try:
        service.create(comment("comment-1", request))
    except RuntimeError:
        pass
    board.statuses[57] = "Ready"
    outcome = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board).create(comment("comment-1", request))

    assert outcome.kind is IntakeOutcomeKind.CONFLICT
    assert board.specialists == ["Coding"]
    assert "changed its status to Ready" in control_issue.replies[-1][1]


def test_replaying_a_completed_intake_preserves_a_later_user_status_change(tmp_path):
    control_issue = FakeControlIssue([])
    board = FakeStoryBoard([], [], [], [], [])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue, board)
    request = "/create\nAdd CSV export. It must include visible headers."

    service.create(comment("comment-1", request))
    board.statuses[57] = "Ready"
    replay = service.create(comment("comment-1", request))

    assert replay.kind is IntakeOutcomeKind.STORY_CREATED
    assert len(control_issue.replies) == 1


def test_recovers_a_lost_confirmation_response_without_a_second_reply(tmp_path):
    database = tmp_path / "record.sqlite3"
    control_issue = FakeControlIssue([], lose_next_reply_response=True)
    board = FakeStoryBoard([], [], [], [], [])
    service = StoryIntakeService(database, control_issue, board)
    request = "/create\nAdd CSV export. It must include visible headers."

    try:
        service.create(comment("comment-1", request))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected lost confirmation response")
    recovered = StoryIntakeService(database, control_issue, board).create(comment("comment-1", request))

    assert recovered.kind is IntakeOutcomeKind.STORY_CREATED
    assert len(control_issue.replies) == 1


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
    assert replayed.kind is IntakeOutcomeKind.ASSESSED
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


def test_replaying_a_completed_continuation_after_restart_returns_the_durable_assessment(tmp_path):
    database = tmp_path / "record.sqlite3"
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(database, control_issue)
    pending = service.handle(comment("comment-1", "/create\nAdd CSV export."))
    continuation = comment("comment-2", f"/continue {pending.intake_id}\nIt must include visible headers.")

    first = service.handle(continuation)
    replay = StoryIntakeService(database, control_issue).handle(continuation)

    assert first.kind is IntakeOutcomeKind.ASSESSED
    assert replay.kind is IntakeOutcomeKind.ASSESSED
    assert replay.assessment == first.assessment


def test_replays_a_pending_clarification_after_reply_failure_without_duplicate_reply(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    pending = service.handle(comment("comment-1", "/create\nResearch and implement CSV export."))
    continuation = comment("comment-2", f"/continue {pending.intake_id}\nIt must include visible headers.")
    control_issue.fail_next_reply = True

    try:
        service.handle(continuation)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the simulated reply failure")
    recovered = service.handle(continuation)
    replay = service.handle(continuation)

    assert recovered.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert replay.kind is IntakeOutcomeKind.NEEDS_CLARIFICATION
    assert len(control_issue.replies) == 2


def test_rejects_a_replayed_comment_with_a_different_intake_or_answer(tmp_path):
    control_issue = FakeControlIssue([])
    service = StoryIntakeService(tmp_path / "record.sqlite3", control_issue)
    first = service.handle(comment("comment-1", "/create\nAdd CSV export."))
    second = service.handle(comment("comment-2", "/create\nAdd PDF export."))
    continuation = comment("comment-3", f"/continue {first.intake_id}\nIt must include visible headers.")

    accepted = service.handle(continuation)
    changed_target = service.handle(comment("comment-3", f"/continue {second.intake_id}\nIt must include visible headers."))
    changed_answer = service.handle(comment("comment-3", f"/continue {first.intake_id}\nIt must export filtered rows."))

    assert accepted.kind is IntakeOutcomeKind.ASSESSED
    assert changed_target.kind is IntakeOutcomeKind.REJECTED
    assert changed_answer.kind is IntakeOutcomeKind.REJECTED
