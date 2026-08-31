"""Construct gateways from generic profile configuration."""

from collections.abc import Mapping
from pathlib import Path

from evistream.models.mock import MockGateway
from evistream.models.openai_compatible import OpenAICompatibleGateway
from evistream.models.profiles import load_model_profile, resolve_model_profile
from evistream.models.types import ModelGateway, ModelRole


def build_model_gateway(
    config_dir: Path,
    profile_name: str,
    role: ModelRole,
    *,
    environment: Mapping[str, str] | None = None,
) -> ModelGateway:
    profile = load_model_profile(config_dir, profile_name)
    resolved = resolve_model_profile(profile, role, environment)
    if resolved.gateway == "mock":
        return MockGateway(model_name=resolved.model)
    if resolved.base_url is None or resolved.api_key is None:
        raise RuntimeError("resolved OpenAI-compatible profile lacks connection values")
    return OpenAICompatibleGateway(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        model=resolved.model,
        capability=resolved.capabilities,
        temperature=resolved.defaults.temperature,
        structured_output=resolved.defaults.structured_output,
        max_attempts=resolved.defaults.max_attempts,
    )
