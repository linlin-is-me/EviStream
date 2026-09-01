"""Exercise Stage 3 migration paths in a disposable PostgreSQL database."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from evistream.config import get_settings


def main() -> None:
    source = make_url(get_settings().database_url)
    database_name = f"evistream_stage3_{uuid4().hex[:12]}"
    with _admin_connection(source) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    target = source.set(database=database_name).render_as_string(hide_password=False)
    environment = os.environ.copy()
    environment["EVISTREAM_DATABASE_URL"] = target
    root = Path(__file__).resolve().parents[1]
    try:
        for arguments in [
            ["upgrade", "0002_stage2_domain"],
            ["upgrade", "head"],
            ["downgrade", "0002_stage2_domain"],
            ["upgrade", "head"],
        ]:
            subprocess.run(
                [sys.executable, "-m", "alembic", *arguments],
                cwd=root,
                env=environment,
                check=True,
            )
    finally:
        with _admin_connection(source) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
    print("Stage 3 migration paths passed")


def _admin_connection(source: URL) -> psycopg.Connection[tuple[Any, ...]]:
    return psycopg.connect(
        host=source.host or "localhost",
        port=source.port or 5432,
        user=source.username or "evistream",
        password=source.password or "",
        dbname="postgres",
        autocommit=True,
    )


if __name__ == "__main__":
    main()
