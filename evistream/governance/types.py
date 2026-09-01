"""Public Stage 5 governance contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evistream.domain import RequirementStatus, Verdict


class GovernanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AggregationConfig(GovernanceModel):
    minimum_confidence: float = Field(default=0.6, ge=0, le=1)
    minimum_supporting_evidence: int = Field(default=1, ge=1, le=20)
    minimum_contradicting_evidence: int = Field(default=1, ge=1, le=20)


class AggregationOutcome(GovernanceModel):
    result_id: str
    requirement_id: str
    requirement_key: str
    status: RequirementStatus
    reason_code: str
    evidence_ids: list[str]
    valid_evidence_ids: list[str]
    ignored_evidence_ids: list[str]
    aggregator_version: str
    input_sha256: str


class RuleTruthValue(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class RuleEvaluation(GovernanceModel):
    verdict: Verdict
    reason_code: str
    explanation: str
    requirement_result_ids: list[str]
    evidence_ids: list[str]
    evaluator_version: str
    input_sha256: str


class AppealStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class Review(GovernanceModel):
    review_id: str
    case_id: str
    reviewer: str
    reviewed_decision_id: str | None = None
    decision_id: str
    note: str
    request_key: str
    created_at: datetime


class Appeal(GovernanceModel):
    appeal_id: str
    case_id: str
    submitter: str
    statement: str
    challenged_decision_id: str
    status: AppealStatus
    resolution_review_id: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class AppealEvent(GovernanceModel):
    event_id: str
    appeal_id: str
    sequence: int = Field(ge=1)
    event_type: str
    actor: str
    note: str
    decision_id: str | None = None
    created_at: datetime


class ReplayMode(StrEnum):
    REEVALUATE = "REEVALUATE"
    REINVESTIGATE = "REINVESTIGATE"


class ReplayItemStatus(StrEnum):
    PENDING = "PENDING"
    MATERIALIZED = "MATERIALIZED"
    INVESTIGATING = "INVESTIGATING"
    COMPLETED = "COMPLETED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    FAILED = "FAILED"


class ReplayLineageAction(StrEnum):
    REUSED = "REUSED"
    INVALIDATED = "INVALIDATED"
    RECREATED = "RECREATED"


class PolicyDiff(GovernanceModel):
    policy_id: str
    source_version: int
    target_version: int
    mode: ReplayMode
    unchanged_requirement_keys: list[str]
    added_requirement_keys: list[str]
    removed_requirement_keys: list[str]
    modified_requirement_keys: list[str]
    aggregation_changed: bool
    evaluator_changed: bool


class ReplayCasePlan(GovernanceModel):
    source_case_id: str
    source_decision_id: str | None = None
    mode: ReplayMode
    reusable_requirement_keys: list[str]
    investigate_requirement_keys: list[str]
    invalidations: list[dict[str, str]] = Field(default_factory=list)
    blocked_reason: str | None = None


class ReplayPreview(GovernanceModel):
    policy_id: str
    source_version: int
    target_version: int
    mode: ReplayMode
    cases: list[ReplayCasePlan]
    affected_case_count: int = Field(ge=0)
    reusable_evidence_count: int = Field(ge=0)
    reusable_result_count: int = Field(ge=0)
    estimated_investigation_count: int = Field(ge=0)
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReplayResult(GovernanceModel):
    replay_job_id: str
    processing_job_id: str
    status: str
    mode: ReplayMode
    completed_items: int = Field(ge=0)
    human_review_items: int = Field(ge=0)
    failed_items: int = Field(ge=0)
    decision_changes: list[dict[str, Any]] = Field(default_factory=list)
