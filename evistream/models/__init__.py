"""Provider-neutral model gateway contracts and adapters."""

from evistream.models.factory import build_model_gateway
from evistream.models.mock import MockGateway
from evistream.models.openai_compatible import OpenAICompatibleGateway
from evistream.models.profiles import ModelProfile, load_model_profile
from evistream.models.types import (
    ModelCapability,
    ModelError,
    ModelErrorCode,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelUsage,
)

__all__ = [
    "MockGateway",
    "ModelCapability",
    "ModelError",
    "ModelErrorCode",
    "ModelMessage",
    "ModelProfile",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelUsage",
    "OpenAICompatibleGateway",
    "build_model_gateway",
    "load_model_profile",
]
