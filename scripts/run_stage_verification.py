"""Run cumulative stage gates against a disposable PostgreSQL database."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from evistream.config import get_settings

DATABASE_PREFIX = "evistream_verify_"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["stage1", "stage2", "stage3", "stage4"])
    arguments = parser.parse_args()
    source = make_url(get_settings().database_url)
    suffix = uuid4().hex[:12]
    database_name = f"{DATABASE_PREFIX}{arguments.stage}_{suffix}"
    if source.database == database_name or not database_name.startswith(DATABASE_PREFIX):
        raise RuntimeError("refusing unsafe verification database name")

    root = Path(__file__).resolve().parents[1]
    with (
        _source_sentinel(source, suffix),
        _disposable_database(source, database_name) as target,
        tempfile.TemporaryDirectory(prefix="evistream-verify-artifacts-") as artifacts,
    ):
        environment = os.environ.copy()
        environment.update(
            {
                "EVISTREAM_DATABASE_URL": target,
                "EVISTREAM_ARTIFACT_ROOT": artifacts,
                "EVISTREAM_MODEL_PROFILE": "mock",
                "EVISTREAM_ASR_BACKEND": "mock",
                "EVISTREAM_OCR_BACKEND": "mock",
                "EVISTREAM_VISION_BACKEND": "mock",
            }
        )
        _run_migrations(arguments.stage, root, environment)
        _run_checks(arguments.stage, root, environment)
    print(f"{arguments.stage} verification passed in a disposable database")


def _run_migrations(stage: str, root: Path, environment: dict[str, str]) -> None:
    if stage == "stage4":
        legacy_run_id = f"manual_legacy_{uuid4().hex[:12]}"
        _run(
            [sys.executable, "-m", "alembic", "upgrade", "0004_pre_stage4_integrity"],
            root,
            environment,
        )
        _seed_legacy_tool_run(environment["EVISTREAM_DATABASE_URL"], legacy_run_id)
        _run([sys.executable, "-m", "alembic", "upgrade", "head"], root, environment)
        _verify_legacy_backfill(environment["EVISTREAM_DATABASE_URL"], legacy_run_id)
        _run(
            [sys.executable, "-m", "alembic", "downgrade", "0004_pre_stage4_integrity"],
            root,
            environment,
        )
        _run([sys.executable, "-m", "alembic", "upgrade", "head"], root, environment)
        _verify_legacy_backfill(environment["EVISTREAM_DATABASE_URL"], legacy_run_id)
        return
    revisions = (
        ["upgrade", "0002_stage2_domain"],
        ["upgrade", "head"],
        ["downgrade", "0002_stage2_domain"],
        ["upgrade", "head"],
    )
    if stage != "stage3":
        revisions = (["upgrade", "head"],)
    for arguments in revisions:
        _run([sys.executable, "-m", "alembic", *arguments], root, environment)


def _run_checks(stage: str, root: Path, environment: dict[str, str]) -> None:
    _run([sys.executable, "-m", "ruff", "check", "."], root, environment)
    _run([sys.executable, "-m", "mypy", "evistream", "apps"], root, environment)
    if stage in {"stage2", "stage3", "stage4"}:
        for policy in [
            "violence-weapon-v1.yaml",
            "dangerous-behavior-v1.yaml",
            "tobacco-alcohol-v1.yaml",
        ]:
            _run(
                [
                    sys.executable,
                    "-m",
                    "evistream.cli",
                    "policy-validate",
                    f"configs/policies/{policy}",
                ],
                root,
                environment,
            )
        _run(
            [sys.executable, "-m", "evistream.cli", "seed-demo", "--check"],
            root,
            environment,
        )
    if stage in {"stage3", "stage4"}:
        _run(
            [
                sys.executable,
                "-m",
                "evistream.cli",
                "embedding-smoke",
                "--profile",
                "mock",
            ],
            root,
            environment,
        )
    if stage == "stage4":
        _run([sys.executable, "scripts/seed_stage4_fixtures.py"], root, environment)
        for scenario in ["obvious", "cross-segment", "insufficient"]:
            case_suffix = scenario.replace("-", "_")
            stage_environment = environment.copy()
            stage_environment.update(
                {
                    "EVISTREAM_STAGE4_VERIFY": "1",
                    "EVISTREAM_STAGE4_SCRIPT": str(
                        root / "configs" / "demo" / f"stage4-{scenario}.yaml"
                    ),
                }
            )
            _run(
                [
                    sys.executable,
                    "-m",
                    "evistream.cli",
                    "investigate",
                    f"case_stage4_{case_suffix}",
                    "--profile",
                    "mock",
                ],
                root,
                stage_environment,
            )
        _verify_stage4_paths(environment["EVISTREAM_DATABASE_URL"], root, environment)
    _run([sys.executable, "-m", "pytest"], root, environment)


def _run(arguments: Sequence[str], root: Path, environment: dict[str, str]) -> None:
    subprocess.run(arguments, cwd=root, env=environment, check=True)


def _seed_legacy_tool_run(database_url: str, run_id: str) -> None:
    source = make_url(database_url)
    suffix = run_id.rsplit("_", maxsplit=1)[-1]
    video_id = f"vid_legacy_{suffix}"
    case_id = f"case_legacy_{suffix}"
    requirement_id = f"req_legacy_{suffix}"
    policy_id = f"test.stage4.legacy.{suffix}"
    with _database_connection(source) as connection:
        connection.execute(
            "INSERT INTO videos (id, original_name, artifact_uri, fingerprint, duration_ms, "
            "width, height, container, video_codec, has_audio, audio_codec, status, "
            "created_at, updated_at) VALUES (%s, 'legacy.mp4', %s, NULL, 1000, 1, 1, "
            "'mp4', 'h264', false, NULL, 'READY', now(), now())",
            (video_id, f"artifact://legacy/{suffix}.mp4"),
        )
        connection.execute(
            "INSERT INTO policies (policy_id, version, name, severity, enabled, lifecycle, "
            "source_yaml, compiled_policy, source_sha256, semantic_sha256, compiler_version, "
            "created_at, updated_at) VALUES (%s, 1, 'legacy', 'LOW', true, 'PUBLISHED', "
            "'id: legacy', '{}'::json, %s, %s, 'test', now(), now())",
            (policy_id, "1" * 64, "2" * 64),
        )
        connection.execute(
            "INSERT INTO cases (id, video_id, policy_id, policy_version, model_profile, status, "
            "created_at, updated_at) VALUES (%s, %s, %s, 1, 'mock', 'READY', now(), now())",
            (case_id, video_id, policy_id),
        )
        connection.execute(
            "INSERT INTO requirements (id, case_id, requirement_key, requirement_type, "
            "source_kind, required, description, suggested_queries, modalities, "
            "tool_capabilities, semantic_sha256, status, created_at, updated_at) VALUES "
            "(%s, %s, 'legacy', 'speech_content', 'requirement', true, 'legacy', "
            "'[]'::json, '[\"transcript\"]'::json, '[\"search_transcript\"]'::json, "
            "%s, 'PENDING', now(), now())",
            (requirement_id, case_id, "3" * 64),
        )
        connection.execute(
            "INSERT INTO tool_runs (id, run_id, case_id, requirement_id, correlation_id, "
            "tool_name, request_key, request_payload, response_payload, status, latency_ms, "
            "estimated_cost, error_code, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, "
            "'search_transcript', %s, '{}'::json, '{}'::json, 'success', 1, 0, NULL, now(), now())",
            (
                f"tool_legacy_{suffix}",
                run_id,
                case_id,
                requirement_id,
                f"corr_legacy_{suffix}",
                "4" * 64,
            ),
        )


def _verify_legacy_backfill(database_url: str, run_id: str) -> None:
    with _database_connection(make_url(database_url)) as connection:
        row = connection.execute(
            "SELECT run_kind, status, job_id FROM agent_runs WHERE id = %s", (run_id,)
        ).fetchone()
        if row != ("MANUAL_TOOL", "COMPLETED", None):
            raise RuntimeError("legacy ToolRun was not backfilled as a terminal manual run")


def _verify_stage4_paths(
    database_url: str, root: Path, environment: dict[str, str]
) -> None:
    expected = {
        "case_stage4_obvious": ("COMPLETED", 1),
        "case_stage4_cross_segment": ("COMPLETED", 2),
        "case_stage4_insufficient": ("NEEDS_HUMAN_REVIEW", 0),
    }
    with _database_connection(make_url(database_url)) as connection:
        for case_id, (status, evidence_count) in expected.items():
            row = connection.execute(
                "SELECT id, status FROM agent_runs WHERE case_id = %s AND "
                "run_kind = 'INVESTIGATION'",
                (case_id,),
            ).fetchone()
            if row is None or row[1] != status:
                raise RuntimeError(f"Stage 4 fixture did not reach {status}: {case_id}")
            count = connection.execute(
                "SELECT count(*) FROM evidence e JOIN model_calls m ON m.id = e.model_call_id "
                "WHERE m.run_id = %s",
                (row[0],),
            ).fetchone()[0]
            if count != evidence_count:
                raise RuntimeError(f"Stage 4 fixture evidence count mismatch: {case_id}")
            status_payload = _run_json(
                [sys.executable, "-m", "evistream.cli", "investigation-status", row[0]],
                root,
                environment,
            )
            trace_payload = _run_json(
                [sys.executable, "-m", "evistream.cli", "investigation-trace", row[0]],
                root,
                environment,
            )
            if status_payload.get("status") != status or not trace_payload.get("steps"):
                raise RuntimeError(f"Stage 4 status or trace CLI failed: {case_id}")


def _run_json(
    arguments: Sequence[str], root: Path, environment: dict[str, str]
) -> dict[str, Any]:
    completed = subprocess.run(
        arguments,
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("CLI did not return a JSON object")
    return payload


@contextmanager
def _disposable_database(source: URL, database_name: str) -> Iterator[str]:
    with _admin_connection(source) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    target = source.set(database=database_name).render_as_string(hide_password=False)
    try:
        yield target
    finally:
        if not database_name.startswith(DATABASE_PREFIX) or database_name == source.database:
            raise RuntimeError("refusing unsafe verification database cleanup")
        with _admin_connection(source) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


@contextmanager
def _source_sentinel(source: URL, suffix: str) -> Iterator[None]:
    sentinel_id = f"vid_verify_sentinel_{suffix}"
    inserted = False
    with _database_connection(source) as connection:
        exists = connection.execute("SELECT to_regclass('public.videos')").fetchone()[0]
        if exists:
            connection.execute(
                "INSERT INTO videos (id, original_name, artifact_uri, fingerprint, duration_ms, "
                "width, height, container, video_codec, has_audio, audio_codec, status, "
                "created_at, updated_at) VALUES (%s, %s, %s, NULL, 1, 1, 1, 'test', 'test', "
                "false, NULL, 'READY', now(), now())",
                (sentinel_id, "verification-sentinel", f"local://sentinel/{sentinel_id}"),
            )
            inserted = True
    try:
        yield
        if inserted:
            with _database_connection(source) as connection:
                row = connection.execute(
                    "SELECT 1 FROM videos WHERE id = %s", (sentinel_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("verification modified the configured development database")
    finally:
        if inserted:
            with _database_connection(source) as connection:
                connection.execute("DELETE FROM videos WHERE id = %s", (sentinel_id,))


def _admin_connection(source: URL) -> psycopg.Connection[tuple[Any, ...]]:
    return psycopg.connect(
        host=source.host or "localhost",
        port=source.port or 5432,
        user=source.username or "evistream",
        password=source.password or "",
        dbname="postgres",
        autocommit=True,
    )


def _database_connection(source: URL) -> psycopg.Connection[tuple[Any, ...]]:
    return psycopg.connect(
        host=source.host or "localhost",
        port=source.port or 5432,
        user=source.username or "evistream",
        password=source.password or "",
        dbname=source.database or "evistream",
        autocommit=True,
    )


if __name__ == "__main__":
    main()
