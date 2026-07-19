from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adk_agents.contracts import SpecialistTask, TaskStatus
from adk_agents.contracts import SpecialistType
import pytest

from adk_agents.specialists import CodingBoundary, DuckDuckGoSearchAdapter, RateLimited, ResearchSpecialist, SearchHit
from adk_agents.workflow import ReviewGate, ReviewStatus
from adk_agents.operations import IncidentTracker, ServicePolicy
from adk_agents.operations import PersistentIncidentTracker
from adk_agents.operational_record import OperationalRecord
from adk_agents.evidence import ArtifactStore, EvidenceLedger
from adk_agents.specialists import DurableResearchEvidence, ResearchCapabilities


class FakeDuckDuckGoClient:
    def __init__(self, responses):
        self._responses = iter(responses)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def text(self, _query: str, *, max_results: int):
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def research_specialist(tmp_path, monkeypatch, responses, **kwargs):
    record = OperationalRecord(tmp_path / "research.sqlite3")
    record.startup()
    client = FakeDuckDuckGoClient(responses)
    monkeypatch.setattr(DuckDuckGoSearchAdapter, "_default_client", staticmethod(lambda: client))
    capabilities = ResearchCapabilities(
        DuckDuckGoSearchAdapter(),
        DurableResearchEvidence(ArtifactStore(record, tmp_path / "artifacts"), EvidenceLedger(record)),
    )
    return ResearchSpecialist(capabilities, **kwargs)


def test_research_retries_only_rate_limits_and_returns_cited_uncertain_report(tmp_path, monkeypatch):
    result = research_specialist(tmp_path, monkeypatch, [RateLimited("rate limited"), [{"body": "A claim", "href": "https://example.test/source"}]], max_attempts=2).research("bounded question")

    assert result.claims[0].source_url == "https://example.test/source"
    assert result.uncertainty == "Sources may be incomplete."


def test_research_emits_a_redacted_evidence_reference_through_its_typed_writer(tmp_path, monkeypatch):
    report = research_specialist(tmp_path, monkeypatch, [[{"body": "A claim", "href": "https://example.test/source"}]]).research("bounded question")

    assert report.evidence_refs[0].startswith("sha256:")


def test_research_reports_rate_limit_exhaustion_without_provider_fallback(tmp_path, monkeypatch):
    report = research_specialist(tmp_path, monkeypatch, [RateLimited("rate limited")], max_attempts=1).research("bounded question")

    assert report.exhausted is True
    assert report.claims == []


def test_research_specialist_returns_typed_cited_findings_for_a_dispatched_task(tmp_path, monkeypatch):
    task = SpecialistTask(
        control_issue_ref="#1",
        story_ref="#15",
        dispatch_id="research-dispatch-15",
        specialist=SpecialistType.RESEARCH,
        objective="Find an authoritative answer.",
        acceptance_criteria=["Return cited, uncertainty-aware findings."],
        requested_by="user",
        deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_steps=1,
    )

    result = research_specialist(tmp_path, monkeypatch, [[{"body": "A claim", "href": "https://example.test/source"}]]).run(task)

    assert result.status is TaskStatus.COMPLETED
    assert result.research_report is not None
    assert result.research_report.claims[0].source_url == "https://example.test/source"
    assert result.research_report.uncertainty == "Sources may be incomplete."


def test_research_rejects_non_policy_capabilities():
    with pytest.raises(TypeError, match="ResearchCapabilities"):
        ResearchSpecialist(object())


@pytest.mark.parametrize("max_attempts", [0, -1, 6])
def test_research_retry_budget_is_validated(max_attempts, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="max_attempts"):
        research_specialist(tmp_path, monkeypatch, [[]], max_attempts=max_attempts)


def test_duckduckgo_adapter_exposes_only_cited_search_hits():
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def text(self, _query: str, *, max_results: int):
            assert max_results == 10
            return [{"body": "A claim", "href": "https://example.test/source"}]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(DuckDuckGoSearchAdapter, "_default_client", staticmethod(Client))
    hits = tuple(DuckDuckGoSearchAdapter()("bounded question"))
    monkeypatch.undo()

    assert hits == (SearchHit("A claim", "https://example.test/source"),)


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


def test_persistent_incident_survives_restart_without_a_duplicate_publish(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    published: list[tuple[str, str]] = []
    first = PersistentIncidentTracker(record, lambda incident, evidence: published.append((incident, evidence)))
    for _ in range(3):
        assert first.record_failure("backup", "sha256:evidence") in {None, "incident:backup"}

    restarted = PersistentIncidentTracker(record, lambda incident, evidence: published.append((incident, evidence)))
    assert restarted.record_failure("backup", "sha256:evidence") == "incident:backup"
    assert published == [("incident:backup", "sha256:evidence")]
    assert restarted.record_recovery("backup", healthy_hours=23) is None
    assert restarted.record_recovery("backup", healthy_hours=24) == "incident:backup"
