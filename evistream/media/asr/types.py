"""Provider-neutral ASR request and response structures."""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, model_validator


class ASRRequest(BaseModel):
    media_path: Path
    language: str | None = None


class ASRSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ASRSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class ASRResponse(BaseModel):
    segments: list[ASRSegment]
    language: str | None = None
    model: str
    duration_ms: int = Field(ge=0)


class ASRAdapter(Protocol):
    def transcribe(self, request: ASRRequest) -> ASRResponse: ...
