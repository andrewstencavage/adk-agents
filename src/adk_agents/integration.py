"""Restart-safe, redacted handoff seam for an approved story."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .evidence import EvidenceLedger
from .operational_record import OperationalRecord


class ApprovedStoryWorkflow:
    """Records dispatch/handoff evidence before emitting only a concise board event."""

    def __init__(self, ledger: EvidenceLedger, board_handoff: Callable[[dict[str, str]], None]) -> None:
        self._ledger, self._board_handoff = ledger, board_handoff
        self._record: OperationalRecord = ledger.record

    def dispatch(self, dispatch_id: str, story_ref: str, request: Any) -> str:
        with self._record.connection() as connection:
            existing = connection.execute("SELECT dispatch_id FROM dispatch WHERE dispatch_id = ?", (dispatch_id,)).fetchone()
            if existing is not None:
                return dispatch_id
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("INSERT INTO dispatch(dispatch_id, project_item_id, issue_node_id, ready_generation, local_state, selected_model_ref, created_at, updated_at) VALUES (?, NULL, ?, NULL, 'running', NULL, ?, ?)", (dispatch_id, story_ref, now, now))
        return self._ledger.append(action_type="story.dispatch", dispatch_id=dispatch_id, input_value=request, outcome_class="started")

    def handoff(self, dispatch_id: str, status: str, result: Any) -> str:
        with self._record.connection() as connection:
            existing = connection.execute("SELECT event_id, delivered FROM story_handoff WHERE dispatch_id = ? AND status = ?", (dispatch_id, status)).fetchone()
            if existing is not None:
                event_id = existing["event_id"]
                if not existing["delivered"]:
                    self._deliver(event_id, dispatch_id, status)
                return event_id
        event_id = self._ledger.append(action_type="story.handoff", dispatch_id=dispatch_id, output_value=result, outcome_class=status)
        with self._record.connection() as connection:
            connection.execute("INSERT INTO story_handoff(dispatch_id, status, event_id, delivered, created_at) VALUES (?, ?, ?, 0, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))", (dispatch_id, status, event_id))
            connection.execute("UPDATE dispatch SET local_state = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE dispatch_id = ?", (status, dispatch_id))
        self._deliver(event_id, dispatch_id, status)
        return event_id

    def recover_pending_handoffs(self) -> int:
        """Replay only persisted, concise board events after a crash.

        The result payload remains in the immutable evidence ledger; a retry
        sends just the event reference and lifecycle outcome to the board.
        """
        with self._record.connection() as connection:
            pending = connection.execute(
                "SELECT dispatch_id, status, event_id FROM story_handoff WHERE delivered = 0 ORDER BY created_at"
            ).fetchall()
        for row in pending:
            self._deliver(row["event_id"], row["dispatch_id"], row["status"])
        return len(pending)

    def _deliver(self, event_id: str, dispatch_id: str, status: str) -> None:
        self._board_handoff({"dispatch_id": dispatch_id, "status": status, "event_id": event_id})
        with self._record.connection() as connection:
            connection.execute("UPDATE story_handoff SET delivered = 1 WHERE event_id = ?", (event_id,))
