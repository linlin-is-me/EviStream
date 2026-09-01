"""Provider-neutral model gateway contracts and adapters."""

from evistream.models.embedding_types import (
    EmbeddingGateway,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingVector,
)
from evistream.models.factory import build_model_gateway, resolve_embedding_gateway
from evistream.models.mock import MockGateway
from evistream.models.mock_embedding import MockEmbeddingGateway
from evistream.models.openai_compatible import OpenAICompatibleGateway
from evistream.models.openai_embedding import OpenAICompatibleEmbeddingGateway
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
    "EmbeddingGateway",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "EmbeddingVector",
    "MockEmbeddingGateway",
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
    "OpenAICompatibleEmbeddingGateway",
    "OpenAICompatibleGateway",
    "build_model_gateway",
    "load_model_profile",
    "resolve_embedding_gateway",
]
