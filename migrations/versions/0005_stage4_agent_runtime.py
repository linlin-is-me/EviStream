"""Add checkpointed Agent runs, immutable steps, and audited model calls."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_stage4_agent_runtime"
down_revision: str | None = "0004_pre_stage4_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _validate_legacy_runs()
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_kind", sa.String(24), nullable=False),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("processing_jobs.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "case_id",
            sa.String(64),
            sa.ForeignKey("cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_profile", sa.String(128), nullable=False),
        sa.Column("current_node", sa.String(24)),
        sa.Column("next_node", sa.String(24)),
        sa.Column("state_snapshot", sa.JSON(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vlm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "consecutive_tool_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("total_tool_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stagnant_iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("last_checkpoint_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("provisional_verdict", sa.String(32)),
        sa.Column("stop_reason", sa.String(64)),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "run_kind IN ('INVESTIGATION', 'MANUAL_TOOL')",
            name="ck_agent_runs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', "
            "'NEEDS_HUMAN_REVIEW', 'FAILED', 'CANCELLED')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "run_kind = 'MANUAL_TOOL' OR job_id IS NOT NULL",
            name="ck_agent_runs_investigation_job",
        ),
        sa.UniqueConstraint("id", "case_id", name="uq_agent_runs_id_case"),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"])
    op.create_index("ix_agent_runs_run_kind", "agent_runs", ["run_kind"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index(
        "uq_agent_runs_job_id",
        "agent_runs",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )
    op.create_index(
        "uq_agent_runs_investigation_case",
        "agent_runs",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("run_kind = 'INVESTIGATION'"),
    )
    _backfill_manual_runs()
    op.create_foreign_key(
        "fk_tool_runs_agent_run_case",
        "tool_runs",
        "agent_runs",
        ["run_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node", sa.String(24), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "state_version", name="uq_agent_steps_run_version"),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
    op.create_index("ix_agent_steps_node", "agent_steps", ["node"])
    op.execute(
        "CREATE TRIGGER protect_agent_steps BEFORE UPDATE OR DELETE ON agent_steps "
        "FOR EACH ROW EXECUTE FUNCTION evistream_forbid_immutable_change()"
    )

    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("processing_jobs.id", ondelete="RESTRICT"),
        ),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("node", sa.String(24), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("profile", sa.String(128), nullable=False),
        sa.Column("requested_model", sa.String(255), nullable=False),
        sa.Column("actual_model", sa.String(255)),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("request_summary", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "case_id"],
            ["agent_runs.id", "agent_runs.case_id"],
            name="fk_model_calls_agent_run_case",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "case_id", name="uq_model_calls_id_case"),
    )
    op.create_index("ix_model_calls_job_id", "model_calls", ["job_id"])
    op.create_index("ix_model_calls_run_id", "model_calls", ["run_id"])
    op.create_index("ix_model_calls_case_id", "model_calls", ["case_id"])
    op.create_index("ix_model_calls_node", "model_calls", ["node"])
    op.create_index("ix_model_calls_status", "model_calls", ["status"])
    op.create_index(
        "uq_model_calls_run_request",
        "model_calls",
        ["run_id", "request_key"],
        unique=True,
    )
    _validate_legacy_model_calls()
    op.create_foreign_key(
        "fk_evidence_model_call_case",
        "evidence",
        "model_calls",
        ["model_call_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )
    _replace_evidence_source_trigger(include_model_call=True)


def _validate_legacy_runs() -> None:
    op.execute(
        sa.text(
            """
            DO $$ DECLARE invalid_ids text; BEGIN
              SELECT string_agg(run_id, ', ' ORDER BY run_id) INTO invalid_ids
              FROM (
                SELECT run_id FROM tool_runs WHERE run_id IS NOT NULL
                GROUP BY run_id HAVING count(DISTINCT case_id) > 1
              ) bad;
              IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION 'legacy tool run IDs span cases: %', invalid_ids;
              END IF;
            END $$
            """
        )
    )


def _backfill_manual_runs() -> None:
    op.execute(
        """
        INSERT INTO agent_runs (
          id, run_kind, job_id, case_id, model_profile, current_node, next_node,
          state_snapshot, state_version, status, iteration, vlm_calls,
          consecutive_tool_failures, total_tool_failures, stagnant_iterations,
          deadline_at, last_checkpoint_at, lease_until, provisional_verdict,
          stop_reason, result_payload, created_at, updated_at
        )
        SELECT t.run_id, 'MANUAL_TOOL', NULL, t.case_id, c.model_profile, NULL, NULL,
          '{}'::json, 0, 'COMPLETED', 0, 0, 0,
          count(*) FILTER (WHERE t.status = 'failed'), 0,
          NULL, max(t.updated_at), NULL, NULL, 'MANUAL_TOOL',
          json_build_object('tool_run_count', count(*)), min(t.created_at), max(t.updated_at)
        FROM tool_runs t JOIN cases c ON c.id = t.case_id
        WHERE t.run_id IS NOT NULL
        GROUP BY t.run_id, t.case_id, c.model_profile
        """
    )


def _validate_legacy_model_calls() -> None:
    op.execute(
        sa.text(
            """
            DO $$ DECLARE invalid_ids text; BEGIN
              SELECT string_agg(id, ', ' ORDER BY id) INTO invalid_ids
              FROM evidence WHERE model_call_id IS NOT NULL;
              IF invalid_ids IS NOT NULL THEN
                RAISE EXCEPTION 'legacy evidence has unresolved model calls: %', invalid_ids;
              END IF;
            END $$
            """
        )
    )


def _replace_evidence_source_trigger(*, include_model_call: bool) -> None:
    model_check = """
          IF NEW.model_call_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM model_calls m WHERE m.id = NEW.model_call_id
              AND m.case_id = NEW.case_id AND m.status = 'success'
          ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
              MESSAGE = 'evidence model call must be successful and belong to the case';
          END IF;
    """ if include_model_call else ""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION evistream_validate_evidence_sources() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.artifact_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM artifacts a JOIN cases c ON c.id = NEW.case_id
            WHERE a.id = NEW.artifact_id AND a.video_id = c.video_id
          ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
              MESSAGE = 'evidence artifact must belong to the case video';
          END IF;
          IF NEW.tool_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM tool_runs t WHERE t.id = NEW.tool_run_id
              AND t.case_id = NEW.case_id AND t.requirement_id = NEW.requirement_id
          ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
              MESSAGE = 'evidence tool run must belong to the same case and requirement';
          END IF;
          {model_check}
          RETURN NEW;
        END $$
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_evidence_model_call_case", "evidence", type_="foreignkey")
    _replace_evidence_source_trigger(include_model_call=False)
    op.drop_index("uq_model_calls_run_request", table_name="model_calls")
    for name in [
        "ix_model_calls_status",
        "ix_model_calls_node",
        "ix_model_calls_case_id",
        "ix_model_calls_run_id",
        "ix_model_calls_job_id",
    ]:
        op.drop_index(name, table_name="model_calls")
    op.drop_table("model_calls")
    op.execute("DROP TRIGGER IF EXISTS protect_agent_steps ON agent_steps")
    op.drop_index("ix_agent_steps_node", table_name="agent_steps")
    op.drop_index("ix_agent_steps_run_id", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_constraint("fk_tool_runs_agent_run_case", "tool_runs", type_="foreignkey")
    op.drop_index("uq_agent_runs_investigation_case", table_name="agent_runs")
    op.drop_index("uq_agent_runs_job_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_run_kind", table_name="agent_runs")
    op.drop_index("ix_agent_runs_case_id", table_name="agent_runs")
    op.drop_table("agent_runs")
