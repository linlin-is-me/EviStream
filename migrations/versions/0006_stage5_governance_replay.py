"""Add deterministic governance, review, appeal, and replay lineage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_stage5_governance_replay"
down_revision: str | None = "0005_stage4_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_replay_tables()
    _extend_existing_tables()
    _backfill_governance_columns()
    _create_governance_constraints()
    _create_insert_default_triggers()
    _create_replay_evidence_trigger()
    _create_review_tables()
    _protect_append_only_tables()


def _create_replay_tables() -> None:
    op.create_table(
        "replay_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "processing_job_id",
            sa.String(64),
            sa.ForeignKey("processing_jobs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("preview_sha256", sa.String(64), nullable=False),
        sa.Column("model_profile", sa.String(128)),
        sa.Column("model_change_policy", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id", "source_version"],
            ["policies.policy_id", "policies.version"],
            name="fk_replay_jobs_source_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "target_version"],
            ["policies.policy_id", "policies.version"],
            name="fk_replay_jobs_target_policy",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "mode IN ('REEVALUATE', 'REINVESTIGATE')",
            name="ck_replay_jobs_mode",
        ),
        sa.CheckConstraint(
            "model_change_policy IN ('keep', 'invalidate-visual')",
            name="ck_replay_jobs_model_policy",
        ),
    )
    op.create_index("ix_replay_jobs_policy", "replay_jobs", ["policy_id"])
    op.create_index("ix_replay_jobs_status", "replay_jobs", ["status"])

    op.create_table(
        "replay_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "replay_job_id",
            sa.String(64),
            sa.ForeignKey("replay_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_case_id",
            sa.String(64),
            sa.ForeignKey("cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_case_id",
            sa.String(64),
            sa.ForeignKey("cases.id", ondelete="RESTRICT"),
        ),
        sa.Column("target_policy_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("plan_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON()),
        sa.Column(
            "source_decision_id",
            sa.String(64),
            sa.ForeignKey("decisions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "target_decision_id",
            sa.String(64),
            sa.ForeignKey("decisions.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_case_id",
            "target_policy_version",
            name="uq_replay_items_source_target_version",
        ),
        sa.CheckConstraint(
            "mode IN ('REEVALUATE', 'REINVESTIGATE')",
            name="ck_replay_items_mode",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'MATERIALIZED', 'INVESTIGATING', "
            "'COMPLETED', 'NEEDS_HUMAN_REVIEW', 'FAILED')",
            name="ck_replay_items_status",
        ),
    )
    op.create_index("ix_replay_items_job", "replay_items", ["replay_job_id"])
    op.create_index("ix_replay_items_status", "replay_items", ["status"])

    op.create_table(
        "replay_lineage",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "replay_item_id",
            sa.String(64),
            sa.ForeignKey("replay_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(64)),
        sa.Column("target_id", sa.String(64)),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "replay_item_id",
            "entity_type",
            "action",
            "source_id",
            "target_id",
            "reason_code",
            name="uq_replay_lineage_entry",
        ),
        sa.CheckConstraint(
            "action IN ('REUSED', 'INVALIDATED', 'RECREATED')",
            name="ck_replay_lineage_action",
        ),
    )
    op.create_index("ix_replay_lineage_item", "replay_lineage", ["replay_item_id"])


def _extend_existing_tables() -> None:
    op.add_column("requirements", sa.Column("current_result_id", sa.String(64)))
    op.add_column("cases", sa.Column("current_decision_id", sa.String(64)))
    op.add_column(
        "agent_runs",
        sa.Column(
            "scope_requirement_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column("evidence", sa.Column("origin_evidence_id", sa.String(64)))
    op.add_column("evidence", sa.Column("replay_item_id", sa.String(64)))

    op.add_column("requirement_results", sa.Column("case_id", sa.String(64)))
    op.add_column("requirement_results", sa.Column("sequence", sa.Integer()))
    op.add_column(
        "requirement_results",
        sa.Column(
            "aggregation_config",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.add_column("requirement_results", sa.Column("origin_result_id", sa.String(64)))
    op.add_column("requirement_results", sa.Column("replay_item_id", sa.String(64)))

    op.add_column("decisions", sa.Column("sequence", sa.Integer()))
    op.add_column(
        "decisions",
        sa.Column("evaluator_version", sa.String(64), nullable=False, server_default="legacy"),
    )
    op.add_column("decisions", sa.Column("input_sha256", sa.String(64)))
    op.add_column("decisions", sa.Column("agent_run_id", sa.String(64)))
    op.add_column("decisions", sa.Column("supersedes_decision_id", sa.String(64)))
    op.add_column("decisions", sa.Column("replay_item_id", sa.String(64)))


def _backfill_governance_columns() -> None:
    op.execute(
        "UPDATE requirement_results result SET case_id = requirement.case_id "
        "FROM requirements requirement WHERE requirement.id = result.requirement_id"
    )
    op.execute(
        "WITH ranked AS (SELECT id, row_number() OVER (PARTITION BY requirement_id "
        "ORDER BY created_at, id) AS value FROM requirement_results) "
        "UPDATE requirement_results result SET sequence = ranked.value "
        "FROM ranked WHERE ranked.id = result.id"
    )
    op.execute(
        "WITH ranked AS (SELECT id, row_number() OVER (PARTITION BY case_id "
        "ORDER BY created_at, id) AS value FROM decisions) "
        "UPDATE decisions decision SET sequence = ranked.value "
        "FROM ranked WHERE ranked.id = decision.id"
    )
    op.execute(
        "UPDATE decisions SET input_sha256 = lpad(md5(id), 64, '0') "
        "WHERE input_sha256 IS NULL"
    )
    op.execute(
        "UPDATE requirements requirement SET current_result_id = latest.id FROM ("
        "SELECT DISTINCT ON (requirement_id) id, requirement_id FROM requirement_results "
        "ORDER BY requirement_id, sequence DESC) latest "
        "WHERE requirement.id = latest.requirement_id"
    )
    op.execute(
        "UPDATE cases case_record SET current_decision_id = latest.id FROM ("
        "SELECT DISTINCT ON (case_id) id, case_id FROM decisions "
        "ORDER BY case_id, sequence DESC) latest WHERE case_record.id = latest.case_id"
    )
    op.alter_column("requirement_results", "case_id", nullable=False)
    op.alter_column("requirement_results", "sequence", nullable=False)
    op.alter_column("decisions", "sequence", nullable=False)
    op.alter_column("decisions", "input_sha256", nullable=False)


def _create_governance_constraints() -> None:
    op.create_unique_constraint(
        "uq_requirement_results_requirement_sequence",
        "requirement_results",
        ["requirement_id", "sequence"],
    )
    op.create_unique_constraint(
        "uq_requirement_results_input",
        "requirement_results",
        ["requirement_id", "aggregator_version", "input_sha256"],
    )
    op.create_unique_constraint(
        "uq_requirement_results_id_case",
        "requirement_results",
        ["id", "case_id"],
    )
    op.create_foreign_key(
        "fk_requirement_results_requirement_case",
        "requirement_results",
        "requirements",
        ["requirement_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_requirement_results_origin",
        "requirement_results",
        "requirement_results",
        ["origin_result_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_requirement_results_replay_item",
        "requirement_results",
        "replay_items",
        ["replay_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_requirements_current_result",
        "requirements",
        "requirement_results",
        ["current_result_id", "id"],
        ["id", "requirement_id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_evidence_origin",
        "evidence",
        "evidence",
        ["origin_evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_evidence_replay_item",
        "evidence",
        "replay_items",
        ["replay_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_unique_constraint(
        "uq_decisions_case_sequence", "decisions", ["case_id", "sequence"]
    )
    op.create_unique_constraint(
        "uq_decisions_deterministic_input",
        "decisions",
        ["case_id", "source", "evaluator_version", "input_sha256"],
    )
    op.create_foreign_key(
        "fk_decisions_agent_run_case",
        "decisions",
        "agent_runs",
        ["agent_run_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_decisions_supersedes_case",
        "decisions",
        "decisions",
        ["supersedes_decision_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_decisions_replay_item",
        "decisions",
        "replay_items",
        ["replay_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_cases_current_decision",
        "cases",
        "decisions",
        ["current_decision_id", "id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "decision_requirement_results",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("result_id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            name="fk_decision_results_decision_case",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "case_id"],
            ["requirement_results.id", "requirement_results.case_id"],
            name="fk_decision_results_result_case",
            ondelete="RESTRICT",
        ),
    )


def _create_review_tables() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("reviewed_decision_id", sa.String(64)),
        sa.Column("decision_id", sa.String(64), nullable=False, unique=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewed_decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            name="fk_reviews_reviewed_decision_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            name="fk_reviews_decision_case",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_reviews_case", "reviews", ["case_id"])

    op.create_table(
        "appeals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("submitter", sa.String(128), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("challenged_decision_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("resolution_review_id", sa.String(64), sa.ForeignKey("reviews.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["challenged_decision_id", "case_id"],
            ["decisions.id", "decisions.case_id"],
            name="fk_appeals_challenged_decision_case",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="ck_appeals_status"),
    )
    op.create_index("ix_appeals_case", "appeals", ["case_id"])
    op.create_index("ix_appeals_status", "appeals", ["status"])

    op.create_table(
        "appeal_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "appeal_id",
            sa.String(64),
            sa.ForeignKey("appeals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("decision_id", sa.String(64), sa.ForeignKey("decisions.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("appeal_id", "sequence", name="uq_appeal_events_sequence"),
        sa.CheckConstraint(
            "event_type IN ('SUBMITTED', 'RESOLVED')",
            name="ck_appeal_events_type",
        ),
    )
    op.create_index("ix_appeal_events_appeal", "appeal_events", ["appeal_id"])


def _create_insert_default_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION evistream_stage5_result_defaults() RETURNS trigger AS $$
        BEGIN
          IF NEW.case_id IS NULL THEN
            SELECT case_id INTO NEW.case_id FROM requirements WHERE id = NEW.requirement_id;
          END IF;
          IF NEW.sequence IS NULL THEN
            SELECT coalesce(max(sequence), 0) + 1 INTO NEW.sequence
            FROM requirement_results WHERE requirement_id = NEW.requirement_id;
          END IF;
          IF NEW.origin_result_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM requirement_results source_result
            JOIN requirements source_requirement
              ON source_requirement.id = source_result.requirement_id
            JOIN cases source_case ON source_case.id = source_result.case_id
            JOIN requirements target_requirement ON target_requirement.id = NEW.requirement_id
            JOIN cases target_case ON target_case.id = NEW.case_id
            WHERE source_result.id = NEW.origin_result_id
              AND source_case.video_id = target_case.video_id
              AND source_requirement.semantic_sha256 = target_requirement.semantic_sha256
          ) THEN
            RAISE EXCEPTION 'replay result origin is not semantically compatible';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER stage5_requirement_result_defaults BEFORE INSERT ON "
        "requirement_results FOR EACH ROW EXECUTE FUNCTION "
        "evistream_stage5_result_defaults()"
    )
    op.execute(
        """
        CREATE FUNCTION evistream_stage5_decision_defaults() RETURNS trigger AS $$
        BEGIN
          IF NEW.sequence IS NULL THEN
            SELECT coalesce(max(sequence), 0) + 1 INTO NEW.sequence
            FROM decisions WHERE case_id = NEW.case_id;
          END IF;
          IF NEW.evaluator_version IS NULL THEN NEW.evaluator_version := 'legacy'; END IF;
          IF NEW.input_sha256 IS NULL THEN NEW.input_sha256 := lpad(md5(NEW.id), 64, '0'); END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER stage5_decision_defaults BEFORE INSERT ON decisions "
        "FOR EACH ROW EXECUTE FUNCTION evistream_stage5_decision_defaults()"
    )


def _create_replay_evidence_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION evistream_validate_replay_evidence() RETURNS trigger AS $$
        DECLARE
          source_video varchar(64);
          target_video varchar(64);
          source_semantic varchar(64);
          target_semantic varchar(64);
        BEGIN
          IF NEW.origin_evidence_id IS NULL THEN RETURN NEW; END IF;
          SELECT source_case.video_id, source_requirement.semantic_sha256
          INTO source_video, source_semantic
          FROM evidence source_evidence
          JOIN cases source_case ON source_case.id = source_evidence.case_id
          JOIN requirements source_requirement
            ON source_requirement.id = source_evidence.requirement_id
          WHERE source_evidence.id = NEW.origin_evidence_id;
          SELECT target_case.video_id, target_requirement.semantic_sha256
          INTO target_video, target_semantic
          FROM cases target_case
          JOIN requirements target_requirement ON target_requirement.case_id = target_case.id
          WHERE target_case.id = NEW.case_id AND target_requirement.id = NEW.requirement_id;
          IF source_video IS NULL OR target_video IS NULL
             OR source_video <> target_video OR source_semantic <> target_semantic THEN
            RAISE EXCEPTION 'replay evidence origin is not semantically compatible';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER validate_replay_evidence BEFORE INSERT ON evidence "
        "FOR EACH ROW EXECUTE FUNCTION evistream_validate_replay_evidence()"
    )


def _protect_append_only_tables() -> None:
    for table in [
        "decision_requirement_results",
        "reviews",
        "appeal_events",
        "replay_lineage",
    ]:
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION evistream_forbid_immutable_change()"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS validate_replay_evidence ON evidence")
    op.execute("DROP FUNCTION IF EXISTS evistream_validate_replay_evidence()")
    op.execute("DROP TRIGGER IF EXISTS stage5_decision_defaults ON decisions")
    op.execute("DROP FUNCTION IF EXISTS evistream_stage5_decision_defaults()")
    op.execute(
        "DROP TRIGGER IF EXISTS stage5_requirement_result_defaults ON requirement_results"
    )
    op.execute("DROP FUNCTION IF EXISTS evistream_stage5_result_defaults()")
    for table in [
        "replay_lineage",
        "appeal_events",
        "reviews",
        "decision_requirement_results",
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS protect_{table} ON {table}")

    op.drop_table("appeal_events")
    op.drop_index("ix_appeals_status", table_name="appeals")
    op.drop_index("ix_appeals_case", table_name="appeals")
    op.drop_table("appeals")
    op.drop_index("ix_reviews_case", table_name="reviews")
    op.drop_table("reviews")
    op.drop_table("decision_requirement_results")

    for constraint, table in [
        ("fk_cases_current_decision", "cases"),
        ("fk_decisions_replay_item", "decisions"),
        ("fk_decisions_supersedes_case", "decisions"),
        ("fk_decisions_agent_run_case", "decisions"),
        ("fk_evidence_replay_item", "evidence"),
        ("fk_evidence_origin", "evidence"),
        ("fk_requirements_current_result", "requirements"),
        ("fk_requirement_results_replay_item", "requirement_results"),
        ("fk_requirement_results_origin", "requirement_results"),
        ("fk_requirement_results_requirement_case", "requirement_results"),
    ]:
        op.drop_constraint(constraint, table, type_="foreignkey")

    op.drop_constraint("uq_decisions_deterministic_input", "decisions", type_="unique")
    op.drop_constraint("uq_decisions_case_sequence", "decisions", type_="unique")
    for constraint in [
        "uq_requirement_results_id_case",
        "uq_requirement_results_input",
        "uq_requirement_results_requirement_sequence",
    ]:
        op.drop_constraint(constraint, "requirement_results", type_="unique")

    for column in [
        "replay_item_id",
        "supersedes_decision_id",
        "agent_run_id",
        "input_sha256",
        "evaluator_version",
        "sequence",
    ]:
        op.drop_column("decisions", column)
    for column in [
        "replay_item_id",
        "origin_result_id",
        "aggregation_config",
        "sequence",
        "case_id",
    ]:
        op.drop_column("requirement_results", column)
    op.drop_column("evidence", "replay_item_id")
    op.drop_column("evidence", "origin_evidence_id")
    op.drop_column("agent_runs", "scope_requirement_ids")
    op.drop_column("cases", "current_decision_id")
    op.drop_column("requirements", "current_result_id")

    op.drop_index("ix_replay_lineage_item", table_name="replay_lineage")
    op.drop_table("replay_lineage")
    op.drop_index("ix_replay_items_status", table_name="replay_items")
    op.drop_index("ix_replay_items_job", table_name="replay_items")
    op.drop_table("replay_items")
    op.drop_index("ix_replay_jobs_status", table_name="replay_jobs")
    op.drop_index("ix_replay_jobs_policy", table_name="replay_jobs")
    op.drop_table("replay_jobs")
