from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adk_agents.contracts import SpecialistType
from adk_agents.manager import Manager, accepted_result
from adk_agents.trace import TraceStore
from adk_agents.operational_record import OperationalRecord
from adk_agents.routing import (
    AssessmentStatus,
    ModelAssessment,
    ModelRef,
    ModelRouter,
    NoEligibleModel,
    RouteRequest,
)
from adk_agents.runtime_discovery import RuntimeConfig, RuntimeDiscovery


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, body: object | None = None) -> object:
        self.calls.append((method, url))
        if url.endswith("/api/tags"):
            return {"models": [{"name": "llama:8b", "digest": "sha256:ollama", "details": {"parameter_size": "8B"}}]}
        if url.endswith("/api/show"):
            return {"details": {"family": "llama", "quantization_level": "Q4"}, "capabilities": ["tools"]}
        if url.endswith("/api/v1/models"):
            return {"data": [
                {"key": "chat", "type": "llm", "architecture": "llama", "quantization": "Q4", "size_bytes": 10, "max_context_length": 8192},
                {"key": "embed", "type": "embedding"},
            ]}
        raise AssertionError(url)


def model() -> ModelRef:
    return ModelRef(runtime_id="ollama", model_id="llama:8b", fingerprint="sha256:ollama", runtime_version="0.1")


def assessment(role: SpecialistType, *, score: float = 95, status: AssessmentStatus = AssessmentStatus.PASSED) -> ModelAssessment:
    return ModelAssessment(model=model(), role=role, suite_version="2026.1", status=status, score=score, artifact_ref="sha256:evidence")


def test_discovery_uses_only_configured_loopback_inventory_endpoints():
    transport = FakeTransport()
    discovery = RuntimeDiscovery(
        [RuntimeConfig("ollama", "http://127.0.0.1:11434", "0.1"), RuntimeConfig("lm_studio", "http://127.0.0.1:1234", "1.0")],
        transport,
    )

    discovered = discovery.discover()

    assert {(item.ref.runtime_id, item.ref.model_id) for item in discovered} == {("ollama", "llama:8b"), ("lm_studio", "chat")}
    assert all("127.0.0.1" in url for _, url in transport.calls)
    assert not any("load" in url or "pull" in url for _, url in transport.calls)


@pytest.mark.parametrize("url", ["http://127.0.0.1.evil", "http://127.0.0.1@evil.example", "http://localhost.evil", "https://example.com"])
def test_runtime_configuration_rejects_loopback_lookalikes(url):
    with pytest.raises(ValueError, match="loopback"):
        RuntimeConfig("ollama", url, "0.1")


def test_router_blocks_when_no_current_passing_exact_assessment(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1")

    with pytest.raises(NoEligibleModel, match="Blocked"):
        router.select(RouteRequest(dispatch_id="dispatch-0001", role=SpecialistType.RESEARCH))


def test_router_requires_exact_fingerprint_and_records_selection_evidence(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1")
    router.record_assessment(assessment(SpecialistType.RESEARCH))

    selection = router.select(RouteRequest(dispatch_id="dispatch-0001", role=SpecialistType.RESEARCH), [model()])

    assert selection.model == model()
    assert selection.evidence_ref.startswith("sha256:")
    with record.connection() as connection:
        row = connection.execute("SELECT selected_model_id, override_used FROM model_selection").fetchone()
    assert tuple(row) == ("llama:8b", 0)


def test_router_honors_only_an_eligible_user_override(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1")
    router.record_assessment(assessment(SpecialistType.RESEARCH))

    selection = router.select(RouteRequest(dispatch_id="dispatch-0001", role=SpecialistType.RESEARCH, user_override=model()), [model()])

    assert selection.override_used is True
    with pytest.raises(NoEligibleModel, match="override"):
        router.select(RouteRequest(dispatch_id="dispatch-0002", role=SpecialistType.RESEARCH, user_override=ModelRef("ollama", "other", "sha256:other", "0.1")), [model()])


def test_changed_fingerprint_cannot_use_prior_assessment(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1")
    router.record_assessment(assessment(SpecialistType.RESEARCH))
    changed = ModelRef("ollama", "llama:8b", "sha256:new", "0.1")

    with pytest.raises(NoEligibleModel):
        router.select(RouteRequest(dispatch_id="dispatch-0001", role=SpecialistType.RESEARCH), [changed])


def test_router_enforces_the_role_threshold_with_a_mocked_local_inventory(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1")
    router.record_assessment(assessment(SpecialistType.RESEARCH, score=84))

    with pytest.raises(NoEligibleModel, match="eligible"):
        router.select(RouteRequest(dispatch_id="dispatch-0001", role=SpecialistType.RESEARCH), [model()])


def test_router_blocks_a_stale_passing_assessment(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1", max_assessment_age=timedelta(seconds=0))
    router.record_assessment(assessment(SpecialistType.RESEARCH))

    with pytest.raises(NoEligibleModel, match="eligible"):
        router.select(RouteRequest(dispatch_id="dispatch-0001", role=SpecialistType.RESEARCH), [model()])


def test_router_records_a_redacted_model_outcome_for_a_dispatch(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1")

    router.record_outcome("dispatch-0001", SpecialistType.RESEARCH, model(), "completed")

    with record.connection() as connection:
        row = connection.execute("SELECT dispatch_id, role, outcome FROM model_outcome").fetchone()
    assert tuple(row) == ("dispatch-0001", "research", "completed")


def test_manager_returns_a_visible_blocked_story_when_nothing_is_eligible(tmp_path):
    record = OperationalRecord(tmp_path / "routing.sqlite3")
    record.startup()
    router = ModelRouter(record, suite_version="2026.1")
    manager = Manager(TraceStore(tmp_path / "manager.sqlite3"), {role.value: accepted_result for role in SpecialistType}, router=router)
    task = {
        "control_issue_ref": "#1", "story_ref": "#14", "dispatch_id": "dispatch-0001",
        "specialist": SpecialistType.RESEARCH.value, "objective": "Bounded research.",
        "acceptance_criteria": ["Return one finding."], "requested_by": "human:andrew",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "budget_steps": 2,
    }
    result = manager.admit(task)

    assert result.status.value == "blocked"
    assert result.next_manager_action == "create_blocked_story"
    assert result.evidence_refs[0].startswith("sha256:")
