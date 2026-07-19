"""Human-approved admission records for the bounded Research role."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _uuid7() -> str:
    milliseconds = int(time.time() * 1_000)
    value = (milliseconds << 80) | (0x7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    return str(UUID(int=value))


class AdmissionState(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    FAILED = "failed"


class ResearchModelFingerprint(BaseModel):
    """The exact non-secret local configuration authorized for Research."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    runtime: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    model_artifact: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_config: dict[str, str] = Field(default_factory=dict)

    @field_validator("runtime_config")
    @classmethod
    def configuration_is_bounded_and_non_secret(cls, value: dict[str, str]) -> dict[str, str]:
        allowed_settings = {"temperature", "top_p", "max_tokens", "context_window", "seed"}
        if len(value) > 32 or any(
            not key or not item or len(key) > 128 or len(item) > 512
            for key, item in value.items()
        ):
            raise ValueError("runtime_config must contain at most 32 bounded non-empty entries")
        if not set(value).issubset(allowed_settings) or any(
            re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", item) is None for item in value.values()
        ):
            raise ValueError("runtime_config permits only non-secret numeric model settings")
        return value


class HumanApproval(BaseModel):
    """A human approval observed from the GitHub control surface."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    actor: str = Field(pattern=r"^human:[^\s]+$")
    evidence_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ResearchTrialSummary(BaseModel):
    """Count-only trial evidence safe to publish on the GitHub control surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal["passed", "failed"]
    cited_claim_count: int = Field(ge=0, le=1_000)
    unsupported_claim_count: int = Field(ge=0, le=1_000)
    typed_tool_call_count: int = Field(ge=0, le=1_000)


class ResearchAdmissionCandidate(BaseModel):
    """An operator-visible candidate with immutable redacted evidence references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    control_issue_ref: str = Field(pattern=r"^#[1-9][0-9]*$")
    publication_ref: str = Field(min_length=1, max_length=256)
    fingerprint: ResearchModelFingerprint
    state: AdmissionState
    trial_evidence_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    trial_evidence_summary: ResearchTrialSummary
    approval_evidence_ref: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    failure_evidence_ref: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    approved_by: str | None = Field(default=None, pattern=r"^human:[^\s]+$")


class AdmissionControlSurface(Protocol):
    """Typed GitHub control-surface operations for a Research admission."""

    def publish_candidate(self, candidate: ResearchAdmissionCandidate) -> str: ...
    def approval_for(self, candidate: ResearchAdmissionCandidate) -> HumanApproval | None: ...


class ResearchAdmissionService:
    """The public lifecycle boundary for human-approved Research admission."""

    _MIGRATION_VERSION = 1

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def nominate(
        self,
        fingerprint: ResearchModelFingerprint,
        *,
        trial_evidence_ref: str,
        trial_evidence_summary: ResearchTrialSummary,
        control_issue_ref: str,
        control_surface: AdmissionControlSurface,
    ) -> ResearchAdmissionCandidate:
        """Publish a redacted candidate for human review, then persist its record."""
        draft = ResearchAdmissionCandidate(
            candidate_id=_uuid7(),
            control_issue_ref=control_issue_ref,
            publication_ref="pending-publication",
            fingerprint=fingerprint,
            state=AdmissionState.PENDING_APPROVAL,
            trial_evidence_ref=trial_evidence_ref,
            trial_evidence_summary=trial_evidence_summary,
        )
        candidate_data = draft.model_dump()
        candidate_data["publication_ref"] = control_surface.publish_candidate(draft)
        candidate = ResearchAdmissionCandidate(**candidate_data)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_admission_candidate
                   (candidate_id, control_issue_ref, publication_ref, runtime, model, model_artifact, runtime_config,
                    state, trial_evidence_ref, trial_evidence_summary, approval_evidence_ref, failure_evidence_ref, approved_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*self._values(candidate), now, now),
            )
        return candidate

    def approve_from_control_surface(
        self, candidate_id: str, control_surface: AdmissionControlSurface
    ) -> ResearchAdmissionCandidate:
        """Activate a pending candidate only from observed human GitHub approval."""
        candidate = self._candidate(candidate_id)
        approval = control_surface.approval_for(candidate)
        if approval is None:
            raise ValueError("Research admission candidate has no human approval")
        approved = self._transition(candidate, AdmissionState.APPROVED, approval=approval)
        return approved

    def fail(self, candidate_id: str, *, failure_evidence_ref: str) -> ResearchAdmissionCandidate:
        """Record failed trial evidence; failed candidates cannot become active."""
        candidate = self._candidate(candidate_id)
        failure = ResearchAdmissionCandidate(
            candidate_id=candidate.candidate_id,
            control_issue_ref=candidate.control_issue_ref,
            publication_ref=candidate.publication_ref,
            fingerprint=candidate.fingerprint,
            state=AdmissionState.FAILED,
            trial_evidence_ref=candidate.trial_evidence_ref,
            trial_evidence_summary=candidate.trial_evidence_summary,
            failure_evidence_ref=failure_evidence_ref,
        )
        return self._persist_pending_transition(failure)

    def active_admission_for(
        self, fingerprint: ResearchModelFingerprint
    ) -> ResearchAdmissionCandidate | None:
        """Return an active admission only for the exact approved fingerprint."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM research_admission_candidate
                   WHERE runtime = ? AND model = ? AND model_artifact = ? AND runtime_config = ?
                     AND state = 'approved'""",
                self._fingerprint_values(fingerprint),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def _transition(
        self,
        candidate: ResearchAdmissionCandidate,
        state: AdmissionState,
        *,
        approval: HumanApproval,
    ) -> ResearchAdmissionCandidate:
        if candidate.state is not AdmissionState.PENDING_APPROVAL:
            raise ValueError("only a pending Research admission candidate can transition")
        approved = ResearchAdmissionCandidate(
            candidate_id=candidate.candidate_id,
            control_issue_ref=candidate.control_issue_ref,
            publication_ref=candidate.publication_ref,
            fingerprint=candidate.fingerprint,
            state=state,
            trial_evidence_ref=candidate.trial_evidence_ref,
            trial_evidence_summary=candidate.trial_evidence_summary,
            approval_evidence_ref=approval.evidence_ref,
            approved_by=approval.actor,
        )
        return self._persist_pending_transition(approved)

    def _persist_pending_transition(self, candidate: ResearchAdmissionCandidate) -> ResearchAdmissionCandidate:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE research_admission_candidate
                   SET state = ?, approval_evidence_ref = ?, failure_evidence_ref = ?, approved_by = ?, updated_at = ?
                   WHERE candidate_id = ? AND state = 'pending_approval'""",
                (
                    candidate.state.value,
                    candidate.approval_evidence_ref,
                    candidate.failure_evidence_ref,
                    candidate.approved_by,
                    datetime.now(timezone.utc).isoformat(),
                    candidate.candidate_id,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError("Research admission candidate is no longer pending approval")
        return candidate

    def _candidate(self, candidate_id: str) -> ResearchAdmissionCandidate:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_admission_candidate WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Research admission candidate does not exist")
        return self._from_row(row)

    @staticmethod
    def _fingerprint_values(fingerprint: ResearchModelFingerprint) -> tuple[str, str, str, str]:
        return (
            fingerprint.runtime,
            fingerprint.model,
            fingerprint.model_artifact,
            json.dumps(fingerprint.runtime_config, sort_keys=True, separators=(",", ":")),
        )

    def _values(self, candidate: ResearchAdmissionCandidate) -> tuple[Any, ...]:
        return (
            candidate.candidate_id,
            candidate.control_issue_ref,
            candidate.publication_ref,
            *self._fingerprint_values(candidate.fingerprint),
            candidate.state.value,
            candidate.trial_evidence_ref,
            json.dumps(candidate.trial_evidence_summary.model_dump(), sort_keys=True, separators=(",", ":")),
            candidate.approval_evidence_ref,
            candidate.failure_evidence_ref,
            candidate.approved_by,
        )

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS operational_migration "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                migration_exists = connection.execute(
                    "SELECT 1 FROM operational_migration WHERE version = ?",
                    (self._MIGRATION_VERSION,),
                ).fetchone()
                if migration_exists:
                    connection.commit()
                    return
                connection.execute(
                    """CREATE TABLE research_admission_candidate (
                        candidate_id TEXT PRIMARY KEY,
                        control_issue_ref TEXT NOT NULL,
                        publication_ref TEXT NOT NULL,
                        runtime TEXT NOT NULL,
                        model TEXT NOT NULL,
                        model_artifact TEXT NOT NULL,
                        runtime_config TEXT NOT NULL,
                        state TEXT NOT NULL CHECK(state IN ('pending_approval', 'approved', 'failed')),
                        trial_evidence_ref TEXT NOT NULL,
                        trial_evidence_summary TEXT NOT NULL,
                        approval_evidence_ref TEXT,
                        failure_evidence_ref TEXT,
                        approved_by TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO operational_migration(version, applied_at) VALUES (?, ?)",
                    (self._MIGRATION_VERSION, datetime.now(timezone.utc).isoformat()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ResearchAdmissionCandidate:
        return ResearchAdmissionCandidate(
            candidate_id=row["candidate_id"],
            control_issue_ref=row["control_issue_ref"],
            publication_ref=row["publication_ref"],
            fingerprint=ResearchModelFingerprint(
                runtime=row["runtime"],
                model=row["model"],
                model_artifact=row["model_artifact"],
                runtime_config=json.loads(row["runtime_config"]),
            ),
            state=AdmissionState(row["state"]),
            trial_evidence_ref=row["trial_evidence_ref"],
            trial_evidence_summary=ResearchTrialSummary.model_validate_json(row["trial_evidence_summary"]),
            approval_evidence_ref=row["approval_evidence_ref"],
            failure_evidence_ref=row["failure_evidence_ref"],
            approved_by=row["approved_by"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
