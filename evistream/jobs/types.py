"""Public job projections used by API and UI."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from evistream.application.types import JobStatus


class DispatchState(StrEnum):
    INLINE_COMPLETED = "INLINE_COMPLETED"
    QUEUED = "QUEUED"
    DEFERRED = "DEFERRED"


class JobView(BaseModel):
    job_id: str
    job_type: str
    subject_id: str
    request_key: str
    correlation_id: str
    status: JobStatus
    attempt: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    retryable: bool
    error_code: str | None = None
    error_message: str | None = None
    next_attempt_at: datetime | None = None
    last_enqueued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class JobSubmission(BaseModel):
    job: JobView
    dispatch_state: DispatchState
