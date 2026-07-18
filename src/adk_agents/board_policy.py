"""Agent-owned Project Status transition boundary."""

from __future__ import annotations

from collections.abc import Callable


class BoardTransitionPolicy:
    """Preserves the human-only Ready/Done authority regardless of caller intent."""

    _AGENT_STATUSES = frozenset({"In Progress", "Review", "Blocked"})

    def __init__(self, set_status: Callable[[str], None]) -> None:
        self._set_status = set_status

    def move(self, status: str) -> None:
        if status not in self._AGENT_STATUSES:
            raise PermissionError("agents may not write Ready or Done")
        self._set_status(status)
