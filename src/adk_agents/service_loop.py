"""One bounded polling tick that composes claim, admission, and durable handoff."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from time import sleep
from typing import Any, Protocol
from .task_format import parse_task_block

def task_from_issue_body(body: str, dispatch_id: str) -> dict[str, Any]:
    """Build the Manager input only from the explicit, validated issue block."""
    return parse_task_block(body, dispatch_id=dispatch_id).model_dump(mode="json")


class ClaimingBoard(Protocol):
    def claim_ready_story(self, candidate: object) -> object | None: ...
    def block_claimed_story(self, candidate: object, dispatch: object, summary: str) -> bool: ...


class AdmittingManager(Protocol):
    def admit(self, task: dict[str, Any]) -> Any: ...


class HandoffWorkflow(Protocol):
    def dispatch(self, dispatch_id: str, story_ref: str, request: Any) -> str: ...
    def handoff(self, dispatch_id: str, status: str, result: Any) -> str | None: ...


class PollingLease(Protocol):
    def acquire(self) -> bool: ...


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
            try:
                task = self._task_for(candidate, dispatch_id)
                self._workflow.dispatch(dispatch_id, task["story_ref"], task)
                result = self._manager.admit(task)
                self._workflow.handoff(dispatch_id, result.status.value, result.model_dump(mode="json"))
                if result.status.value == "blocked":
                    self._board.block_claimed_story(candidate, claim, result.summary)
                dispatched += 1
            except Exception as error:
                self._board.block_claimed_story(candidate, claim, f"Dispatch failed: {type(error).__name__}")
        return dispatched

    def run_forever(self, *, interval_seconds: float, should_stop: Callable[[], bool] = lambda: False, wait: Callable[[float], None] = sleep) -> None:
        """Run serial polling ticks until the host's shutdown signal is observed."""
        if interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        while not should_stop():
            self.tick()
            if not should_stop():
                wait(interval_seconds)


class LeasedPollingWorker:
    """Runs a PollingService only while this process owns the project lease."""

    def __init__(self, polling: PollingService, lease: PollingLease) -> None:
        self._polling, self._lease = polling, lease

    def tick(self) -> int:
        if not self._lease.acquire():
            return 0
        return self._polling.tick()

    def run_forever(self, *, interval_seconds: float, should_stop: Callable[[], bool] = lambda: False, wait: Callable[[float], None] = sleep) -> None:
        if interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        while not should_stop():
            self.tick()
            if not should_stop():
                wait(interval_seconds)
