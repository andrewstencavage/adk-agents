"""Bounded, non-mutating review gate state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReviewStatus(str, Enum):
    ACCEPTED = "accepted"
    NEEDS_REVISION = "needs_revision"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ReviewDecision:
    status: ReviewStatus
    human_approval: bool = False


class ReviewGate:
    """Accepts only passing checks and bounds correction cycles; cannot approve a PR."""

    def __init__(self, *, max_corrections: int = 2) -> None:
        self._max_corrections, self._corrections = max_corrections, 0

    def evaluate(self, check_results: list[str]) -> ReviewDecision:
        if not check_results:
            return ReviewDecision(ReviewStatus.BLOCKED)
        if all(result in {"tests passed", "lint passed", "types passed"} for result in check_results):
            return ReviewDecision(ReviewStatus.ACCEPTED)
        if self._corrections >= self._max_corrections:
            return ReviewDecision(ReviewStatus.BLOCKED)
        self._corrections += 1
        return ReviewDecision(ReviewStatus.NEEDS_REVISION)
