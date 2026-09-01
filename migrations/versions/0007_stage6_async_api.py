"""Add Stage 6 async job metadata and automatic video triage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_stage6_async_api"
down_revision: str | None = "0006_stage5_governance_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("model_profile", sa.String(128), nullable=True))
    op.add_column("videos", sa.Column("triage_status", sa.String(32), nullable=True))
    op.execute("UPDATE videos SET model_profile = 'mock', triage_status = 'SUCCEEDED'")
    op.alter_column("videos", "model_profile", nullable=False, server_default="mock")
    op.alter_column("videos", "triage_status", nullable=False, server_default="PENDING")
    op.create_index("ix_videos_triage_status", "videos", ["triage_status"])
    op.create_check_constraint(
        "ck_videos_triage_status",
        "videos",
        "triage_status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
    )

    op.add_column("processing_jobs", sa.Column("payload", sa.JSON(), nullable=True))
    op.add_column("processing_jobs", sa.Column("error_message", sa.Text()))
    op.add_column(
        "processing_jobs",
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("processing_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("processing_jobs", sa.Column("last_enqueued_at", sa.DateTime(timezone=True)))
    op.execute(
        """
        UPDATE processing_jobs j SET payload = CASE
          WHEN j.type = 'MEDIA_PREPROCESS' THEN json_build_object(
            'video_id', j.subject_id,
            'model_profile', COALESCE((
              SELECT v.model_profile FROM videos v WHERE v.id = j.subject_id
            ), 'mock')
          )
          WHEN j.type = 'AGENT_INVESTIGATION' THEN COALESCE((
            SELECT json_build_object(
              'run_id', a.id, 'case_id', a.case_id, 'model_profile', a.model_profile
            )
            FROM agent_runs a WHERE a.job_id = j.id
          ), '{}'::json)
          WHEN j.type = 'POLICY_REPLAY' THEN COALESCE((
            SELECT json_build_object('replay_job_id', r.id, 'model_profile', r.model_profile)
            FROM replay_jobs r WHERE r.processing_job_id = j.id
          ), '{}'::json)
          ELSE '{}'::json
        END
        """
    )
    op.alter_column("processing_jobs", "payload", nullable=False, server_default="{}")
    op.create_check_constraint(
        "ck_processing_jobs_status",
        "processing_jobs",
        "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'RETRY_WAIT', 'FAILED', 'CANCELLED')",
    )
    op.create_check_constraint(
        "ck_processing_jobs_attempts",
        "processing_jobs",
        "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts",
    )

    op.drop_constraint("fk_model_calls_agent_run_case", "model_calls", type_="foreignkey")
    op.drop_index("uq_model_calls_run_request", table_name="model_calls")
    op.alter_column("model_calls", "run_id", nullable=True)
    op.alter_column("model_calls", "case_id", nullable=True)
    op.add_column("model_calls", sa.Column("video_id", sa.String(64)))
    op.create_foreign_key(
        "fk_model_calls_agent_run_case",
        "model_calls",
        "agent_runs",
        ["run_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_model_calls_video",
        "model_calls",
        "videos",
        ["video_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_model_calls_video_id", "model_calls", ["video_id"])
    op.create_index(
        "uq_model_calls_run_request",
        "model_calls",
        ["run_id", "request_key"],
        unique=True,
    )
    op.create_index(
        "uq_model_calls_video_request",
        "model_calls",
        ["video_id", "request_key"],
        unique=True,
        postgresql_where=sa.text("video_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_model_calls_scope",
        "model_calls",
        "(run_id IS NOT NULL AND case_id IS NOT NULL AND video_id IS NULL) OR "
        "(run_id IS NULL AND case_id IS NULL AND video_id IS NOT NULL)",
    )

    op.create_table(
        "video_triage_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "video_id",
            sa.String(64),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("model_profile", sa.String(128), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("matched_terms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("matched_requirement_keys", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text()),
        sa.Column("case_id", sa.String(64), sa.ForeignKey("cases.id", ondelete="RESTRICT")),
        sa.Column(
            "model_call_id",
            sa.String(64),
            sa.ForeignKey("model_calls.id", ondelete="RESTRICT"),
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id", "policy_version"],
            ["policies.policy_id", "policies.version"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("video_id", "policy_id", "policy_version"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'RETRY_WAIT', 'FAILED')",
            name="ck_video_triage_checks_status",
        ),
        sa.CheckConstraint(
            "action IS NULL OR action IN ('SKIP', 'CREATE_CASE', 'NEEDS_HUMAN_REVIEW')",
            name="ck_video_triage_checks_action",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_video_triage_checks_confidence",
        ),
    )
    for column in ["job_id", "video_id", "status", "case_id", "model_call_id"]:
        op.create_index(f"ix_video_triage_checks_{column}", "video_triage_checks", [column])


def downgrade() -> None:
    op.drop_table("video_triage_checks")
    op.drop_constraint("ck_model_calls_scope", "model_calls", type_="check")
    op.drop_index("uq_model_calls_video_request", table_name="model_calls")
    op.drop_index("uq_model_calls_run_request", table_name="model_calls")
    op.drop_index("ix_model_calls_video_id", table_name="model_calls")
    op.drop_constraint("fk_model_calls_video", "model_calls", type_="foreignkey")
    op.drop_constraint("fk_model_calls_agent_run_case", "model_calls", type_="foreignkey")
    op.drop_column("model_calls", "video_id")
    op.alter_column("model_calls", "case_id", nullable=False)
    op.alter_column("model_calls", "run_id", nullable=False)
    op.create_foreign_key(
        "fk_model_calls_agent_run_case",
        "model_calls",
        "agent_runs",
        ["run_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_model_calls_run_request",
        "model_calls",
        ["run_id", "request_key"],
        unique=True,
    )
    op.drop_constraint("ck_processing_jobs_attempts", "processing_jobs", type_="check")
    op.drop_constraint("ck_processing_jobs_status", "processing_jobs", type_="check")
    for column in [
        "last_enqueued_at",
        "next_attempt_at",
        "payload",
        "retryable",
        "error_message",
    ]:
        op.drop_column("processing_jobs", column)
    op.drop_constraint("ck_videos_triage_status", "videos", type_="check")
    op.drop_index("ix_videos_triage_status", table_name="videos")
    op.drop_column("videos", "triage_status")
    op.drop_column("videos", "model_profile")
