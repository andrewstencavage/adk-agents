from __future__ import annotations

from dataclasses import dataclass

from adk_agents.review_gate import (
    CommandResult,
    CriterionResult,
    Finding,
    ReviewGate,
    ReviewRequest,
    ReviewState,
)


@dataclass
class FakeCheckout:
    path: str = "/reviews/17"
    read_only: bool = True
    source_worktree: str = "/worktrees/17"


class FakeCheckoutProvider:
    def __init__(self, checkout: FakeCheckout | None = None) -> None:
        self.checkout = checkout or FakeCheckout()
        self.calls: list[tuple[str, str]] = []

    def create_read_only(self, branch: str, commit_sha: str) -> FakeCheckout:
        self.calls.append((branch, commit_sha))
        return self.checkout


class FakeChecks:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[str, str]] = []

    def run(self, checkout: FakeCheckout, command: str) -> CommandResult:
        self.calls.append((checkout.path, command))
        return next(self.results)


class FakePullRequests:
    def __init__(self) -> None:
        self.requests = []

    def create_ready_for_review(self, request):
        self.requests.append(request)
        return "https://github.test/acme/adk-agents/pull/17"


def request(**overrides) -> ReviewRequest:
    values = {
        "story_ref": "#17",
        "branch": "agent/17-review-gate",
        "commit_sha": "a" * 40,
        "source_worktree": "/worktrees/17",
        "acceptance_criteria": ["Review uses a read-only checkout."],
        "criterion_results": [
            CriterionResult(
                criterion="Review uses a read-only checkout.",
                passed=True,
                path="src/adk_agents/review_gate.py",
            )
        ],
        "approved_commands": ["pytest -q"],
        "changed_paths": ["src/adk_agents/review_gate.py"],
        "approved_paths": ["src/", "tests/"],
        "correction_cycle": 0,
        "implementation_summary": "Adds the review gate.",
        "handoff_evidence": ["sha256:" + "b" * 64],
    }
    values.update(overrides)
    return ReviewRequest(**values)


def passing_result() -> CommandResult:
    return CommandResult(command="pytest -q", passed=True)


def test_acceptance_uses_independent_read_only_checkout_and_creates_normal_pr():
    checkouts = FakeCheckoutProvider()
    prs = FakePullRequests()
    gate = ReviewGate(checkouts, FakeChecks([passing_result()]), prs)

    outcome = gate.review(request())

    assert outcome.state is ReviewState.ACCEPTED
    assert checkouts.calls == [("agent/17-review-gate", "a" * 40)]
    assert outcome.pull_request_url == "https://github.test/acme/adk-agents/pull/17"
    assert len(prs.requests) == 1
    assert prs.requests[0].base_branch == "main"
    assert prs.requests[0].review_gate_version == "v1"
    assert "Revision count: 0" in prs.requests[0].body


def test_failed_check_returns_actionable_blocking_finding_without_creating_pr():
    prs = FakePullRequests()
    gate = ReviewGate(
        FakeCheckoutProvider(),
        FakeChecks([CommandResult(command="pytest -q", passed=False, path="tests/test_review_gate.py", detail="2 failures")]),
        prs,
    )

    outcome = gate.review(request())

    assert outcome.state is ReviewState.NEEDS_CORRECTION
    assert outcome.findings == [
        Finding(
            severity="blocking",
            path="tests/test_review_gate.py",
            violated_requirement="Approved command `pytest -q` must pass.",
            remediation="Fix the reported failures, then submit a new verified commit.",
        )
    ]
    assert prs.requests == []


def test_repeated_transient_check_failure_blocks_without_consuming_a_correction_cycle():
    checks = FakeChecks(
        [
            CommandResult(command="pytest -q", passed=False, transient=True, detail="runner timeout"),
            CommandResult(command="pytest -q", passed=False, transient=True, detail="runner timeout"),
        ]
    )
    gate = ReviewGate(FakeCheckoutProvider(), checks, FakePullRequests())

    outcome = gate.review(request(correction_cycle=1))

    assert outcome.state is ReviewState.BLOCKED
    assert outcome.correction_cycle == 1
    assert "Repeated transient failure" in outcome.blocked_reason
    assert len(checks.calls) == 2


def test_a_third_unresolved_review_result_blocks_the_story():
    gate = ReviewGate(
        FakeCheckoutProvider(),
        FakeChecks([passing_result()]),
        FakePullRequests(),
    )

    outcome = gate.review(
        request(
            correction_cycle=2,
            findings=[
                Finding(
                    severity="blocking",
                    path="src/adk_agents/review_gate.py",
                    violated_requirement="Review reports required evidence.",
                    remediation="Add the missing evidence to the handoff.",
                )
            ],
        )
    )

    assert outcome.state is ReviewState.BLOCKED
    assert "two correction cycles" in outcome.blocked_reason


def test_unapproved_ci_change_is_a_blocking_finding_with_path_evidence():
    gate = ReviewGate(FakeCheckoutProvider(), FakeChecks([passing_result()]), FakePullRequests())

    outcome = gate.review(request(changed_paths=[".github/workflows/test.yml"]))

    assert outcome.state is ReviewState.NEEDS_CORRECTION
    assert outcome.findings[0].path == ".github/workflows/test.yml"
    assert outcome.findings[0].remediation == "Remove the unapproved change or obtain recorded scope approval."


def test_unmet_acceptance_criterion_blocks_pr_with_evidence_and_remediation():
    prs = FakePullRequests()
    gate = ReviewGate(FakeCheckoutProvider(), FakeChecks([passing_result()]), prs)

    outcome = gate.review(
        request(
            criterion_results=[
                CriterionResult(
                    criterion="Review uses a read-only checkout.",
                    passed=False,
                    path="src/adk_agents/review_gate.py",
                    detail="checkout mutability was not proven",
                )
            ]
        )
    )

    assert outcome.state is ReviewState.NEEDS_CORRECTION
    assert outcome.findings[0].violated_requirement == "Review uses a read-only checkout."
    assert outcome.findings[0].remediation == "Provide a committed implementation that satisfies this criterion."
    assert prs.requests == []


def test_approved_ci_scope_expansion_is_not_rejected():
    gate = ReviewGate(FakeCheckoutProvider(), FakeChecks([passing_result()]), FakePullRequests())

    outcome = gate.review(
        request(
            changed_paths=[".github/workflows/test.yml"],
            scope_expansion_approved_paths=[".github/"],
        )
    )

    assert outcome.state is ReviewState.ACCEPTED


def test_an_invalid_third_correction_cycle_is_blocked_before_pr_creation():
    prs = FakePullRequests()
    gate = ReviewGate(FakeCheckoutProvider(), FakeChecks([passing_result()]), prs)

    outcome = gate.review(request(correction_cycle=3))

    assert outcome.state is ReviewState.BLOCKED
    assert "two correction cycles" in outcome.blocked_reason
    assert prs.requests == []


def test_pr_handoff_includes_actual_passing_check_result_and_changed_file_summary():
    prs = FakePullRequests()
    gate = ReviewGate(FakeCheckoutProvider(), FakeChecks([passing_result()]), prs)

    gate.review(request())

    assert "Check result: `pytest -q` — passed" in prs.requests[0].body
    assert "Changed files: src/adk_agents/review_gate.py" in prs.requests[0].body
