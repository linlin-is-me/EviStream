"""Internal contracts that isolate business code from model providers."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ModelRole(StrEnum):
    AGENT = "agent"
    TRIAGE = "triage"
    VERIFIER = "verifier"
    JUDGE = "judge"


class ModelMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class MediaReference(BaseModel):
    kind: Literal["image", "video"]
    uri: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    role: ModelRole
    messages: Sequence[ModelMessage]
    response_schema: type[BaseModel]
    media: Sequence[MediaReference] = field(default_factory=tuple)
    timeout_seconds: float = 30.0
    trace_id: str = ""


class ModelUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ModelResponse(BaseModel):
    data: dict[str, Any]
    actual_model: str
    usage: ModelUsage = Field(default_factory=ModelUsage)
    latency_ms: int = Field(ge=0)
    finish_reason: str | None = None
    provider_request_id: str | None = None


class ModelCapability(BaseModel):
    text: bool = True
    image: bool = False
    video: bool = False
    structured_output: bool = True


class ModelErrorCode(StrEnum):
    TIMEOUT = "MODEL_TIMEOUT"
    RATE_LIMITED = "MODEL_RATE_LIMITED"
    OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    UNAVAILABLE = "MODEL_UNAVAILABLE"


class ModelError(RuntimeError):
    def __init__(self, code: ModelErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ModelGateway(Protocol):
    @property
    def capability(self) -> ModelCapability: ...

    async def generate(self, request: ModelRequest) -> ModelResponse: ...
