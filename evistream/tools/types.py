"""Stable input and output schema shared by every investigation tool."""

from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator


class ToolRequest(BaseModel):
    correlation_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    requirement_id: str = Field(min_length=1, max_length=64)
    query: str = ""
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    limit: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ToolRequest":
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms and end_ms must be provided together")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class ToolItem(BaseModel):
    source_ref: str
    artifact_id: str | None = None
    modality: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    content: str
    score: float | None = None


class ToolResult(BaseModel):
    tool_run_id: str
    request_key: str
    status: Literal["success", "partial", "failed"]
    items: list[ToolItem]
    latency_ms: int = Field(ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    error_code: str | None = None


class ToolOutput(BaseModel):
    status: Literal["success", "partial", "failed"] = "success"
    items: list[ToolItem] = Field(default_factory=list)
    estimated_cost: float = Field(default=0, ge=0)
    error_code: str | None = None


class Tool(Protocol):
    name: str

    async def execute(self, request: ToolRequest) -> ToolOutput: ...
