import pytest

from adk_agents.board_policy import BoardTransitionPolicy


def test_agent_board_policy_never_allows_ready_or_done_writes():
    writes: list[str] = []
    policy = BoardTransitionPolicy(writes.append)

    policy.move("In Progress")
    policy.move("Review")
    policy.move("Blocked")
    with pytest.raises(PermissionError):
        policy.move("Ready")
    with pytest.raises(PermissionError):
        policy.move("Done")

    assert writes == ["In Progress", "Review", "Blocked"]
