"""Provider-neutral text embedding contracts."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from evistream.models.types import ModelUsage


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    texts: tuple[str, ...]
    dimensions: int = 1536
    timeout_seconds: float = 30.0
    trace_id: str = ""


class EmbeddingVector(BaseModel):
    index: int = Field(ge=0)
    values: list[float]


class EmbeddingResponse(BaseModel):
    vectors: list[EmbeddingVector]
    actual_model: str
    usage: ModelUsage = Field(default_factory=ModelUsage)
    latency_ms: int = Field(ge=0)
    provider_request_id: str | None = None


class EmbeddingGateway(Protocol):
    @property
    def model_name(self) -> str: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...
