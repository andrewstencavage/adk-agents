from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adk_agents.ready_workflows import (
    CodingSession,
    BackupService,
    CommitAuthority,
    IncidentService,
    ReviewFinding,
    ReviewService,
    StoryHandoff,
    PythonCommandProfile,
    TransientCheckFailure,
)


def test_coding_session_blocks_out_of_scope_change_and_commit_authority_only_commits_verified_diff(tmp_path):
    session = CodingSession(tmp_path, approved_paths=("src",), approved_commands=("pytest",))
    assert session.propose_change(".github/workflows/ci.yml").blocked
    session.propose_change("src/feature.py")
    committed: list[tuple[str, ...]] = []
    authority = CommitAuthority(lambda paths: paths == ("src/feature.py",), lambda paths: committed.append(paths) or "abc123")

    assert authority.commit(session) == "abc123"
    assert committed == [("src/feature.py",)]


def test_python_command_profile_rejects_git_network_and_package_install_without_invoking_runner():
    calls: list[tuple[str, ...]] = []
    profile = PythonCommandProfile((("pytest",),), lambda argv: calls.append(argv) or "passed")

    assert profile.run(("pytest",)) == "passed"
    with pytest.raises(PermissionError):
        profile.run(("git", "status"))
    with pytest.raises(PermissionError):
        profile.run(("pip", "install", "x"))
    assert calls == [("pytest",)]


def test_coding_scope_expansion_requires_a_recorded_scrum_master_approval(tmp_path):
    session = CodingSession(tmp_path, approved_paths=("src",), approved_commands=("pytest",))

    with pytest.raises(PermissionError):
        session.approve_expansion(("docs",), (), "")
    session.approve_expansion(("docs",), (), "comment:123")
    assert not session.propose_change("docs/decision.md").blocked


def test_review_requires_read_only_checkout_and_only_creates_pr_after_acceptance():
    created: list[dict[str, object]] = []
    service = ReviewService(lambda body: created.append(body) or "pr-7")

    rejected = service.review(read_only=False, findings=[])
    accepted = service.review(read_only=True, findings=[], story_ref="#16", branch="agent/16-safe", commits=("abc",), checks=("pytest",), implementation_summary="safe change")

    assert rejected.blocked
    assert accepted.pr_ref == "pr-7"
    assert created[0]["base"] == "main"
    assert created[0]["approval"] is False
    assert created[0]["summary"] == "safe change"
    assert created[0]["revision_count"] == 0


def test_review_blocks_after_two_corrections_and_reports_actionable_findings():
    service = ReviewService(lambda _body: "never")
    finding = ReviewFinding("high", "src/x.py", "tests fail", "fix test failure")

    assert service.review(read_only=True, findings=[finding]).needs_revision
    assert service.review(read_only=True, findings=[finding]).needs_revision
    assert service.review(read_only=True, findings=[finding]).blocked


def test_review_retries_one_transient_check_failure_then_blocks_a_repeat():
    service = ReviewService(lambda _body: "never")
    attempts = 0

    def succeeds_on_retry():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransientCheckFailure()

    assert service.check(succeeds_on_retry).needs_revision is False
    assert service.check(lambda: (_ for _ in ()).throw(TransientCheckFailure())).blocked


def test_incident_service_deduplicates_persistent_failures_and_closes_only_after_24_hours():
    events: list[StoryHandoff] = []
    service = IncidentService(events.append)
    for _ in range(3):
        result = service.record_failure("backup", "redacted-digest")

    assert result == "incident:backup"
    assert len(events) == 1
    assert service.record_recovery("backup", healthy_hours=23) is None
    assert service.record_recovery("backup", healthy_hours=24) == "incident:backup"


def test_backup_service_uses_external_snapshot_and_isolated_monthly_restore():
    copied: list[str] = []
    verified: list[str] = []
    service = BackupService(copied.append, verified.append)

    assert service.daily_backup("record.sqlite3") == "backup:record.sqlite3"
    assert copied == ["record.sqlite3"]
    assert service.monthly_restore_verify("backup:record.sqlite3") is True
    assert verified == ["backup:record.sqlite3"]


def test_backup_retention_keeps_14_daily_and_12_monthly_sets():
    service = BackupService(lambda _path: None, lambda _ref: None)
    now = datetime.now(timezone.utc)
    for days in range(16):
        service.record_set(f"daily-{days}", "daily", now - timedelta(days=days))
    for months in range(14):
        service.record_set(f"monthly-{months}", "monthly", now - timedelta(days=31 * months))

    assert set(service.expired(now)) == {"daily-14", "daily-15", "monthly-12", "monthly-13"}
