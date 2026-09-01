"""Run cumulative stage gates against a disposable PostgreSQL database."""

import argparse
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
    parser.add_argument("stage", choices=["stage1", "stage2", "stage3"])
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
    if stage in {"stage2", "stage3"}:
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
    if stage == "stage3":
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
    _run([sys.executable, "-m", "pytest"], root, environment)


def _run(arguments: Sequence[str], root: Path, environment: dict[str, str]) -> None:
    subprocess.run(arguments, cwd=root, env=environment, check=True)


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
