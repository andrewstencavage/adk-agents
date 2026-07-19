from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adk_agents.contracts import SpecialistResult, SpecialistTask, SpecialistType, TaskStatus
from adk_agents.manager import AdmissionDenied, Manager, accepted_result
from adk_agents.research_admission import (
    AdmissionState,
    ResearchAdmissionCandidate,
    ResearchModelFingerprint,
    ResearchTrialSummary,
)
from adk_agents.trace import TraceStore


def fingerprint(*, artifact: str = "a") -> ResearchModelFingerprint:
    return ResearchModelFingerprint(
        runtime="ollama",
        model="qwen2.5:7b",
        model_artifact="sha256:" + artifact * 64,
        runtime_config={"temperature": "0"},
    )


def active_candidate(value: ResearchModelFingerprint) -> ResearchAdmissionCandidate:
    return ResearchAdmissionCandidate(
        candidate_id="candidate-1",
        control_issue_ref="#29",
        publication_ref="comment-1",
        fingerprint=value,
        state=AdmissionState.APPROVED,
        trial_evidence_ref="sha256:" + "b" * 64,
        trial_evidence_summary=ResearchTrialSummary(
            outcome="passed", cited_claim_count=1, unsupported_claim_count=0, typed_tool_call_count=1
        ),
        approval_evidence_ref="sha256:" + "c" * 64,
        approved_by="human:andrew",
    )


class AdmissionRegistry:
    def __init__(self, active: ResearchAdmissionCandidate | None) -> None:
        self.active = active

    def active_admission_for(self, value: ResearchModelFingerprint):
        return (
            self.active
            if self.active is not None
            and self.active.state is AdmissionState.APPROVED
            and self.active.fingerprint == value
            else None
        )

    def candidate_for(self, value: ResearchModelFingerprint):
        return self.active if self.active is not None and self.active.fingerprint == value else None


class MisreportingAdmissionRegistry(AdmissionRegistry):
    def active_admission_for(self, value: ResearchModelFingerprint):
        return self.active


def research_task(value: ResearchModelFingerprint) -> dict[str, object]:
    return {
        "control_issue_ref": "#1",
        "story_ref": "#11",
        "dispatch_id": "dispatch-0001",
        "specialist": SpecialistType.RESEARCH.value,
        "objective": "Summarize one bounded public source.",
        "acceptance_criteria": ["Return one cited finding."],
        "requested_by": "human:andrew",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "budget_steps": 2,
        "research_model_fingerprint": value.model_dump(mode="json"),
    }


def manager(tmp_path, registry: AdmissionRegistry, handler=accepted_result):
    return Manager(
        TraceStore(tmp_path / "record.sqlite3"),
        {role.value: handler for role in SpecialistType},
        registry,
    )


def test_matching_active_research_admission_dispatches_to_research(tmp_path):
    approved = active_candidate(fingerprint())
    subject = manager(tmp_path, AdmissionRegistry(approved))

    result = subject.admit(research_task(approved.fingerprint))

    assert result.status is TaskStatus.COMPLETED


@pytest.mark.parametrize("candidate_state", [None, AdmissionState.PENDING_APPROVAL, AdmissionState.FAILED])
def test_inactive_research_admission_blocks_before_research_runs(tmp_path, candidate_state):
    called = False
    value = fingerprint()
    candidate = active_candidate(value) if candidate_state is not None else None
    if candidate is not None:
        candidate = candidate.model_copy(update={"state": candidate_state})

    def should_not_run(_: SpecialistTask) -> SpecialistResult:
        nonlocal called
        called = True
        raise AssertionError("Research must not run without an active admission")

    subject = manager(tmp_path, AdmissionRegistry(candidate), should_not_run)

    with pytest.raises(AdmissionDenied) as error:
        subject.admit(research_task(value))

    assert not called
    assert error.value.denial.status is TaskStatus.BLOCKED
    assert error.value.denial.next_manager_action == "block_story"
    assert error.value.denial.evidence_refs == ([] if candidate is None else [candidate.trial_evidence_ref])


def test_mismatched_research_fingerprint_blocks_before_research_runs(tmp_path):
    approved = active_candidate(fingerprint())
    subject = manager(tmp_path, AdmissionRegistry(approved))

    with pytest.raises(AdmissionDenied) as error:
        subject.admit(research_task(fingerprint(artifact="d")))

    assert error.value.denial.status is TaskStatus.BLOCKED


@pytest.mark.parametrize(
    "candidate",
    [
        active_candidate(fingerprint()).model_copy(update={"state": AdmissionState.PENDING_APPROVAL}),
        active_candidate(fingerprint()),
    ],
)
def test_manager_defensively_rechecks_registry_admission_state_and_fingerprint(tmp_path, candidate):
    requested = fingerprint(artifact="d") if candidate.state is AdmissionState.APPROVED else candidate.fingerprint
    subject = manager(tmp_path, MisreportingAdmissionRegistry(candidate))

    with pytest.raises(AdmissionDenied):
        subject.admit(research_task(requested))
