"""RQ import target. The queue payload contains only a PostgreSQL job ID."""

from evistream.config import get_settings
from evistream.jobs.runtime import execute_persisted_job
from evistream.jobs.service import JobService
from evistream.observability import configure_json_logging
from evistream.storage.database import Database


def execute_job(job_id: str) -> dict[str, object]:
    settings = get_settings()
    logger = configure_json_logging("worker")
    logger.info("job_started", extra={"event": "job.started", "job_id": job_id})
    result = execute_persisted_job(settings, job_id)
    persisted = JobService(Database(settings.database_url)).get(job_id)
    if persisted.status == "RETRY_WAIT":
        logger.error(
            "job_retry_wait",
            extra={
                "event": "job.retry_wait",
                "job_id": job_id,
                "job_type": persisted.job_type,
                "attempt": persisted.attempt,
                "status": persisted.status,
                "error_code": persisted.error_code,
            },
        )
        raise RetryableJobError(persisted.error_code or "JOB_RETRY_WAIT")
    logger.info(
        "job_finished",
        extra={
            "event": "job.finished",
            "job_id": job_id,
            "job_type": persisted.job_type,
            "attempt": persisted.attempt,
            "status": persisted.status,
            "latency_ms": result.elapsed_ms,
            "error_code": persisted.error_code,
        },
    )
    return result.model_dump(mode="json")


class RetryableJobError(RuntimeError):
    """Signal RQ to apply the configured delayed retry schedule."""
