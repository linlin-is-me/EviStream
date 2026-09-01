"""Isolate credentialed acceptance runs from the development database."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from run_stage_verification import _disposable_database, _source_sentinel
from sqlalchemy.engine import make_url

from evistream.config import get_settings


def main() -> None:
    if sys.argv[1:] != ["stage5"]:
        raise SystemExit("usage: run_external_verification.py stage5")
    source = make_url(get_settings().database_url)
    suffix = uuid4().hex[:12]
    name = f"evistream_verify_external_stage5_{suffix}"
    root = Path(__file__).resolve().parents[1]
    with (
        _source_sentinel(source, suffix),
        _disposable_database(source, name) as target,
        tempfile.TemporaryDirectory(prefix="evistream-external-artifacts-") as artifacts,
    ):
        environment = os.environ.copy()
        environment.update(
            {
                "EVISTREAM_DATABASE_URL": target,
                "EVISTREAM_ARTIFACT_ROOT": artifacts,
                "EVISTREAM_ASR_BACKEND": "faster-whisper",
                "EVISTREAM_OCR_BACKEND": "paddleocr",
                "EVISTREAM_VISION_BACKEND": "gateway",
            }
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            env=environment,
            check=True,
        )
        subprocess.run(
            [sys.executable, "scripts/run_stage5_external_acceptance.py"],
            cwd=root,
            env=environment,
            check=True,
        )


if __name__ == "__main__":
    main()
