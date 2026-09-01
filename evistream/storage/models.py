"""Stage 1 media persistence models."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from evistream.storage.database import Base, utc_now


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class VideoRecord(TimestampMixin, Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255))
    artifact_uri: Mapped[str] = mapped_column(Text, unique=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    duration_ms: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    container: Mapped[str] = mapped_column(String(64))
    video_codec: Mapped[str] = mapped_column(String(64))
    has_audio: Mapped[bool]
    audio_codec: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    model_profile: Mapped[str] = mapped_column(String(128), default="mock")
    triage_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)


class SegmentRecord(TimestampMixin, Base):
    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("video_id", "start_ms", "end_ms"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    sequence: Mapped[int] = mapped_column(Integer)


class ArtifactRecord(TimestampMixin, Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[str | None] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32), index=True)
    uri: Mapped[str] = mapped_column(Text, unique=True)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SearchDocumentRecord(TimestampMixin, Base):
    __tablename__ = "search_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[str | None] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"))
    modality: Mapped[str] = mapped_column(String(32), index=True)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    keyword_lexemes: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', keyword_lexemes)", persisted=True),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_space: Mapped[str | None] = mapped_column(String(64), index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    embedding_source_sha256: Mapped[str | None] = mapped_column(String(64))
    embedding_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingJobRecord(TimestampMixin, Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyRecord(TimestampMixin, Base):
    __tablename__ = "policies"

    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool]
    lifecycle: Mapped[str] = mapped_column(String(16), index=True)
    source_yaml: Mapped[str] = mapped_column(Text)
    compiled_policy: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_sha256: Mapped[str] = mapped_column(String(64))
    semantic_sha256: Mapped[str] = mapped_column(String(64))
    compiler_version: Mapped[str] = mapped_column(String(32))


class CaseRecord(TimestampMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "policy_version"],
            ["policies.policy_id", "policies.version"],
        ),
        UniqueConstraint("video_id", "policy_id", "policy_version"),
        UniqueConstraint("id", "policy_id", "policy_version"),
        ForeignKeyConstraint(
            ["current_decision_id", "id"],
            ["decisions.id", "decisions.case_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    policy_id: Mapped[str] = mapped_column(String(128))
    policy_version: Mapped[int] = mapped_column(Integer)
    model_profile: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_decision_id: Mapped[str | None] = mapped_column(String(64))


class RequirementRecord(TimestampMixin, Base):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("case_id", "requirement_key"),
        UniqueConstraint("id", "case_id"),
        ForeignKeyConstraint(
            ["current_result_id", "id"],
            ["requirement_results.id", "requirement_results.requirement_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    requirement_key: Mapped[str] = mapped_column(String(128))
    requirement_type: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(16))
    required: Mapped[bool]
    description: Mapped[str] = mapped_column(Text)
    suggested_queries: Mapped[list[str]] = mapped_column(JSON)
    modalities: Mapped[list[str]] = mapped_column(JSON)
    tool_capabilities: Mapped[list[str]] = mapped_column(JSON)
    semantic_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_result_id: Mapped[str | None] = mapped_column(String(64))


class ToolRunRecord(TimestampMixin, Base):
    __tablename__ = "tool_runs"
    __table_args__ = (
        Index(
            "uq_tool_runs_run_request",
            "run_id",
            "request_key",
            unique=True,
            postgresql_where=text("run_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["requirement_id", "case_id"],
            ["requirements.id", "requirements.case_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "case_id"],
            ["agent_runs.id", "agent_runs.case_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[str | None] = mapped_column(String(64), index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    request_key: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))


class EvidenceRecord(TimestampMixin, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["requirement_id", "case_id"],
            ["requirements.id", "requirements.case_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_evidence_time"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_evidence_confidence",
        ),
        UniqueConstraint("id", "requirement_id"),
        UniqueConstraint("id", "case_id"),
        ForeignKeyConstraint(
            ["model_call_id", "case_id"],
            ["model_calls.id", "model_calls.case_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    requirement_id: Mapped[str] = mapped_column(String(64), index=True)
    stance: Mapped[str] = mapped_column(String(16))
    modality: Mapped[str] = mapped_column(String(32))
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    tool_run_id: Mapped[str | None] = mapped_column(ForeignKey("tool_runs.id", ondelete="SET NULL"))
    model_call_id: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(255))
    source_ref: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None]
    origin_evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT")
    )
    replay_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("replay_items.id", ondelete="RESTRICT"), index=True
    )


class RequirementResultRecord(TimestampMixin, Base):
    __tablename__ = "requirement_results"
    __table_args__ = (
        UniqueConstraint("id", "requirement_id"),
        UniqueConstraint("id", "case_id"),
        UniqueConstraint("requirement_id", "sequence"),
        UniqueConstraint("requirement_id", "aggregator_version", "input_sha256"),
        ForeignKeyConstraint(
            ["requirement_id", "case_id"],
            ["requirements.id", "requirements.case_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    reason_code: Mapped[str] = mapped_column(String(64))
    aggregator_version: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    aggregation_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    origin_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("requirement_results.id", ondelete="RESTRICT")
    )
    replay_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("replay_items.id", ondelete="RESTRICT"), index=True
    )


class RequirementResultEvidenceRecord(Base):
    __tablename__ = "requirement_result_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["result_id", "requirement_id"],
            ["requirement_results.id", "requirement_results.requirement_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "requirement_id"],
            ["evidence.id", "evidence.requirement_id"],
            ondelete="RESTRICT",
        ),
    )

    result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(String(64))


class DecisionRecord(TimestampMixin, Base):
    __tablename__ = "decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "policy_id", "policy_version"],
            ["cases.id", "cases.policy_id", "cases.policy_version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "case_id"),
        UniqueConstraint("case_id", "sequence"),
        UniqueConstraint("case_id", "source", "evaluator_version", "input_sha256"),
        ForeignKeyConstraint(
            ["agent_run_id", "case_id"],
            ["agent_runs.id", "agent_runs.case_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    policy_id: Mapped[str] = mapped_column(String(128))
    policy_version: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    reason_code: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    explanation: Mapped[str] = mapped_column(Text, default="")
    decision_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sequence: Mapped[int] = mapped_column(Integer)
    evaluator_version: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    agent_run_id: Mapped[str | None] = mapped_column(String(64))
    supersedes_decision_id: Mapped[str | None] = mapped_column(String(64))
    replay_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("replay_items.id", ondelete="RESTRICT"), index=True
    )


class DecisionEvidenceRecord(Base):
    __tablename__ = "decision_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "case_id"],
            ["evidence.id", "evidence.case_id"],
            ondelete="RESTRICT",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64))


class AgentRunRecord(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("id", "case_id"),
        CheckConstraint(
            "run_kind IN ('INVESTIGATION', 'MANUAL_TOOL')",
            name="ck_agent_runs_kind",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', "
            "'NEEDS_HUMAN_REVIEW', 'FAILED', 'CANCELLED')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "run_kind = 'MANUAL_TOOL' OR job_id IS NOT NULL",
            name="ck_agent_runs_investigation_job",
        ),
        Index(
            "uq_agent_runs_job_id",
            "job_id",
            unique=True,
            postgresql_where=text("job_id IS NOT NULL"),
        ),
        Index(
            "uq_agent_runs_investigation_case",
            "case_id",
            unique=True,
            postgresql_where=text("run_kind = 'INVESTIGATION'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_kind: Mapped[str] = mapped_column(String(24), index=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"), index=True
    )
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="RESTRICT"), index=True)
    model_profile: Mapped[str] = mapped_column(String(128))
    current_node: Mapped[str | None] = mapped_column(String(24))
    next_node: Mapped[str | None] = mapped_column(String(24))
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    vlm_calls: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_tool_failures: Mapped[int] = mapped_column(Integer, default=0)
    total_tool_failures: Mapped[int] = mapped_column(Integer, default=0)
    stagnant_iterations: Mapped[int] = mapped_column(Integer, default=0)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provisional_verdict: Mapped[str | None] = mapped_column(String(32))
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scope_requirement_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class AgentStepRecord(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "state_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    node: Mapped[str] = mapped_column(String(24), index=True)
    iteration: Mapped[int] = mapped_column(Integer)
    state_version: Mapped[int] = mapped_column(Integer)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelCallRecord(TimestampMixin, Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        UniqueConstraint("id", "case_id"),
        Index(
            "uq_model_calls_run_request",
            "run_id",
            "request_key",
            unique=True,
        ),
        Index(
            "uq_model_calls_video_request",
            "video_id",
            "request_key",
            unique=True,
            postgresql_where=text("video_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["run_id", "case_id"],
            ["agent_runs.id", "agent_runs.case_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(run_id IS NOT NULL AND case_id IS NOT NULL AND video_id IS NULL) OR "
            "(run_id IS NULL AND case_id IS NULL AND video_id IS NOT NULL)",
            name="ck_model_calls_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    case_id: Mapped[str | None] = mapped_column(String(64), index=True)
    video_id: Mapped[str | None] = mapped_column(
        ForeignKey("videos.id", ondelete="RESTRICT"), index=True
    )
    node: Mapped[str] = mapped_column(String(24), index=True)
    state_version: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24))
    profile: Mapped[str] = mapped_column(String(128))
    requested_model: Mapped[str] = mapped_column(String(255))
    actual_model: Mapped[str | None] = mapped_column(String(255))
    request_key: Mapped[str] = mapped_column(String(64))
    request_summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))


class VideoTriageCheckRecord(TimestampMixin, Base):
    __tablename__ = "video_triage_checks"
    __table_args__ = (
        UniqueConstraint("video_id", "policy_id", "policy_version"),
        ForeignKeyConstraint(
            ["policy_id", "policy_version"],
            ["policies.policy_id", "policies.version"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'RETRY_WAIT', 'FAILED')",
            name="ck_video_triage_checks_status",
        ),
        CheckConstraint(
            "action IS NULL OR action IN ('SKIP', 'CREATE_CASE', 'NEEDS_HUMAN_REVIEW')",
            name="ck_video_triage_checks_action",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_video_triage_checks_confidence",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    policy_id: Mapped[str] = mapped_column(String(128))
    policy_version: Mapped[int] = mapped_column(Integer)
    model_profile: Mapped[str] = mapped_column(String(128))
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    matched_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    matched_requirement_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    model_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_calls.id", ondelete="RESTRICT"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))


class DecisionRequirementResultRecord(Base):
    __tablename__ = "decision_requirement_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["result_id", "case_id"],
            ["requirement_results.id", "requirement_results.case_id"],
            ondelete="RESTRICT",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64))


class ReviewRecord(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reviewed_decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    reviewer: Mapped[str] = mapped_column(String(128))
    reviewed_decision_id: Mapped[str | None] = mapped_column(String(64))
    decision_id: Mapped[str] = mapped_column(String(64), unique=True)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AppealRecord(TimestampMixin, Base):
    __tablename__ = "appeals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["challenged_decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('OPEN', 'RESOLVED')"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    submitter: Mapped[str] = mapped_column(String(128))
    statement: Mapped[str] = mapped_column(Text)
    challenged_decision_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    resolution_review_id: Mapped[str | None] = mapped_column(ForeignKey("reviews.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppealEventRecord(Base):
    __tablename__ = "appeal_events"
    __table_args__ = (
        UniqueConstraint("appeal_id", "sequence"),
        CheckConstraint("event_type IN ('SUBMITTED', 'RESOLVED')"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    appeal_id: Mapped[str] = mapped_column(
        ForeignKey("appeals.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text)
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("decisions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReplayJobRecord(TimestampMixin, Base):
    __tablename__ = "replay_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "source_version"],
            ["policies.policy_id", "policies.version"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["policy_id", "target_version"],
            ["policies.policy_id", "policies.version"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("mode IN ('REEVALUATE', 'REINVESTIGATE')"),
        CheckConstraint("model_change_policy IN ('keep', 'invalidate-visual')"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    processing_job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"), unique=True
    )
    policy_id: Mapped[str] = mapped_column(String(128), index=True)
    source_version: Mapped[int] = mapped_column(Integer)
    target_version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(24))
    preview_sha256: Mapped[str] = mapped_column(String(64))
    model_profile: Mapped[str | None] = mapped_column(String(128))
    model_change_policy: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ReplayItemRecord(TimestampMixin, Base):
    __tablename__ = "replay_items"
    __table_args__ = (
        UniqueConstraint("source_case_id", "target_policy_version"),
        CheckConstraint("mode IN ('REEVALUATE', 'REINVESTIGATE')"),
        CheckConstraint(
            "status IN ('PENDING', 'MATERIALIZED', 'INVESTIGATING', "
            "'COMPLETED', 'NEEDS_HUMAN_REVIEW', 'FAILED')"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    replay_job_id: Mapped[str] = mapped_column(
        ForeignKey("replay_jobs.id", ondelete="CASCADE"), index=True
    )
    source_case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    target_case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    target_policy_version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(32), index=True)
    plan_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="RESTRICT")
    )
    target_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="RESTRICT")
    )


class ReplayLineageRecord(Base):
    __tablename__ = "replay_lineage"
    __table_args__ = (
        UniqueConstraint(
            "replay_item_id",
            "entity_type",
            "action",
            "source_id",
            "target_id",
            "reason_code",
        ),
        CheckConstraint("action IN ('REUSED', 'INVALIDATED', 'RECREATED')"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    replay_item_id: Mapped[str] = mapped_column(
        ForeignKey("replay_items.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
