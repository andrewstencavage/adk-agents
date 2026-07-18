"""The root Manager admission boundary and named specialist dispatch seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from .contracts import SpecialistResult, SpecialistTask, SpecialistType, TaskStatus
from .routing import ModelRef, ModelRouter, NoEligibleModel, RouteRequest
from .trace import TraceStore

SpecialistHandler = Callable[[SpecialistTask], SpecialistResult]


class AdmissionDenied(ValueError):
    """Raised when input is invalid or a specialist violates the result contract."""


class Manager:
    """Validates a bounded handoff before invoking one static, named specialist."""

    def __init__(self, trace_store: TraceStore, specialists: Mapping[str, SpecialistHandler], *, router: ModelRouter | None = None, inventory: Callable[[], list[ModelRef]] | None = None) -> None:
        expected_specialists = {role.value for role in SpecialistType}
        if set(specialists) != expected_specialists:
            raise ValueError("the Manager registry must contain exactly the four approved specialists")
        self._trace_store = trace_store
        self._specialists = dict(specialists)
        self._router = router
        self._inventory = inventory

    def admit(self, raw_task: Mapping[str, Any]) -> SpecialistResult:
        """Validate, dispatch once, validate the result, then durably record it."""
        try:
            task = SpecialistTask.model_validate(raw_task)
            handler = self._specialists.get(task.specialist.value)
            if handler is None:
                raise AdmissionDenied("specialist is not registered")
        except (ValidationError, AdmissionDenied) as error:
            self._trace_store.record(
                decision="denied", request=raw_task, error_class=type(error).__name__
            )
            raise AdmissionDenied("Manager denied the specialist task") from error

        if self._router is not None:
            try:
                self._router.select(
                    RouteRequest(dispatch_id=task.dispatch_id, role=task.specialist),
                    self._inventory() if self._inventory is not None else [],
                )
            except NoEligibleModel as error:
                result = SpecialistResult(status=TaskStatus.BLOCKED, summary=str(error), next_manager_action="create_blocked_story", evidence_refs=[error.evidence_ref])
                self._trace_store.record(decision="accepted", request=raw_task, dispatch_id=task.dispatch_id, specialist=task.specialist.value, result=result.model_dump(mode="json"))
                return result

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


def accepted_result(task: SpecialistTask) -> SpecialistResult:
    """A deterministic local handler for the admission slice and its tests."""
    return SpecialistResult(
        status=TaskStatus.COMPLETED,
        summary=f"Accepted bounded {task.specialist.value} work.",
        next_manager_action="record_handoff",
    )
