from pathlib import Path

from evistream.config import Settings


def test_settings_have_safe_stage_zero_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.model_profile == "mock"
    assert settings.model_config_dir == Path("configs/models")
    assert settings.asr_backend == "mock"

