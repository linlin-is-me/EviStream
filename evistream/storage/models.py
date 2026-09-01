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
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    policy_id: Mapped[str] = mapped_column(String(128))
    policy_version: Mapped[int] = mapped_column(Integer)
    model_profile: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)


class RequirementRecord(TimestampMixin, Base):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("case_id", "requirement_key"),
        UniqueConstraint("id", "case_id"),
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


class RequirementResultRecord(TimestampMixin, Base):
    __tablename__ = "requirement_results"
    __table_args__ = (UniqueConstraint("id", "requirement_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    reason_code: Mapped[str] = mapped_column(String(64))
    aggregator_version: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))


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
