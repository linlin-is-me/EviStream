"""Stage 1 media persistence models."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))


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
