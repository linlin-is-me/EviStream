import os
from pathlib import Path

import pytest

from evistream.config import Settings


def test_settings_have_safe_stage_zero_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("EVISTREAM_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert settings.environment == "development"
    assert settings.model_profile == "mock"
    assert settings.model_config_dir == Path("configs/models")
    assert settings.asr_backend == "mock"
