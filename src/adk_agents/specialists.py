"""Narrow public contracts for the Research and Coding specialists."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import sleep
from typing import Any, Iterable

from .contracts import ResearchClaim, ResearchReport, SpecialistResult, SpecialistTask, SpecialistType, TaskStatus
from .evidence import ArtifactStore, EvidenceLedger


@dataclass(frozen=True)
class SearchHit:
    claim: str
    source_url: str


class RateLimited(RuntimeError):
    """Typed adapter signal; ordinary timeouts never trigger a retry."""


class DuckDuckGoSearchAdapter:
    """Narrow adapter over the DuckDuckGo Python client; it exposes search only."""

    def __call__(self, query: str) -> Iterable[SearchHit]:
        try:
            with self._default_client() as client:
                rows = client.text(query, max_results=10)
                return tuple(
                    SearchHit(claim=row.get("body") or row.get("title", ""), source_url=row["href"])
                    for row in rows
                    if row.get("href") and (row.get("body") or row.get("title"))
                )
        except Exception as error:
            if "rate" in str(error).lower() and "limit" in str(error).lower():
                raise RateLimited("DuckDuckGo rate limited the research request") from error
            raise

    @staticmethod
    def _default_client() -> Any:
        try:
            from duckduckgo_search import DDGS
        except ImportError as error:
            raise RuntimeError("DuckDuckGo research adapter is not installed") from error
        return DDGS()


class DurableResearchEvidence:
    """The only evidence capability granted to Research in production."""

    def __init__(self, artifacts: ArtifactStore, ledger: EvidenceLedger) -> None:
        self._artifacts = artifacts
        self._ledger = ledger

    def write(self, payload: object) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        manifest = self._artifacts.write(encoded, logical_type="research-report")
        self._ledger.append(
            action_type="research_evidence",
            input_value=payload,
            artifact_digest=manifest.digest,
            outcome_class="recorded",
        )
        return manifest.digest


@dataclass(frozen=True)
class ResearchCapabilities:
    """The sole production capability grant for Research work."""

    search: DuckDuckGoSearchAdapter
    evidence: DurableResearchEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.search, DuckDuckGoSearchAdapter) or not isinstance(self.evidence, DurableResearchEvidence):
            raise TypeError("Research may use only DuckDuckGo search and durable evidence capabilities")


class ResearchSpecialist:
    """Uses the policy-owned Research capability grant; no general tools are accepted."""

    def __init__(self, capabilities: ResearchCapabilities, *, max_attempts: int = 2, retry_delay_seconds: float = 0) -> None:
        if not isinstance(capabilities, ResearchCapabilities):
            raise TypeError("ResearchSpecialist requires ResearchCapabilities")
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        self._search, self._max_attempts, self._retry_delay_seconds = capabilities.search, max_attempts, retry_delay_seconds
        self._evidence = capabilities.evidence

    def research(self, question: str) -> ResearchReport:
        for attempt in range(self._max_attempts):
            try:
                hits = tuple(self._search(question))
                claims = [ResearchClaim(text=hit.claim, source_url=hit.source_url) for hit in hits]
                evidence_refs = [self._evidence.write({"question": question, "claims": [claim.model_dump() for claim in claims], "uncertainty": "Sources may be incomplete."})]
                return ResearchReport(claims=claims, uncertainty="Sources may be incomplete.", evidence_refs=evidence_refs)
            except RateLimited:
                if attempt + 1 == self._max_attempts:
                    return ResearchReport(
                        claims=[],
                        uncertainty="Research rate-limit retry policy exhausted; no provider fallback was used.",
                        evidence_refs=[self._evidence.write({"question": question, "exhausted": True})],
                        exhausted=True,
                    )
                sleep(self._retry_delay_seconds)
        raise AssertionError("unreachable")

    def run(self, task: SpecialistTask) -> SpecialistResult:
        """Handle one bounded Research dispatch through the Manager contract."""
        if task.specialist is not SpecialistType.RESEARCH:
            raise ValueError("Research specialist accepts only Research tasks")
        report = self.research(task.objective)
        evidence_refs = report.evidence_refs
        if report.exhausted:
            return SpecialistResult(
                status=TaskStatus.BLOCKED,
                summary="Research stopped after the configured rate-limit retry budget.",
                next_manager_action="record_research_exhaustion",
                evidence_refs=evidence_refs,
                research_report=report,
            )
        return SpecialistResult(
            status=TaskStatus.COMPLETED,
            summary=f"Research returned {len(report.claims)} cited finding(s).",
            next_manager_action="record_research_handoff",
            evidence_refs=evidence_refs,
            research_report=report,
        )


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
