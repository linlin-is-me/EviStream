"""Stage 1 media persistence baseline."""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0001_stage1_media"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "videos",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False, unique=True),
        sa.Column("fingerprint", sa.String(64)),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("container", sa.String(64), nullable=False),
        sa.Column("video_codec", sa.String(64), nullable=False),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("audio_codec", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("duration_ms >= 0"),
        sa.CheckConstraint("width > 0 AND height > 0"),
    )
    op.create_index("ix_videos_fingerprint", "videos", ["fingerprint"])
    op.create_index("ix_videos_status", "videos", ["status"])
    op.create_table(
        "segments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "video_id",
            sa.String(64),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms"),
        sa.UniqueConstraint("video_id", "start_ms", "end_ms"),
    )
    op.create_index("ix_segments_video_id", "segments", ["video_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "video_id",
            sa.String(64),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_id", sa.String(64), sa.ForeignKey("segments.id", ondelete="CASCADE")),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False, unique=True),
        sa.Column("artifact_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_video_id", "artifacts", ["video_id"])
    op.create_index("ix_artifacts_segment_id", "artifacts", ["segment_id"])
    op.create_index("ix_artifacts_type", "artifacts", ["type"])
    op.create_table(
        "search_documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "video_id",
            sa.String(64),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_id", sa.String(64), sa.ForeignKey("segments.id", ondelete="CASCADE")),
        sa.Column(
            "artifact_id",
            sa.String(64),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("modality", sa.String(32), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("start_ms >= 0 AND end_ms >= start_ms"),
    )
    op.create_index("ix_search_documents_video_id", "search_documents", ["video_id"])
    op.create_index("ix_search_documents_segment_id", "search_documents", ["segment_id"])
    op.create_index("ix_search_documents_modality", "search_documents", ["modality"])
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_processing_jobs_type", "processing_jobs", ["type"])
    op.create_index("ix_processing_jobs_subject_id", "processing_jobs", ["subject_id"])
    op.create_index("ix_processing_jobs_correlation_id", "processing_jobs", ["correlation_id"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])


def downgrade() -> None:
    for table in ["processing_jobs", "search_documents", "artifacts", "segments", "videos"]:
        op.drop_table(table)
