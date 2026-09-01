"""PostgreSQL-backed job queries and explicit retry transitions."""

from datetime import datetime

from sqlalchemy import or_, select

from evistream.application.types import JobStatus
from evistream.jobs.types import JobView
from evistream.storage.database import Database, utc_now
from evistream.storage.models import ProcessingJobRecord


class JobServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class JobService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, job_id: str) -> JobView:
        with self.database.session() as session:
            record = session.get(ProcessingJobRecord, job_id)
            if record is None:
                raise JobServiceError("JOB_NOT_FOUND", f"job not found: {job_id}")
            return job_view(record)

    def retry(self, job_id: str) -> JobView:
        with self.database.session() as session:
            record = session.scalar(
                select(ProcessingJobRecord)
                .where(ProcessingJobRecord.id == job_id)
                .with_for_update()
            )
            if record is None:
                raise JobServiceError("JOB_NOT_FOUND", f"job not found: {job_id}")
            if record.status == JobStatus.RUNNING:
                raise JobServiceError("JOB_ALREADY_RUNNING", "job has an active execution")
            if (
                record.status not in {JobStatus.RETRY_WAIT, JobStatus.FAILED}
                or not record.retryable
            ):
                raise JobServiceError("JOB_NOT_RETRYABLE", "job is not retryable")
            if record.attempt >= record.max_attempts:
                raise JobServiceError("JOB_RETRY_EXHAUSTED", "job attempts are exhausted")
            record.status = JobStatus.PENDING
            record.next_attempt_at = None
            record.finished_at = None
            record.updated_at = utc_now()
            return job_view(record)

    def due(self, limit: int, now: datetime | None = None) -> list[JobView]:
        current = now or utc_now()
        with self.database.session() as session:
            records = session.scalars(
                select(ProcessingJobRecord)
                .where(
                    or_(
                        ProcessingJobRecord.status == JobStatus.PENDING,
                        (
                            (ProcessingJobRecord.status == JobStatus.RETRY_WAIT)
                            & (ProcessingJobRecord.next_attempt_at <= current)
                        ),
                        (
                            (ProcessingJobRecord.status == JobStatus.RUNNING)
                            & (ProcessingJobRecord.lease_until <= current)
                        ),
                    ),
                    ProcessingJobRecord.attempt < ProcessingJobRecord.max_attempts,
                )
                .order_by(ProcessingJobRecord.created_at, ProcessingJobRecord.id)
                .limit(limit)
            ).all()
            return [job_view(record) for record in records]

    def unfinished(self, limit: int) -> list[JobView]:
        with self.database.session() as session:
            records = session.scalars(
                select(ProcessingJobRecord)
                .where(
                    ProcessingJobRecord.status.in_(
                        [JobStatus.PENDING, JobStatus.RETRY_WAIT, JobStatus.RUNNING]
                    ),
                    ProcessingJobRecord.attempt < ProcessingJobRecord.max_attempts,
                )
                .order_by(ProcessingJobRecord.created_at, ProcessingJobRecord.id)
                .limit(limit)
            ).all()
            return [job_view(record) for record in records]

    def mark_enqueued(self, job_id: str) -> None:
        with self.database.session() as session:
            record = session.get(ProcessingJobRecord, job_id)
            if record is None:
                raise JobServiceError("JOB_NOT_FOUND", f"job not found: {job_id}")
            record.last_enqueued_at = utc_now()


def job_view(record: ProcessingJobRecord) -> JobView:
    return JobView(
        job_id=record.id,
        job_type=record.type,
        subject_id=record.subject_id,
        request_key=record.request_key,
        correlation_id=record.correlation_id,
        status=record.status,
        attempt=record.attempt,
        max_attempts=record.max_attempts,
        retryable=record.retryable,
        error_code=record.error_code,
        error_message=record.error_message,
        next_attempt_at=record.next_attempt_at,
        last_enqueued_at=record.last_enqueued_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
