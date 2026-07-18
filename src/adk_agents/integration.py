"""Restart-safe, redacted handoff seam for an approved story."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .evidence import EvidenceLedger


class ApprovedStoryWorkflow:
    """Records dispatch/handoff evidence before emitting only a concise board event."""

    def __init__(self, ledger: EvidenceLedger, board_handoff: Callable[[dict[str, str]], None]) -> None:
        self._ledger, self._board_handoff = ledger, board_handoff

    def dispatch(self, dispatch_id: str, story_ref: str, request: Any) -> str:
        return self._ledger.append(action_type="story.dispatch", dispatch_id=dispatch_id, input_value=request, outcome_class="started")

    def handoff(self, dispatch_id: str, status: str, result: Any) -> str:
        event_id = self._ledger.append(action_type="story.handoff", dispatch_id=dispatch_id, output_value=result, outcome_class=status)
        self._board_handoff({"dispatch_id": dispatch_id, "status": status, "event_id": event_id})
        return event_id
