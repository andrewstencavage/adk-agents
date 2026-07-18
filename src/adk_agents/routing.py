"""Exact-fingerprint assessment storage and deterministic specialist routing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from .contracts import SpecialistType
from .evidence import EvidenceLedger
from .operational_record import OperationalRecord
from .ids import uuid7


class AssessmentStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True)
class ModelRef:
    runtime_id: str
    model_id: str
    fingerprint: str
    runtime_version: str


@dataclass(frozen=True)
class ModelAssessment:
    model: ModelRef
    role: SpecialistType
    suite_version: str
    status: AssessmentStatus
    score: float
    artifact_ref: str


@dataclass(frozen=True)
class RouteRequest:
    dispatch_id: str
    role: SpecialistType
    user_override: ModelRef | None = None


@dataclass(frozen=True)
class ModelSelection:
    model: ModelRef
    override_used: bool
    evidence_ref: str


class NoEligibleModel(RuntimeError):
    """A visible blocked decision, never a cloud or unassessed fallback."""

    def __init__(self, message: str, evidence_ref: str) -> None:
        super().__init__(message)
        self.evidence_ref = evidence_ref


class ModelRouter:
    def __init__(self, record: OperationalRecord, *, suite_version: str, max_assessment_age: timedelta = timedelta(days=30)) -> None:
        self._record, self._suite_version, self._max_assessment_age = record, suite_version, max_assessment_age
        self._ledger = EvidenceLedger(record)

    def record_assessment(self, assessment: ModelAssessment) -> None:
        with self._record.connection() as connection:
            connection.execute(
                "INSERT INTO model_assessment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid7(), assessment.suite_version, assessment.model.runtime_id, assessment.model.model_id, assessment.model.fingerprint, assessment.model.runtime_version, assessment.role.value, assessment.status.value, assessment.score, assessment.artifact_ref, _now()),
            )

    def select(self, request: RouteRequest, inventory: list[ModelRef] | None = None) -> ModelSelection:
        candidates = inventory or []
        eligible = [model for model in candidates if self._passes_current_suite(model, request.role)]
        if request.user_override is not None:
            if request.user_override not in eligible:
                evidence_ref = self._record_block(request, "ineligible override")
                raise NoEligibleModel("Blocked: the requested model override has no current passing assessment", evidence_ref)
            selected, override = request.user_override, True
        elif not eligible:
            evidence_ref = self._record_block(request, "no eligible exact-fingerprint assessment")
            raise NoEligibleModel("Blocked: no eligible model has a current passing assessment", evidence_ref)
        else:
            selected, override = sorted(eligible, key=lambda candidate: (-self._score(candidate, request.role), candidate.runtime_id, candidate.model_id, candidate.fingerprint))[0], False
        evidence_ref = _digest({"dispatch_id": request.dispatch_id, "role": request.role.value, "selected": selected, "override": override})
        with self._record.connection() as connection:
            connection.execute("INSERT INTO model_selection VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (uuid7(), request.dispatch_id, request.role.value, selected.runtime_id, selected.model_id, selected.fingerprint, int(override), "selected", evidence_ref, _now()))
        self._ledger.append(action_type="model_selected", input_value=request, output_value=selected, dispatch_id=request.dispatch_id, outcome_class="selected")
        return ModelSelection(selected, override, evidence_ref)

    def _passes_current_suite(self, model: ModelRef, role: SpecialistType) -> bool:
        row = self._latest_assessment(model, role)
        if row is None:
            return False
        completed_at = datetime.fromisoformat(row["completed_at"])
        return row["status"] == AssessmentStatus.PASSED.value and float(row["score"]) >= _ROLE_THRESHOLDS[role] and datetime.now(timezone.utc) - completed_at <= self._max_assessment_age

    def _score(self, model: ModelRef, role: SpecialistType) -> float:
        row = self._latest_assessment(model, role)
        return float(row["score"])

    def _latest_assessment(self, model: ModelRef, role: SpecialistType):
        with self._record.connection() as connection:
            return connection.execute("SELECT status, score, completed_at FROM model_assessment WHERE suite_version = ? AND runtime_id = ? AND model_id = ? AND fingerprint = ? AND runtime_version = ? AND role = ? ORDER BY completed_at DESC LIMIT 1", (self._suite_version, model.runtime_id, model.model_id, model.fingerprint, model.runtime_version, role.value)).fetchone()

    def _record_block(self, request: RouteRequest, reason: str) -> str:
        evidence_ref = _digest({"dispatch_id": request.dispatch_id, "role": request.role.value, "reason": reason})
        with self._record.connection() as connection:
            connection.execute("INSERT INTO model_selection VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (uuid7(), request.dispatch_id, request.role.value, None, None, None, 0, "blocked", evidence_ref, _now()))
        self._ledger.append(action_type="model_routing_blocked", input_value=request, dispatch_id=request.dispatch_id, outcome_class="blocked")
        return evidence_ref


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, default=lambda item: item.__dict__, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ROLE_THRESHOLDS = {
    SpecialistType.SCRUM_MASTER: 95,
    SpecialistType.RESEARCH: 85,
    SpecialistType.CODING: 80,
    SpecialistType.REVIEW: 85,
}
