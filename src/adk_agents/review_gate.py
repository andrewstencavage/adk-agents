"""Independent, bounded Review-gate and pull-request handoff workflow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ReviewState(str, Enum):
    ACCEPTED = "accepted"
    NEEDS_CORRECTION = "needs_correction"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Finding:
    """A blocking review finding with enough evidence for Coding to correct it."""

    severity: str
    path: str
    violated_requirement: str
    remediation: str


@dataclass(frozen=True)
class CommandResult:
    command: str
    passed: bool
    transient: bool = False
    path: str = "<review checkout>"
    detail: str = ""


@dataclass(frozen=True)
class ReadOnlyCheckout:
    path: str
    source_worktree: str
    read_only: bool


@dataclass(frozen=True)
class PullRequestRequest:
    story_ref: str
    head_branch: str
    base_branch: str
    body: str
    review_gate_version: str


@dataclass(frozen=True)
class ReviewRequest:
    story_ref: str
    branch: str
    commit_sha: str
    source_worktree: str
    acceptance_criteria: list[str]
    approved_commands: list[str]
    changed_paths: list[str]
    approved_paths: list[str]
    correction_cycle: int
    implementation_summary: str
    handoff_evidence: list[str]
    findings: list[Finding] | None = None


@dataclass(frozen=True)
class ReviewOutcome:
    state: ReviewState
    correction_cycle: int
    findings: list[Finding]
    pull_request_url: str | None = None
    blocked_reason: str | None = None


class CheckoutProvider(Protocol):
    def create_read_only(self, branch: str, commit_sha: str) -> ReadOnlyCheckout: ...


class CheckRunner(Protocol):
    def run(self, checkout: ReadOnlyCheckout, command: str) -> CommandResult: ...


class PullRequestPublisher(Protocol):
    """May create a ready-for-review PR, but deliberately has no approve/merge API."""

    def create_ready_for_review(self, request: PullRequestRequest) -> str: ...


class ReviewGate:
    """Review committed work in isolation, allowing at most two corrections."""

    VERSION = "v1"
    _RESTRICTED_PREFIXES = (".github/",)
    _RESTRICTED_FILES = {"pyproject.toml", "uv.lock", "requirements.txt", "requirements.lock"}

    def __init__(
        self,
        checkouts: CheckoutProvider,
        checks: CheckRunner,
        pull_requests: PullRequestPublisher,
    ) -> None:
        self._checkouts = checkouts
        self._checks = checks
        self._pull_requests = pull_requests

    def review(self, request: ReviewRequest) -> ReviewOutcome:
        """Run the versioned gate and publish only an accepted story's normal PR."""
        checkout = self._checkouts.create_read_only(request.branch, request.commit_sha)
        if not checkout.read_only or checkout.path == request.source_worktree or checkout.source_worktree != request.source_worktree:
            return self._blocked(request, "Review checkout is not independent and read-only.")

        findings = self._scope_findings(request)
        command_outcome = self._run_commands(checkout, request.approved_commands, request.correction_cycle)
        if command_outcome is not None:
            return command_outcome if command_outcome.state is ReviewState.BLOCKED else self._reject(request, command_outcome.findings)
        findings.extend(request.findings or [])
        if findings:
            return self._reject(request, findings)

        pr_request = PullRequestRequest(
            story_ref=request.story_ref,
            head_branch=request.branch,
            base_branch="main",
            review_gate_version=self.VERSION,
            body=self._pr_body(request),
        )
        return ReviewOutcome(
            state=ReviewState.ACCEPTED,
            correction_cycle=request.correction_cycle,
            findings=[],
            pull_request_url=self._pull_requests.create_ready_for_review(pr_request),
        )

    def _run_commands(
        self, checkout: ReadOnlyCheckout, commands: list[str], correction_cycle: int
    ) -> ReviewOutcome | None:
        for command in commands:
            result = self._checks.run(checkout, command)
            if result.passed:
                continue
            if result.transient:
                retry = self._checks.run(checkout, command)
                if retry.passed:
                    continue
                if retry.transient:
                    return ReviewOutcome(
                        state=ReviewState.BLOCKED,
                        correction_cycle=correction_cycle,
                        findings=[],
                        blocked_reason=f"Repeated transient failure for `{command}`: {retry.detail}",
                    )
                result = retry
            return ReviewOutcome(
                state=ReviewState.NEEDS_CORRECTION,
                correction_cycle=correction_cycle,
                findings=[
                    Finding(
                        severity="blocking",
                        path=result.path,
                        violated_requirement=f"Approved command `{command}` must pass.",
                        remediation="Fix the reported failures, then submit a new verified commit.",
                    )
                ],
            )
        return None

    def _scope_findings(self, request: ReviewRequest) -> list[Finding]:
        findings = []
        for path in request.changed_paths:
            allowed = any(path.startswith(prefix) for prefix in request.approved_paths)
            restricted = path in self._RESTRICTED_FILES or path.startswith(self._RESTRICTED_PREFIXES)
            if not allowed or restricted:
                findings.append(
                    Finding(
                        severity="blocking",
                        path=path,
                        violated_requirement="Changes must stay within approved scope and exclude dependency or CI/workflow files.",
                        remediation="Remove the unapproved change or obtain recorded scope approval.",
                    )
                )
        return findings

    def _reject(self, request: ReviewRequest, findings: list[Finding]) -> ReviewOutcome:
        if request.correction_cycle >= 2:
            return self._blocked(request, "Review remains unresolved after two correction cycles.")
        return ReviewOutcome(ReviewState.NEEDS_CORRECTION, request.correction_cycle, findings)

    def _blocked(self, request: ReviewRequest, reason: str) -> ReviewOutcome:
        return ReviewOutcome(ReviewState.BLOCKED, request.correction_cycle, [], blocked_reason=reason)

    def _pr_body(self, request: ReviewRequest) -> str:
        return "\n".join(
            [
                f"Story: {request.story_ref}",
                "",
                request.implementation_summary,
                "",
                f"Commit: `{request.commit_sha}`",
                f"Review gate: {self.VERSION}",
                f"Revision count: {request.correction_cycle}",
                "Approved checks: " + ", ".join(request.approved_commands),
                "Handoff evidence: " + ", ".join(request.handoff_evidence),
            ]
        )
