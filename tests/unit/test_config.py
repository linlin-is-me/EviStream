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


def test_settings_expose_profile_environment_without_plaintext_secret_repr() -> None:
    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_api_key="secret-value",
        triage_model="triage-model",
    )

    environment = settings.model_environment()
    assert environment["EVISTREAM_MODEL_BASE_URL"] == "https://models.example/v1"
    assert environment["EVISTREAM_MODEL_API_KEY"] == "secret-value"
    assert environment["EVISTREAM_TRIAGE_MODEL"] == "triage-model"
    assert "secret-value" not in repr(settings)
