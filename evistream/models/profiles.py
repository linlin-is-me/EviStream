"""YAML model profiles resolved exclusively through environment variable names."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from evistream.models.types import ModelCapability, ModelError, ModelErrorCode, ModelRole


class ProfileModels(BaseModel):
    agent_env: str
    triage_env: str
    verifier_env: str
    judge_env: str

    def environment_name(self, role: ModelRole) -> str:
        return {
            ModelRole.AGENT: self.agent_env,
            ModelRole.TRIAGE: self.triage_env,
            ModelRole.VERIFIER: self.verifier_env,
            ModelRole.JUDGE: self.judge_env,
        }[role]


class ProfileDefaults(BaseModel):
    temperature: float = Field(default=0.0, ge=0, le=2)
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    structured_output: bool = True
    max_attempts: int = Field(default=2, ge=1, le=5)


class ModelProfile(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    gateway: Literal["mock", "openai_compatible"]
    base_url_env: str | None = None
    api_key_env: str | None = None
    models: ProfileModels
    defaults: ProfileDefaults = Field(default_factory=ProfileDefaults)
    capabilities: ModelCapability = Field(default_factory=ModelCapability)


class ResolvedModelProfile(BaseModel):
    name: str
    gateway: Literal["mock", "openai_compatible"]
    base_url: str | None
    api_key: str | None
    model: str
    defaults: ProfileDefaults
    capabilities: ModelCapability


def load_model_profile(config_dir: Path, profile_name: str) -> ModelProfile:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", profile_name):
        raise ModelError(ModelErrorCode.UNAVAILABLE, "invalid model profile name", retryable=False)

    root = config_dir.resolve()
    candidate = (root / f"{profile_name}.yaml").resolve()
    if root not in candidate.parents:
        raise ModelError(
            ModelErrorCode.UNAVAILABLE,
            "model profile escaped config root",
            retryable=False,
        )
    if not candidate.is_file():
        raise ModelError(
            ModelErrorCode.UNAVAILABLE,
            f"model profile not found: {profile_name}",
            retryable=False,
        )

    try:
        document = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        return ModelProfile.model_validate(document)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ModelError(
            ModelErrorCode.UNAVAILABLE,
            f"invalid model profile: {profile_name}",
            retryable=False,
        ) from error


def resolve_model_profile(
    profile: ModelProfile,
    role: ModelRole,
    environment: Mapping[str, str] | None = None,
) -> ResolvedModelProfile:
    values = environment if environment is not None else os.environ
    if profile.gateway == "mock":
        return ResolvedModelProfile(
            name=profile.name,
            gateway=profile.gateway,
            base_url=None,
            api_key=None,
            model="mock-stage0",
            defaults=profile.defaults,
            capabilities=profile.capabilities,
        )

    base_url = _required_value(values, profile.base_url_env, "base URL")
    api_key = _required_value(values, profile.api_key_env, "API key")
    model = _required_value(values, profile.models.environment_name(role), "model ID")
    return ResolvedModelProfile(
        name=profile.name,
        gateway=profile.gateway,
        base_url=base_url,
        api_key=api_key,
        model=model,
        defaults=profile.defaults,
        capabilities=profile.capabilities,
    )


def _required_value(
    environment: Mapping[str, str],
    variable_name: str | None,
    label: str,
) -> str:
    if not variable_name:
        raise ModelError(
            ModelErrorCode.UNAVAILABLE,
            f"profile does not declare a {label} environment variable",
            retryable=False,
        )
    value = environment.get(variable_name, "").strip()
    if not value:
        raise ModelError(
            ModelErrorCode.UNAVAILABLE,
            f"required {label} is not configured in {variable_name}",
            retryable=False,
        )
    return value
