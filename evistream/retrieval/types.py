"""Contracts for hybrid and temporal retrieval."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RetrievalRequest(BaseModel):
    video_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1)
    modalities: list[Literal["transcript", "ocr", "vision"]]
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    limit: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def validate_time_range(self) -> "RetrievalRequest":
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms and end_ms must be provided together")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if not self.query.strip():
            raise ValueError("query must not be blank")
        if not self.modalities:
            raise ValueError("at least one modality is required")
        return self


class RetrievalHit(BaseModel):
    document_id: str
    source_ref: str
    artifact_id: str
    modality: str
    start_ms: int
    end_ms: int
    content: str
    keyword_rank: int | None = None
    vector_rank: int | None = None
    score: float = Field(ge=0)


class RetrievalResult(BaseModel):
    status: Literal["success", "partial", "failed"]
    hits: list[RetrievalHit]
    error_code: str | None = None


class IndexFailure(BaseModel):
    batch_index: int = Field(ge=0)
    document_ids: list[str]
    error_code: str
    retryable: bool


class IndexSummary(BaseModel):
    status: Literal["success", "partial", "failed"]
    error_code: str | None = None
    video_id: str
    total: int = Field(ge=0)
    indexed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    actual_model: str
    embedding_space: str
    dimensions: int = Field(gt=0)
    prompt_tokens: int = Field(ge=0)
    failures: list[IndexFailure] = Field(default_factory=list)
