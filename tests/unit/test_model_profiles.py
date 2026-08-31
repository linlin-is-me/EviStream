from pathlib import Path

import pytest

from evistream.models.profiles import load_model_profile, resolve_model_profile
from evistream.models.types import ModelError, ModelErrorCode, ModelRole

CONFIG_DIR = Path("configs/models")


def test_mock_profile_does_not_require_provider_environment() -> None:
    profile = load_model_profile(CONFIG_DIR, "mock")

    resolved = resolve_model_profile(profile, ModelRole.AGENT, {})

    assert resolved.gateway == "mock"
    assert resolved.model == "mock-stage0"
    assert resolved.api_key is None


def test_custom_profile_reads_only_declared_environment_names() -> None:
    profile = load_model_profile(CONFIG_DIR, "custom-openai")
    environment = {
        "EVISTREAM_MODEL_BASE_URL": "https://provider.example/v1",
        "EVISTREAM_MODEL_API_KEY": "secret",
        "EVISTREAM_AGENT_MODEL": "example-model",
    }

    resolved = resolve_model_profile(profile, ModelRole.AGENT, environment)

    assert resolved.base_url == "https://provider.example/v1"
    assert resolved.api_key == "secret"
    assert resolved.model == "example-model"


def test_missing_api_key_has_explicit_non_retryable_error() -> None:
    profile = load_model_profile(CONFIG_DIR, "custom-openai")

    with pytest.raises(ModelError) as caught:
        resolve_model_profile(
            profile,
            ModelRole.AGENT,
            {
                "EVISTREAM_MODEL_BASE_URL": "https://provider.example/v1",
                "EVISTREAM_AGENT_MODEL": "example-model",
            },
        )

    assert caught.value.code is ModelErrorCode.UNAVAILABLE
    assert caught.value.retryable is False


def test_profile_name_cannot_escape_config_directory() -> None:
    with pytest.raises(ModelError, match="invalid model profile name"):
        load_model_profile(CONFIG_DIR, "../secret")
