from __future__ import annotations

from adk_agents.contracts import SpecialistType
from adk_agents.specialists import CodingBoundary, RateLimited, ResearchSpecialist, SearchHit
from adk_agents.workflow import ReviewGate, ReviewStatus
from adk_agents.operations import IncidentTracker, ServicePolicy


def test_research_retries_only_rate_limits_and_returns_cited_uncertain_report():
    calls = 0

    def search(_query: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimited("rate limited")
        return [SearchHit("A claim", "https://example.test/source")]

    result = ResearchSpecialist(search, max_attempts=2).research("bounded question")

    assert result.claims[0].source_url == "https://example.test/source"
    assert result.uncertainty == "Sources may be incomplete."
    assert calls == 2


def test_coding_boundary_reports_a_scope_gap_without_running_an_unapproved_command(tmp_path):
    boundary = CodingBoundary(tmp_path, approved_paths=("src",), approved_commands=("pytest",))

    blocked = boundary.authorize(path=".github/workflows/ci.yml", command="git status")

    assert blocked.blocked
    assert "scope expansion" in blocked.reason


def test_review_gate_blocks_a_third_revision_and_never_represents_human_approval():
    gate = ReviewGate(max_corrections=2)

    assert gate.evaluate(["tests passed"]).status is ReviewStatus.ACCEPTED
    assert gate.evaluate(["tests failed"]).status is ReviewStatus.NEEDS_REVISION
    assert gate.evaluate(["tests failed"]).status is ReviewStatus.NEEDS_REVISION
    assert gate.evaluate(["tests failed"]).status is ReviewStatus.BLOCKED
    assert gate.evaluate(["tests passed"]).human_approval is False


def test_operational_incident_is_deduplicated_after_three_failures_and_closes_after_recovery():
    incidents = IncidentTracker()

    assert incidents.record_failure("backup") is None
    assert incidents.record_failure("backup") is None
    incident = incidents.record_failure("backup")

    assert incident is not None
    assert incidents.record_failure("backup") == incident
    assert incidents.record_recovery("backup", healthy_hours=24) == incident
    assert ServicePolicy().restart_delay_seconds == 10
