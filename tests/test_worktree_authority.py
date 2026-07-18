from pathlib import Path

from adk_agents.worktree_authority import WorktreeAuthority


def test_host_authority_creates_one_story_branch_from_main_and_returns_only_the_worktree_path(tmp_path):
    calls: list[tuple[str, ...]] = []
    authority = WorktreeAuthority(lambda argv: calls.append(argv), tmp_path)

    path = authority.create(16, "isolated-coding")

    assert path == tmp_path / "16-isolated-coding"
    assert calls == [("git", "worktree", "add", "-b", "agent/16-isolated-coding", str(path), "main")]


def test_host_authority_removes_only_a_story_worktree_at_terminal_state(tmp_path):
    calls: list[tuple[str, ...]] = []
    authority = WorktreeAuthority(lambda argv: calls.append(argv), tmp_path)
    path = tmp_path / "16-isolated-coding"

    authority.remove(path)

    assert calls == [("git", "worktree", "remove", "--force", str(path))]
