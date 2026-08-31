"""Stage 2 moderation entities independent from storage and transport frameworks."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyLifecycle(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CaseStatus(StrEnum):
    READY = "READY"
    INVESTIGATING = "INVESTIGATING"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    DECIDED = "DECIDED"
    CANCELLED = "CANCELLED"


class RequirementStatus(StrEnum):
    PENDING = "PENDING"
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"


class EvidenceStance(StrEnum):
    SUPPORT = "support"
    CONTRADICT = "contradict"
    NEUTRAL = "neutral"
    UNCERTAIN = "uncertain"


class ToolRunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class DecisionSource(StrEnum):
    MACHINE = "MACHINE"
    HUMAN = "HUMAN"


class Policy(DomainModel):
    policy_id: str = Field(min_length=3, max_length=128)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)
    severity: Severity
    enabled: bool
    lifecycle: PolicyLifecycle
    source_yaml: str
    compiled: dict[str, Any]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_version: str
    created_at: datetime
    updated_at: datetime


class Case(DomainModel):
    case_id: str = Field(min_length=1, max_length=64)
    video_id: str = Field(min_length=1, max_length=64)
    policy_id: str = Field(min_length=3, max_length=128)
    policy_version: int = Field(ge=1)
    model_profile: str = Field(min_length=1, max_length=128)
    status: CaseStatus = CaseStatus.READY
    created_at: datetime
    updated_at: datetime


class Requirement(DomainModel):
    requirement_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    requirement_key: str = Field(min_length=1, max_length=128)
    requirement_type: str = Field(min_length=1, max_length=64)
    source_kind: str = Field(pattern=r"^(requirement|exception)$")
    required: bool
    description: str = Field(min_length=1)
    suggested_queries: list[str]
    modalities: list[str]
    tool_capabilities: list[str]
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RequirementStatus = RequirementStatus.PENDING
    created_at: datetime
    updated_at: datetime


class ToolRun(DomainModel):
    tool_run_id: str = Field(min_length=1, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    requirement_id: str | None = Field(default=None, max_length=64)
    correlation_id: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=128)
    request_key: str = Field(min_length=1, max_length=64)
    request: dict[str, Any]
    response: dict[str, Any] | None = None
    status: ToolRunStatus
    latency_ms: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=64)
    created_at: datetime
    updated_at: datetime


class Evidence(DomainModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    requirement_id: str = Field(min_length=1, max_length=64)
    stance: EvidenceStance
    modality: str = Field(min_length=1, max_length=32)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    artifact_id: str | None = Field(default=None, max_length=64)
    tool_run_id: str | None = Field(default=None, max_length=64)
    model_call_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=255)
    source_ref: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> "Evidence":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class RequirementResult(DomainModel):
    result_id: str = Field(min_length=1, max_length=64)
    requirement_id: str = Field(min_length=1, max_length=64)
    status: RequirementStatus
    evidence_ids: list[str]
    reason_code: str = Field(min_length=1, max_length=64)
    aggregator_version: str = Field(min_length=1, max_length=64)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def reject_pending(self) -> "RequirementResult":
        if self.status is RequirementStatus.PENDING:
            raise ValueError("RequirementResult cannot be PENDING")
        return self


class Decision(DomainModel):
    decision_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    policy_id: str = Field(min_length=3, max_length=128)
    policy_version: int = Field(ge=1)
    verdict: Verdict
    reason_code: str = Field(min_length=1, max_length=64)
    source: DecisionSource
    explanation: str = ""
    evidence_ids: list[str]
    decision_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
