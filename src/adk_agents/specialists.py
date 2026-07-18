"""Narrow public contracts for the Research and Coding specialists."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Callable, Iterable


@dataclass(frozen=True)
class SearchHit:
    claim: str
    source_url: str


@dataclass(frozen=True)
class CitedClaim:
    text: str
    source_url: str


@dataclass(frozen=True)
class ResearchReport:
    claims: tuple[CitedClaim, ...]
    uncertainty: str
    evidence_refs: tuple[str, ...] = ()


class RateLimited(RuntimeError):
    """Typed adapter signal; ordinary timeouts never trigger a retry."""


class ResearchSpecialist:
    """Uses only the injected typed search adapter; no browser or shell capability."""

    def __init__(self, search: Callable[[str], Iterable[SearchHit]], *, max_attempts: int = 2, retry_delay_seconds: float = 0, wait: Callable[[float], None] = sleep, evidence_writer: Callable[[object], str] | None = None) -> None:
        self._search, self._max_attempts, self._retry_delay_seconds, self._wait, self._evidence_writer = search, max_attempts, retry_delay_seconds, wait, evidence_writer

    def research(self, question: str) -> ResearchReport:
        for attempt in range(self._max_attempts):
            try:
                hits = tuple(self._search(question))
                claims = tuple(CitedClaim(hit.claim, hit.source_url) for hit in hits)
                evidence_refs = () if self._evidence_writer is None else (self._evidence_writer({"question": question, "claims": claims, "uncertainty": "Sources may be incomplete."}),)
                return ResearchReport(claims, "Sources may be incomplete.", evidence_refs)
            except RateLimited:
                if attempt + 1 == self._max_attempts:
                    raise RuntimeError("research rate-limit retry policy exhausted") from None
                self._wait(self._retry_delay_seconds)
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class ScopeDecision:
    blocked: bool
    reason: str


class CodingBoundary:
    """Validates an isolated worktree scope; it deliberately executes nothing."""

    def __init__(self, worktree: str | Path, *, approved_paths: tuple[str, ...], approved_commands: tuple[str, ...]) -> None:
        self._worktree = Path(worktree).resolve()
        self._paths, self._commands = approved_paths, approved_commands

    def authorize(self, *, path: str, command: str) -> ScopeDecision:
        candidate = (self._worktree / path).resolve()
        allowed_path = candidate.is_relative_to(self._worktree) and any(
            candidate.is_relative_to((self._worktree / prefix).resolve()) for prefix in self._paths
        )
        allowed_command = command in self._commands
        if allowed_path and allowed_command:
            return ScopeDecision(False, "approved")
        return ScopeDecision(True, "Blocked pending a user-approved scope expansion.")
