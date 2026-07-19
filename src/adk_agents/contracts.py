"""The bounded handoff contract shared by the Manager and specialists."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .research_admission import ResearchModelFingerprint


class SpecialistType(str, Enum):
    SCRUM_MASTER = "scrum_master"
    RESEARCH = "research"
    CODING = "coding"
    REVIEW = "review"


class TaskStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_REVISION = "needs_revision"
    BLOCKED = "blocked"
    FAILED = "failed"


class CodingAgentScope(BaseModel):
    """An explicit coding-only grant; all other specialists must omit it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    approved_paths: Annotated[list[str], Field(min_length=1, max_length=32)]
    approved_commands: Annotated[list[str], Field(min_length=1, max_length=16)]


class SpecialistTask(BaseModel):
    """Validated, bounded input to exactly one named specialist."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    control_issue_ref: Annotated[str, Field(pattern=r"^#[1-9][0-9]*$")]
    story_ref: Annotated[str, Field(pattern=r"^#[1-9][0-9]*$")]
    dispatch_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{7,127}$")]
    specialist: SpecialistType
    objective: Annotated[str, Field(min_length=1, max_length=1_000)]
    acceptance_criteria: Annotated[list[str], Field(min_length=1, max_length=20)]
    input_artifact_refs: Annotated[list[str], Field(max_length=32)] = Field(default_factory=list)
    requested_by: Annotated[str, Field(min_length=1, max_length=128)]
    deadline: datetime
    budget_steps: Annotated[int, Field(ge=1, le=20)]
    coding_agent_scope: CodingAgentScope | None = None
    research_model_fingerprint: ResearchModelFingerprint | None = None

    @field_validator("acceptance_criteria")
    @classmethod
    def criteria_are_bounded(cls, criteria: list[str]) -> list[str]:
        if any(not criterion or len(criterion) > 500 for criterion in criteria):
            raise ValueError("each acceptance criterion must be 1 to 500 characters")
        return criteria

    @field_validator("input_artifact_refs")
    @classmethod
    def artifact_refs_are_safe(cls, refs: list[str]) -> list[str]:
        if any(len(ref) > 256 or not ref.startswith("sha256:") for ref in refs):
            raise ValueError("artifact references must be sha256 digests")
        return refs

    @model_validator(mode="after")
    def scope_matches_specialist(self) -> "SpecialistTask":
        if self.specialist is SpecialistType.CODING and self.coding_agent_scope is None:
            raise ValueError("coding tasks require coding_agent_scope")
        if self.specialist is not SpecialistType.CODING and self.coding_agent_scope is not None:
            raise ValueError("only coding tasks may include coding_agent_scope")
        if self.specialist is SpecialistType.RESEARCH and self.research_model_fingerprint is None:
            raise ValueError("research tasks require research_model_fingerprint")
        if self.specialist is not SpecialistType.RESEARCH and self.research_model_fingerprint is not None:
            raise ValueError("only research tasks may include research_model_fingerprint")
        if self.deadline.tzinfo is None or self.deadline <= datetime.now(timezone.utc):
            raise ValueError("deadline must be a future timezone-aware timestamp")
        return self


class BoardUpdateRequest(BaseModel):
    """A Manager-reviewed proposal, not an adapter command or board mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    proposed_status: Annotated[str, Field(min_length=1, max_length=64)]
    rationale: Annotated[str, Field(min_length=1, max_length=500)]


class SpecialistResult(BaseModel):
    """A specialist's validated proposal; it never mutates the task board itself."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    status: TaskStatus
    summary: Annotated[str, Field(min_length=1, max_length=2_000)]
    next_manager_action: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_refs: Annotated[list[str], Field(max_length=32)] = Field(default_factory=list)
    artifact_refs: Annotated[list[str], Field(max_length=32)] = Field(default_factory=list)
    board_update_request: BoardUpdateRequest | None = None
    scope_gap: Annotated[str, Field(max_length=1_000)] | None = None
    escalation_reason: Annotated[str, Field(max_length=1_000)] | None = None

    @field_validator("evidence_refs", "artifact_refs")
    @classmethod
    def result_refs_are_safe(cls, refs: list[str]) -> list[str]:
        if any(len(ref) > 256 or not ref.startswith("sha256:") for ref in refs):
            raise ValueError("result references must be sha256 digests")
        return refs
