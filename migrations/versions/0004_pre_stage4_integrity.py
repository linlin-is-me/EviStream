"""Protect policy and evidence audit relationships before Stage 4."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_pre_stage4_integrity"
down_revision: str | None = "0003_stage3_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requirement_result_evidence", sa.Column("requirement_id", sa.String(64))
    )
    op.execute(
        "UPDATE requirement_result_evidence link "
        "SET requirement_id = result.requirement_id "
        "FROM requirement_results result WHERE result.id = link.result_id"
    )
    op.add_column("decision_evidence", sa.Column("case_id", sa.String(64)))
    op.execute(
        "UPDATE decision_evidence link SET case_id = decision.case_id "
        "FROM decisions decision WHERE decision.id = link.decision_id"
    )
    _validate_existing_data()
    op.alter_column("requirement_result_evidence", "requirement_id", nullable=False)
    op.alter_column("decision_evidence", "case_id", nullable=False)

    op.create_unique_constraint(
        "uq_cases_id_policy", "cases", ["id", "policy_id", "policy_version"]
    )
    op.create_unique_constraint(
        "uq_evidence_id_requirement", "evidence", ["id", "requirement_id"]
    )
    op.create_unique_constraint("uq_evidence_id_case", "evidence", ["id", "case_id"])
    op.create_unique_constraint(
        "uq_requirement_results_id_requirement",
        "requirement_results",
        ["id", "requirement_id"],
    )
    op.create_unique_constraint("uq_decisions_id_case", "decisions", ["id", "case_id"])

    op.drop_constraint("tool_runs_requirement_id_fkey", "tool_runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_tool_runs_requirement_case",
        "tool_runs",
        "requirements",
        ["requirement_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint("decisions_case_id_fkey", "decisions", type_="foreignkey")
    op.drop_constraint(
        "decisions_policy_id_policy_version_fkey", "decisions", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_decisions_case_policy",
        "decisions",
        "cases",
        ["case_id", "policy_id", "policy_version"],
        ["id", "policy_id", "policy_version"],
        ondelete="RESTRICT",
    )

    for constraint, table in [
        ("requirement_result_evidence_result_id_fkey", "requirement_result_evidence"),
        ("requirement_result_evidence_evidence_id_fkey", "requirement_result_evidence"),
        ("decision_evidence_decision_id_fkey", "decision_evidence"),
        ("decision_evidence_evidence_id_fkey", "decision_evidence"),
    ]:
        op.drop_constraint(constraint, table, type_="foreignkey")
    op.create_foreign_key(
        "fk_result_evidence_result_requirement",
        "requirement_result_evidence",
        "requirement_results",
        ["result_id", "requirement_id"],
        ["id", "requirement_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_result_evidence_evidence_requirement",
        "requirement_result_evidence",
        "evidence",
        ["evidence_id", "requirement_id"],
        ["id", "requirement_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_decision_evidence_decision_case",
        "decision_evidence",
        "decisions",
        ["decision_id", "case_id"],
        ["id", "case_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_decision_evidence_evidence_case",
        "decision_evidence",
        "evidence",
        ["evidence_id", "case_id"],
        ["id", "case_id"],
        ondelete="RESTRICT",
    )
    _create_triggers()


def _validate_existing_data() -> None:
    validations = {
        "cases reference unpublished policies": """
            SELECT string_agg(c.id, ', ' ORDER BY c.id)
            FROM cases c JOIN policies p
              ON p.policy_id = c.policy_id AND p.version = c.policy_version
            WHERE p.lifecycle <> 'PUBLISHED'
        """,
        "decisions do not match case policy": """
            SELECT string_agg(d.id, ', ' ORDER BY d.id)
            FROM decisions d JOIN cases c ON c.id = d.case_id
            WHERE (d.policy_id, d.policy_version) <> (c.policy_id, c.policy_version)
        """,
        "tool runs reference a requirement from another case": """
            SELECT string_agg(t.id, ', ' ORDER BY t.id)
            FROM tool_runs t JOIN requirements r ON r.id = t.requirement_id
            WHERE t.requirement_id IS NOT NULL AND t.case_id <> r.case_id
        """,
        "result evidence references another requirement": """
            SELECT string_agg(link.result_id || ':' || link.evidence_id, ', ')
            FROM requirement_result_evidence link
            JOIN evidence e ON e.id = link.evidence_id
            WHERE e.requirement_id <> link.requirement_id
        """,
        "decision evidence references another case": """
            SELECT string_agg(link.decision_id || ':' || link.evidence_id, ', ')
            FROM decision_evidence link JOIN evidence e ON e.id = link.evidence_id
            WHERE e.case_id <> link.case_id
        """,
        "evidence references an artifact from another video": """
            SELECT string_agg(e.id, ', ' ORDER BY e.id)
            FROM evidence e JOIN cases c ON c.id = e.case_id
            JOIN artifacts a ON a.id = e.artifact_id
            WHERE e.artifact_id IS NOT NULL AND a.video_id <> c.video_id
        """,
        "evidence references an unrelated tool run": """
            SELECT string_agg(e.id, ', ' ORDER BY e.id)
            FROM evidence e JOIN tool_runs t ON t.id = e.tool_run_id
            WHERE e.tool_run_id IS NOT NULL
              AND (t.case_id <> e.case_id OR t.requirement_id IS DISTINCT FROM e.requirement_id)
        """,
    }
    for message, query in validations.items():
        escaped = message.replace("'", "''")
        op.execute(
            sa.text(
                "DO $$ DECLARE invalid_ids text; BEGIN "
                f"SELECT bad.ids INTO invalid_ids FROM ({query}) AS bad(ids); "
                f"IF invalid_ids IS NOT NULL THEN RAISE EXCEPTION '{escaped}: %', invalid_ids; "
                "END IF; END $$"
            )
        )


def _create_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION evistream_forbid_immutable_change() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = format('%s records are append-only', TG_TABLE_NAME);
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION evistream_protect_published_policy() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.lifecycle = 'PUBLISHED' THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'published policy is immutable';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION evistream_require_published_policy() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM policies p WHERE p.policy_id = NEW.policy_id
              AND p.version = NEW.policy_version AND p.lifecycle = 'PUBLISHED'
          ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'case policy must be published';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION evistream_validate_evidence_sources() RETURNS trigger
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
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER protect_published_policy BEFORE UPDATE OR DELETE ON policies "
        "FOR EACH ROW EXECUTE FUNCTION evistream_protect_published_policy()"
    )
    op.execute(
        "CREATE TRIGGER require_published_case_policy BEFORE INSERT OR UPDATE ON cases "
        "FOR EACH ROW EXECUTE FUNCTION evistream_require_published_policy()"
    )
    op.execute(
        "CREATE TRIGGER validate_evidence_sources BEFORE INSERT OR UPDATE ON evidence "
        "FOR EACH ROW EXECUTE FUNCTION evistream_validate_evidence_sources()"
    )
    for table in [
        "evidence",
        "requirement_results",
        "requirement_result_evidence",
        "decisions",
        "decision_evidence",
    ]:
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION evistream_forbid_immutable_change()"
        )


def downgrade() -> None:
    for table in [
        "decision_evidence",
        "decisions",
        "requirement_result_evidence",
        "requirement_results",
        "evidence",
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS protect_{table} ON {table}")
    op.execute("DROP TRIGGER IF EXISTS validate_evidence_sources ON evidence")
    op.execute("DROP TRIGGER IF EXISTS require_published_case_policy ON cases")
    op.execute("DROP TRIGGER IF EXISTS protect_published_policy ON policies")
    for function in [
        "evistream_validate_evidence_sources",
        "evistream_require_published_policy",
        "evistream_protect_published_policy",
        "evistream_forbid_immutable_change",
    ]:
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")

    for constraint, table in [
        ("fk_decision_evidence_evidence_case", "decision_evidence"),
        ("fk_decision_evidence_decision_case", "decision_evidence"),
        ("fk_result_evidence_evidence_requirement", "requirement_result_evidence"),
        ("fk_result_evidence_result_requirement", "requirement_result_evidence"),
        ("fk_decisions_case_policy", "decisions"),
        ("fk_tool_runs_requirement_case", "tool_runs"),
    ]:
        op.drop_constraint(constraint, table, type_="foreignkey")

    op.create_foreign_key(
        "tool_runs_requirement_id_fkey",
        "tool_runs",
        "requirements",
        ["requirement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "decisions_case_id_fkey", "decisions", "cases", ["case_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "decisions_policy_id_policy_version_fkey",
        "decisions",
        "policies",
        ["policy_id", "policy_version"],
        ["policy_id", "version"],
    )
    op.create_foreign_key(
        "requirement_result_evidence_result_id_fkey",
        "requirement_result_evidence",
        "requirement_results",
        ["result_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "requirement_result_evidence_evidence_id_fkey",
        "requirement_result_evidence",
        "evidence",
        ["evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "decision_evidence_decision_id_fkey",
        "decision_evidence",
        "decisions",
        ["decision_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "decision_evidence_evidence_id_fkey",
        "decision_evidence",
        "evidence",
        ["evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    for constraint, table in [
        ("uq_decisions_id_case", "decisions"),
        ("uq_requirement_results_id_requirement", "requirement_results"),
        ("uq_evidence_id_case", "evidence"),
        ("uq_evidence_id_requirement", "evidence"),
        ("uq_cases_id_policy", "cases"),
    ]:
        op.drop_constraint(constraint, table, type_="unique")
    op.drop_column("decision_evidence", "case_id")
    op.drop_column("requirement_result_evidence", "requirement_id")
