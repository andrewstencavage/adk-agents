"""The root Manager admission boundary and named specialist dispatch seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from .contracts import SpecialistResult, SpecialistTask, SpecialistType, TaskStatus
from .research_admission import AdmissionState, ResearchAdmissionCandidate, ResearchModelFingerprint
from .trace import TraceStore

SpecialistHandler = Callable[[SpecialistTask], SpecialistResult]


class ResearchAdmissionRegistry(Protocol):
    """Read-only admission lookup used at the Manager's Research boundary."""

    def active_admission_for(
        self, fingerprint: ResearchModelFingerprint
    ) -> ResearchAdmissionCandidate | None: ...

    def candidate_for(self, fingerprint: ResearchModelFingerprint) -> ResearchAdmissionCandidate | None: ...


class AdmissionDenied(ValueError):
    """Raised when input is invalid or a specialist violates the result contract."""

    def __init__(self, message: str, *, denial: SpecialistResult | None = None) -> None:
        super().__init__(message)
        self.denial = denial


class Manager:
    """Validates a bounded handoff before invoking one static, named specialist."""

    def __init__(
        self,
        trace_store: TraceStore,
        specialists: Mapping[str, SpecialistHandler],
        research_admissions: ResearchAdmissionRegistry | None = None,
    ) -> None:
        expected_specialists = {role.value for role in SpecialistType}
        if set(specialists) != expected_specialists:
            raise ValueError("the Manager registry must contain exactly the four approved specialists")
        self._trace_store = trace_store
        self._specialists = dict(specialists)
        self._research_admissions = research_admissions

    def admit(self, raw_task: Mapping[str, Any]) -> SpecialistResult:
        """Validate, dispatch once, validate the result, then durably record it."""
        try:
            task = SpecialistTask.model_validate(raw_task)
            handler = self._specialists.get(task.specialist.value)
            if handler is None:
                raise AdmissionDenied("specialist is not registered")
            if task.specialist is SpecialistType.RESEARCH:
                self._require_active_research_admission(task, raw_task)
        except (ValidationError, AdmissionDenied) as error:
            if isinstance(error, AdmissionDenied) and error.denial is not None:
                raise
            self._trace_store.record(
                decision="denied", request=raw_task, error_class=type(error).__name__
            )
            raise AdmissionDenied("Manager denied the specialist task") from error

        try:
            result = SpecialistResult.model_validate(handler(task))
        except Exception as error:
            self._trace_store.record(
                decision="denied",
                request=raw_task,
                dispatch_id=task.dispatch_id,
                specialist=task.specialist.value,
                error_class="InvalidSpecialistResult",
            )
            raise AdmissionDenied("Manager denied an invalid specialist result") from error

        self._trace_store.record(
            decision="accepted",
            request=raw_task,
            dispatch_id=task.dispatch_id,
            specialist=task.specialist.value,
            result=result.model_dump(mode="json"),
        )
        return result

    def _require_active_research_admission(
        self, task: SpecialistTask, raw_task: Mapping[str, Any]
    ) -> None:
        fingerprint = task.research_model_fingerprint
        assert fingerprint is not None
        active = (
            self._research_admissions.active_admission_for(fingerprint)
            if self._research_admissions is not None
            else None
        )
        if (
            active is not None
            and active.state is AdmissionState.APPROVED
            and active.fingerprint == fingerprint
        ):
            return
        candidate = (
            self._research_admissions.candidate_for(fingerprint)
            if self._research_admissions is not None
            else None
        )
        evidence_refs = [] if candidate is None else [candidate.trial_evidence_ref]
        if candidate is not None and candidate.failure_evidence_ref is not None:
            evidence_refs.append(candidate.failure_evidence_ref)
        denial = SpecialistResult(
            status=TaskStatus.BLOCKED,
            summary="Research model admission is missing, inactive, or does not match the requested fingerprint.",
            next_manager_action="block_story",
            evidence_refs=evidence_refs,
        )
        self._trace_store.record(
            decision="denied",
            request=raw_task,
            dispatch_id=task.dispatch_id,
            specialist=task.specialist.value,
            result=denial.model_dump(mode="json"),
            error_class="InactiveResearchAdmission",
        )
        raise AdmissionDenied("Manager denied the Research task without an active admission", denial=denial)


def accepted_result(task: SpecialistTask) -> SpecialistResult:
    """A deterministic local handler for the admission slice and its tests."""
    return SpecialistResult(
        status=TaskStatus.COMPLETED,
        summary=f"Accepted bounded {task.specialist.value} work.",
        next_manager_action="record_handoff",
    )
