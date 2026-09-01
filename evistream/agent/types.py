"""Provider-neutral contracts for the Stage 4 investigation runtime."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evistream.domain import EvidenceStance, Verdict


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentNode(StrEnum):
    PLAN = "PLAN"
    RETRIEVE = "RETRIEVE"
    INSPECT = "INSPECT"
    VERIFY = "VERIFY"
    CHALLENGE = "CHALLENGE"
    DECIDE = "DECIDE"


class InvestigationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentRunKind(StrEnum):
    INVESTIGATION = "INVESTIGATION"
    MANUAL_TOOL = "MANUAL_TOOL"


class InvestigationRequirement(AgentModel):
    requirement_id: str = Field(min_length=1, max_length=64)
    requirement_key: str = Field(min_length=1, max_length=128)
    source_kind: Literal["requirement", "exception"]
    required: bool
    description: str = Field(min_length=1)
    suggested_queries: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    tool_capabilities: list[str] = Field(default_factory=list)


class Hypothesis(AgentModel):
    requirement_id: str = Field(min_length=1, max_length=64)
    statement: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)


class AgentAction(AgentModel):
    requirement_id: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=128)
    query: str = ""
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    limit: int = Field(default=5, ge=1, le=50)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "AgentAction":
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms and end_ms must be provided together")
        if (
            self.start_ms is not None
            and self.end_ms is not None
            and self.end_ms <= self.start_ms
        ):
            raise ValueError("end_ms must be greater than start_ms")
        return self


class PlanOutput(AgentModel):
    hypothesis: Hypothesis
    action: AgentAction


class InspectionObservation(AgentModel):
    source_ref: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1)
    visible_entities: list[str] = Field(default_factory=list)
    uncertainty: float = Field(default=0, ge=0, le=1)


class EvidenceDraft(AgentModel):
    source_ref: str = Field(min_length=1, max_length=255)
    stance: EvidenceStance
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    summary: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_range(self) -> "EvidenceDraft":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class VerificationOutput(AgentModel):
    evidence: list[EvidenceDraft] = Field(default_factory=list)


class ChallengeOutput(AgentModel):
    actions: list[AgentAction] = Field(default_factory=list, max_length=8)
    unresolved_exception: bool = False
    contradictory_evidence: bool = False
    continue_investigation: bool = False
    rationale: str = Field(min_length=1)


class ProvisionalDecision(AgentModel):
    verdict: Verdict
    reason_code: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class InvestigationState(AgentModel):
    run_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: int = Field(ge=1)
    model_profile: str = Field(min_length=1, max_length=128)
    requirements: list[InvestigationRequirement]
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    selected_items: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[InspectionObservation] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    missing_requirement_ids: list[str] = Field(default_factory=list)
    contradictory_requirement_ids: list[str] = Field(default_factory=list)
    pending_action: AgentAction | None = None
    iteration: int = Field(default=0, ge=0)
    vlm_calls: int = Field(default=0, ge=0)
    consecutive_tool_failures: int = Field(default=0, ge=0)
    total_tool_failures: int = Field(default=0, ge=0)
    stagnant_iterations: int = Field(default=0, ge=0)
    last_evidence_count: int = Field(default=0, ge=0)
    current_node: AgentNode = AgentNode.PLAN
    next_node: AgentNode | None = AgentNode.PLAN
    state_version: int = Field(default=0, ge=0)
    deadline_at: datetime
    last_checkpoint_at: datetime
    status: InvestigationStatus = InvestigationStatus.PENDING
    provisional_decision: ProvisionalDecision | None = None
    stop_reason: str | None = Field(default=None, max_length=64)


class InvestigationResult(AgentModel):
    run_id: str
    job_id: str | None = None
    case_id: str
    status: InvestigationStatus
    provisional_decision: ProvisionalDecision | None = None
    stop_reason: str | None = None
    state_version: int = Field(ge=0)
    node_count: int = Field(default=0, ge=0)
    tool_count: int = Field(default=0, ge=0)
    model_call_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
