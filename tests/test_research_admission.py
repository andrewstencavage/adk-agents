from __future__ import annotations

import pytest

from adk_agents.research_admission import (
    AdmissionState,
    HumanApproval,
    ResearchAdmissionService,
    ResearchModelFingerprint,
    ResearchTrialSummary,
)


def fingerprint(*, runtime_config: dict[str, str] | None = None) -> ResearchModelFingerprint:
    return ResearchModelFingerprint(
        runtime="ollama",
        model="qwen2.5:7b",
        model_artifact="sha256:" + "a" * 64,
        runtime_config=runtime_config or {"temperature": "0"},
    )


def trial_summary(*, outcome: str = "passed") -> ResearchTrialSummary:
    return ResearchTrialSummary(
        outcome=outcome,
        cited_claim_count=2,
        unsupported_claim_count=0,
        typed_tool_call_count=1,
    )


class FakeControlSurface:
    def __init__(self) -> None:
        self.published = []
        self.approvals: dict[str, HumanApproval] = {}

    def publish_candidate(self, candidate):
        self.published.append(candidate)
        return f"github-comment-{len(self.published)}"

    def approval_for(self, candidate):
        return self.approvals.get(candidate.candidate_id)


def test_operator_can_nominate_and_approve_an_exact_research_fingerprint(tmp_path):
    service = ResearchAdmissionService(tmp_path / "record.sqlite3")
    control_surface = FakeControlSurface()

    candidate = service.nominate(
        fingerprint(),
        trial_evidence_ref="sha256:" + "b" * 64,
        trial_evidence_summary=trial_summary(),
        control_issue_ref="#29",
        control_surface=control_surface,
    )

    assert candidate.state is AdmissionState.PENDING_APPROVAL
    assert candidate.trial_evidence_ref == "sha256:" + "b" * 64
    assert candidate.publication_ref == "github-comment-1"
    assert control_surface.published[0].trial_evidence_ref == candidate.trial_evidence_ref
    assert control_surface.published[0].trial_evidence_summary == trial_summary()
    assert control_surface.published[0].control_issue_ref == "#29"
    assert service.active_admission_for(fingerprint()) is None
    control_surface.approvals[candidate.candidate_id] = HumanApproval(
        actor="human:andrew", evidence_ref="sha256:" + "c" * 64
    )

    approved = service.approve_from_control_surface(candidate.candidate_id, control_surface)

    assert approved.state is AdmissionState.APPROVED
    assert approved.approval_evidence_ref == "sha256:" + "c" * 64
    assert approved.approved_by == "human:andrew"
    assert service.active_admission_for(fingerprint()) == approved


def test_admission_persists_but_does_not_apply_to_a_changed_fingerprint(tmp_path):
    database = tmp_path / "record.sqlite3"
    first_service = ResearchAdmissionService(database)
    control_surface = FakeControlSurface()
    candidate = first_service.nominate(
        fingerprint(), trial_evidence_ref="sha256:" + "b" * 64,
        trial_evidence_summary=trial_summary(),
        control_issue_ref="#29", control_surface=control_surface,
    )
    control_surface.approvals[candidate.candidate_id] = HumanApproval(
        actor="human:andrew", evidence_ref="sha256:" + "c" * 64
    )
    first_service.approve_from_control_surface(candidate.candidate_id, control_surface)

    reloaded_service = ResearchAdmissionService(database)

    assert reloaded_service.active_admission_for(fingerprint()) is not None
    assert reloaded_service.active_admission_for(fingerprint(runtime_config={"temperature": "0.2"})) is None


def test_failed_candidate_never_becomes_an_active_admission(tmp_path):
    service = ResearchAdmissionService(tmp_path / "record.sqlite3")
    candidate = service.nominate(
        fingerprint(), trial_evidence_ref="sha256:" + "b" * 64,
        trial_evidence_summary=trial_summary(outcome="failed"),
        control_issue_ref="#29", control_surface=FakeControlSurface(),
    )

    failed = service.fail(candidate.candidate_id, failure_evidence_ref="sha256:" + "d" * 64)

    assert failed.state is AdmissionState.FAILED
    assert failed.failure_evidence_ref == "sha256:" + "d" * 64
    assert service.active_admission_for(fingerprint()) is None


def test_operator_cannot_record_non_digest_admission_evidence(tmp_path):
    service = ResearchAdmissionService(tmp_path / "record.sqlite3")
    control_surface = FakeControlSurface()

    with pytest.raises(ValueError):
        service.nominate(fingerprint(), trial_evidence_ref="raw trial output", trial_evidence_summary=trial_summary(), control_issue_ref="#29", control_surface=control_surface)

    candidate = service.nominate(
        fingerprint(), trial_evidence_ref="sha256:" + "b" * 64,
        trial_evidence_summary=trial_summary(),
        control_issue_ref="#29", control_surface=control_surface,
    )

    with pytest.raises(ValueError):
        control_surface.approvals[candidate.candidate_id] = HumanApproval(
            actor="human:andrew", evidence_ref="raw approval"
        )
        service.approve_from_control_surface(candidate.candidate_id, control_surface)


def test_operator_cannot_persist_secret_bearing_runtime_configuration(tmp_path):
    with pytest.raises(ValueError, match="non-secret"):
        fingerprint(runtime_config={"temperature": "secret-value"})


def test_control_surface_receives_only_a_count_only_trial_summary(tmp_path):
    service = ResearchAdmissionService(tmp_path / "record.sqlite3")

    with pytest.raises(ValueError):
        service.nominate(
            fingerprint(),
            trial_evidence_ref="sha256:" + "b" * 64,
            trial_evidence_summary={"outcome": "passed", "raw_output": "secret-value"},
            control_issue_ref="#29",
            control_surface=FakeControlSurface(),
        )
