from __future__ import annotations

from datetime import datetime

from adk_agents.ready_workflows import (
    CodingSession,
    CommitAuthority,
    IncidentService,
    ReviewFinding,
    ReviewService,
    StoryHandoff,
)


def test_coding_session_blocks_out_of_scope_change_and_commit_authority_only_commits_verified_diff(tmp_path):
    session = CodingSession(tmp_path, approved_paths=("src",), approved_commands=("pytest",))
    assert session.propose_change(".github/workflows/ci.yml").blocked
    session.propose_change("src/feature.py")
    committed: list[tuple[str, ...]] = []
    authority = CommitAuthority(lambda paths: paths == ("src/feature.py",), lambda paths: committed.append(paths) or "abc123")

    assert authority.commit(session) == "abc123"
    assert committed == [("src/feature.py",)]


def test_review_requires_read_only_checkout_and_only_creates_pr_after_acceptance():
    created: list[dict[str, object]] = []
    service = ReviewService(lambda body: created.append(body) or "pr-7")

    rejected = service.review(read_only=False, findings=[])
    accepted = service.review(read_only=True, findings=[], story_ref="#16", branch="agent/16-safe", commits=("abc",), checks=("pytest",))

    assert rejected.blocked
    assert accepted.pr_ref == "pr-7"
    assert created[0]["base"] == "main"
    assert created[0]["approval"] is False


def test_review_blocks_after_two_corrections_and_reports_actionable_findings():
    service = ReviewService(lambda _body: "never")
    finding = ReviewFinding("high", "src/x.py", "tests fail", "fix test failure")

    assert service.review(read_only=True, findings=[finding]).needs_revision
    assert service.review(read_only=True, findings=[finding]).needs_revision
    assert service.review(read_only=True, findings=[finding]).blocked


def test_incident_service_deduplicates_persistent_failures_and_closes_only_after_24_hours():
    events: list[StoryHandoff] = []
    service = IncidentService(events.append)
    for _ in range(3):
        result = service.record_failure("backup", "redacted-digest")

    assert result == "incident:backup"
    assert len(events) == 1
    assert service.record_recovery("backup", healthy_hours=23) is None
    assert service.record_recovery("backup", healthy_hours=24) == "incident:backup"
