from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from adk_agents.coding import (
    CommitAuthority,
    CodingBoundary,
    PythonCommandProfile,
    ScopeExpansion,
    ScopeGapBlocked,
    StoryWorktree,
)


class FakeWorktreeHost:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []

    def create_worktree(self, *, base_branch: str, branch: str, path: Path) -> str:
        self.created.append((base_branch, branch, str(path)))
        return "base-sha"


class FakeCommitHost:
    def __init__(self, changed_paths: tuple[str, ...], check_results: dict[tuple[str, ...], bool]) -> None:
        self.changed_paths = changed_paths
        self.check_results = check_results
        self.ran: list[tuple[str, ...]] = []
        self.commits: list[str] = []

    def changed_paths_for(self, worktree: StoryWorktree) -> tuple[str, ...]:
        return self.changed_paths

    def run_check(self, worktree: StoryWorktree, command: tuple[str, ...]) -> bool:
        self.ran.append(command)
        return self.check_results[command]

    def create_commit(self, worktree: StoryWorktree, message: str) -> str:
        self.commits.append(message)
        return "abc123"

    def current_branch(self, worktree: StoryWorktree) -> str:
        return worktree.branch


def boundary() -> CodingBoundary:
    return CodingBoundary(
        approved_paths=("src/adk_agents/", "tests/"),
        command_profile=PythonCommandProfile((("pytest",), ("ruff", "check"))),
    )


def test_coding_boundary_allows_only_approved_paths_and_python_commands():
    subject = boundary()

    subject.require_path("src/adk_agents/coding.py")
    subject.require_command(("pytest",))

    with pytest.raises(ScopeGapBlocked, match="pyproject.toml"):
        subject.require_path("pyproject.toml")
    with pytest.raises(ScopeGapBlocked, match="git"):
        subject.require_command(("git", "status"))


def test_scope_gap_remains_blocked_until_a_user_approved_expansion_is_recorded():
    subject = boundary()

    with pytest.raises(ScopeGapBlocked):
        subject.require_path("docs/design.md")

    subject.record_expansion(
        ScopeExpansion(
            approved_by="human:andrew",
            approval_ref="#16-comment-1",
            paths=("docs/",),
        )
    )

    subject.require_path("docs/design.md")


def test_scope_gap_carries_a_manager_visible_blocked_result():
    with pytest.raises(ScopeGapBlocked) as error:
        boundary().require_path("docs/design.md")

    assert error.value.result.status.value == "blocked"
    assert error.value.result.board_update_request.proposed_status == "Blocked"


def test_host_creates_one_fresh_story_worktree_on_the_story_branch(tmp_path):
    host = FakeWorktreeHost()

    worktree = StoryWorktree.create(
        host=host,
        issue_number=16,
        slug="isolated-coding",
        path=tmp_path / "story",
    )

    assert worktree.branch == "agent/16-isolated-coding"
    assert worktree.base_commit == "base-sha"
    assert host.created == [("main", "agent/16-isolated-coding", str(tmp_path / "story"))]


def test_commit_authority_checks_allowed_diff_and_required_profile_before_one_commit(tmp_path):
    worktree = StoryWorktree(branch="agent/16-isolated-coding", path=tmp_path / "story")
    host = FakeCommitHost(
        changed_paths=("src/adk_agents/coding.py", "tests/test_coding.py"),
        check_results={("pytest",): True, ("ruff", "check"): True},
    )

    commit = CommitAuthority(host, boundary()).commit(worktree, "feat: enforce coding scope")

    assert commit == "abc123"
    assert host.ran == [("pytest",), ("ruff", "check")]
    assert host.commits == ["feat: enforce coding scope"]


def test_commit_authority_rejects_scope_violation_without_committing(tmp_path):
    worktree = StoryWorktree(branch="agent/16-isolated-coding", path=tmp_path / "story")
    host = FakeCommitHost(
        changed_paths=("pyproject.toml",),
        check_results={("pytest",): True, ("ruff", "check"): True},
    )

    with pytest.raises(ScopeGapBlocked):
        CommitAuthority(host, boundary()).commit(worktree, "feat: enforce coding scope")

    assert host.ran == []
    assert host.commits == []


def test_command_profile_rejects_non_python_or_network_capable_commands():
    with pytest.raises(ValueError, match="Python command profile"):
        PythonCommandProfile((("curl", "https://example.test"),))


def test_worktree_must_be_fresh(tmp_path):
    path = tmp_path / "story"
    path.mkdir()

    with pytest.raises(ValueError, match="fresh"):
        StoryWorktree.create(host=FakeWorktreeHost(), issue_number=16, slug="isolated-coding", path=path)


def test_commit_authority_creates_only_one_verified_commit(tmp_path):
    worktree = StoryWorktree(branch="agent/16-isolated-coding", path=tmp_path / "story", base_commit="base")
    host = FakeCommitHost(("src/adk_agents/coding.py",), {("pytest",): True, ("ruff", "check"): True})
    authority = CommitAuthority(host, boundary())

    authority.commit(worktree, "feat: enforce coding scope")
    with pytest.raises(RuntimeError, match="already committed"):
        authority.commit(worktree, "feat: enforce coding scope")


def test_commit_authority_rejects_failed_check_without_committing(tmp_path):
    worktree = StoryWorktree(branch="agent/16-isolated-coding", path=tmp_path / "story")
    host = FakeCommitHost(
        changed_paths=("src/adk_agents/coding.py",),
        check_results={("pytest",): False, ("ruff", "check"): True},
    )

    with pytest.raises(RuntimeError, match="required check failed"):
        CommitAuthority(host, boundary()).commit(worktree, "feat: enforce coding scope")

    assert host.commits == []
