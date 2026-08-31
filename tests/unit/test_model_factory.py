from pathlib import Path

from evistream.models.factory import build_model_gateway
from evistream.models.mock import MockGateway
from evistream.models.openai_compatible import OpenAICompatibleGateway
from evistream.models.types import ModelRole

CONFIG_DIR = Path("configs/models")


def test_factory_builds_mock_without_credentials() -> None:
    gateway = build_model_gateway(CONFIG_DIR, "mock", ModelRole.AGENT, environment={})

    assert isinstance(gateway, MockGateway)


def test_factory_builds_generic_compatible_gateway() -> None:
    gateway = build_model_gateway(
        CONFIG_DIR,
        "custom-openai",
        ModelRole.AGENT,
        environment={
            "EVISTREAM_MODEL_BASE_URL": "https://provider.example/v1",
            "EVISTREAM_MODEL_API_KEY": "secret",
            "EVISTREAM_AGENT_MODEL": "provider-model",
        },
    )

    assert isinstance(gateway, OpenAICompatibleGateway)
