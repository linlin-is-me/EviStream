"""Stage 2 moderation domain and policy versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_stage2_domain"
down_revision: str | None = "0001_stage1_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("policy_id", sa.String(128), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("lifecycle", sa.String(16), nullable=False),
        sa.Column("source_yaml", sa.Text(), nullable=False),
        sa.Column("compiled_policy", sa.JSON(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("semantic_sha256", sa.String(64), nullable=False),
        sa.Column("compiler_version", sa.String(32), nullable=False),
        *timestamps(),
        sa.CheckConstraint("version >= 1"),
    )
    op.create_index("ix_policies_lifecycle", "policies", ["lifecycle"])
    op.create_table(
        "cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "video_id",
            sa.String(64),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("model_profile", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["policy_id", "policy_version"], ["policies.policy_id", "policies.version"]
        ),
        sa.UniqueConstraint("video_id", "policy_id", "policy_version"),
    )
    op.create_index("ix_cases_video_id", "cases", ["video_id"])
    op.create_index("ix_cases_status", "cases", ["status"])
    op.create_table(
        "requirements",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "case_id", sa.String(64), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("requirement_key", sa.String(128), nullable=False),
        sa.Column("requirement_type", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("suggested_queries", sa.JSON(), nullable=False),
        sa.Column("modalities", sa.JSON(), nullable=False),
        sa.Column("tool_capabilities", sa.JSON(), nullable=False),
        sa.Column("semantic_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("case_id", "requirement_key"),
        sa.UniqueConstraint("id", "case_id"),
    )
    op.create_index("ix_requirements_case_id", "requirements", ["case_id"])
    op.create_index("ix_requirements_status", "requirements", ["status"])
    op.create_table(
        "tool_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64)),
        sa.Column(
            "case_id", sa.String(64), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "requirement_id", sa.String(64), sa.ForeignKey("requirements.id", ondelete="SET NULL")
        ),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 6), nullable=False),
        sa.Column("error_code", sa.String(64)),
        *timestamps(),
        sa.CheckConstraint("latency_ms >= 0"),
        sa.CheckConstraint("estimated_cost >= 0"),
    )
    for column in ["run_id", "case_id", "requirement_id", "correlation_id", "status"]:
        op.create_index(f"ix_tool_runs_{column}", "tool_runs", [column])
    op.create_index(
        "uq_tool_runs_run_request",
        "tool_runs",
        ["run_id", "request_key"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("requirement_id", sa.String(64), nullable=False),
        sa.Column("stance", sa.String(16), nullable=False),
        sa.Column("modality", sa.String(32), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="SET NULL")),
        sa.Column("tool_run_id", sa.String(64), sa.ForeignKey("tool_runs.id", ondelete="SET NULL")),
        sa.Column("model_call_id", sa.String(64)),
        sa.Column("model_name", sa.String(255)),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["requirement_id", "case_id"],
            ["requirements.id", "requirements.case_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_evidence_time"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_evidence_confidence",
        ),
    )
    op.create_index("ix_evidence_case_id", "evidence", ["case_id"])
    op.create_index("ix_evidence_requirement_id", "evidence", ["requirement_id"])
    op.create_table(
        "requirement_results",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "requirement_id",
            sa.String(64),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("aggregator_version", sa.String(64), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        *timestamps(),
    )
    op.create_index(
        "ix_requirement_results_requirement_id", "requirement_results", ["requirement_id"]
    )
    op.create_index("ix_requirement_results_status", "requirement_results", ["status"])
    op.create_table(
        "requirement_result_evidence",
        sa.Column(
            "result_id",
            sa.String(64),
            sa.ForeignKey("requirement_results.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "evidence_id",
            sa.String(64),
            sa.ForeignKey("evidence.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "case_id", sa.String(64), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("decision_metadata", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["policy_id", "policy_version"], ["policies.policy_id", "policies.version"]
        ),
    )
    op.create_index("ix_decisions_case_id", "decisions", ["case_id"])
    op.create_index("ix_decisions_verdict", "decisions", ["verdict"])
    op.create_table(
        "decision_evidence",
        sa.Column(
            "decision_id",
            sa.String(64),
            sa.ForeignKey("decisions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "evidence_id",
            sa.String(64),
            sa.ForeignKey("evidence.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    for table in [
        "decision_evidence",
        "decisions",
        "requirement_result_evidence",
        "requirement_results",
        "evidence",
        "tool_runs",
        "requirements",
        "cases",
        "policies",
    ]:
        op.drop_table(table)
