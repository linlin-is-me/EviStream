"""Construct gateways from generic profile configuration."""

from collections.abc import Mapping
from pathlib import Path

from evistream.models.embedding_types import EmbeddingGateway
from evistream.models.mock import MockGateway
from evistream.models.mock_embedding import MockEmbeddingGateway
from evistream.models.openai_compatible import OpenAICompatibleGateway
from evistream.models.openai_embedding import OpenAICompatibleEmbeddingGateway
from evistream.models.profiles import (
    ResolvedEmbeddingProfile,
    load_model_profile,
    resolve_embedding_profile,
    resolve_model_profile,
)
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


def resolve_embedding_gateway(
    config_dir: Path,
    profile_name: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[EmbeddingGateway, ResolvedEmbeddingProfile]:
    profile = load_model_profile(config_dir, profile_name)
    resolved = resolve_embedding_profile(profile, environment)
    if resolved.gateway == "mock":
        return MockEmbeddingGateway(resolved.model), resolved
    if resolved.base_url is None or resolved.api_key is None:
        raise RuntimeError("resolved embedding profile lacks connection values")
    gateway = OpenAICompatibleEmbeddingGateway(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        model=resolved.model,
        max_attempts=resolved.max_attempts,
    )
    return gateway, resolved
