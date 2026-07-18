"""One bounded polling tick that composes claim, admission, and durable handoff."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from time import sleep
from typing import Any, Protocol


class ClaimingBoard(Protocol):
    def claim_ready_story(self, candidate: object) -> object | None: ...


class AdmittingManager(Protocol):
    def admit(self, task: dict[str, Any]) -> Any: ...


class HandoffWorkflow(Protocol):
    def handoff(self, dispatch_id: str, status: str, result: Any) -> str | None: ...


class PollingService:
    """Never dispatches unclaimed work; adapters remain lifecycle authorities."""

    def __init__(self, board: ClaimingBoard, manager: AdmittingManager, workflow: HandoffWorkflow, candidates: Callable[[], Iterable[object]], task_for: Callable[[object, str], dict[str, Any]]) -> None:
        self._board, self._manager, self._workflow, self._candidates, self._task_for = board, manager, workflow, candidates, task_for

    def tick(self) -> int:
        dispatched = 0
        for candidate in self._candidates():
            claim = self._board.claim_ready_story(candidate)
            if claim is None:
                continue
            dispatch_id = getattr(claim, "dispatch_id")
            result = self._manager.admit(self._task_for(candidate, dispatch_id))
            self._workflow.handoff(dispatch_id, result.status.value, result.model_dump(mode="json"))
            dispatched += 1
        return dispatched

    def run_forever(self, *, interval_seconds: float, should_stop: Callable[[], bool] = lambda: False, wait: Callable[[float], None] = sleep) -> None:
        """Run serial polling ticks until the host's shutdown signal is observed."""
        if interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        while not should_stop():
            self.tick()
            if not should_stop():
                wait(interval_seconds)
