"""Stable task contracts shared by inline and future queue executors."""

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobRequest(BaseModel):
    job_id: str = Field(min_length=1)
    job_type: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class JobExecution(BaseModel):
    job_id: str
    job_type: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    elapsed_ms: int = Field(ge=0)


class JobHandler(Protocol):
    async def handle(self, request: JobRequest) -> dict[str, Any]: ...


class TaskDispatcher(Protocol):
    async def dispatch(self, request: JobRequest) -> JobExecution: ...

