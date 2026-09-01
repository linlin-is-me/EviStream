"""Shared inline and RQ dispatch composition."""

import asyncio
from typing import Any

from evistream.agent.runtime import build_agent_runtime
from evistream.application.types import JobExecution, JobRequest, JobStatus, TaskDispatcher
from evistream.config import Settings
from evistream.governance.runtime import build_governance_runtime
from evistream.governance.service import GovernanceApplicationService
from evistream.jobs.service import JobService
from evistream.jobs.types import DispatchState, JobSubmission
from evistream.media.runtime import build_media_runtime
from evistream.storage.database import Database, utc_now
from evistream.storage.models import ProcessingJobRecord


class PersistedJobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def request(self, job_id: str) -> JobRequest:
        with self.database.session() as session:
            record = session.get(ProcessingJobRecord, job_id)
            if record is None:
                raise LookupError(job_id)
            return JobRequest(
                job_id=record.id,
                job_type=record.type,
                request_key=record.request_key,
                correlation_id=record.correlation_id,
                payload=record.payload,
            )


class RuntimeFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, request: JobRequest) -> JobExecution:
        profile = str(request.payload.get("model_profile") or self.settings.model_profile)
        if request.job_type == "MEDIA_PREPROCESS":
            return await build_media_runtime(self.settings, profile).dispatcher.dispatch(request)
        if request.job_type == "AGENT_INVESTIGATION":
            agent_runtime = build_agent_runtime(self.settings, profile)
            execution = await agent_runtime.dispatcher.dispatch(request)
            if execution.status == JobStatus.SUCCEEDED:
                GovernanceApplicationService(agent_runtime.database).finalize_case(
                    str(request.payload["case_id"])
                )
            return execution
        if request.job_type == "POLICY_REPLAY":
            governance_runtime = build_governance_runtime(self.settings, profile)
            return await governance_runtime.dispatcher.dispatch(request)
        raise LookupError(f"unsupported job type: {request.job_type}")


class ApplicationDispatcher(TaskDispatcher):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_url)
        self.factory = RuntimeFactory(settings)

    async def dispatch(self, request: JobRequest) -> JobExecution:
        if self.settings.task_dispatcher == "inline":
            return await self.factory.execute(request)
        try:
            _enqueue(self.settings, request.job_id)
            with self.database.session() as session:
                record = session.get(ProcessingJobRecord, request.job_id)
                if record is not None:
                    record.last_enqueued_at = utc_now()
            return JobExecution(
                job_id=request.job_id,
                job_type=request.job_type,
                status=JobStatus.PENDING,
                result={"dispatch_state": DispatchState.QUEUED},
                elapsed_ms=0,
            )
        except Exception:
            return JobExecution(
                job_id=request.job_id,
                job_type=request.job_type,
                status=JobStatus.PENDING,
                result={"dispatch_state": DispatchState.DEFERRED},
                elapsed_ms=0,
            )

    async def submit(self, request: JobRequest) -> JobSubmission:
        execution = await self.dispatch(request)
        state = (
            DispatchState.INLINE_COMPLETED
            if self.settings.task_dispatcher == "inline"
            else DispatchState((execution.result or {}).get("dispatch_state", "DEFERRED"))
        )
        return JobSubmission(
            job=JobService(self.database).get(request.job_id), dispatch_state=state
        )


def _enqueue(settings: Settings, job_id: str) -> Any:
    from redis import Redis
    from rq import Queue, Retry

    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.rq_queue, connection=connection)
    retry = (
        Retry(
            max=settings.job_max_attempts - 1,
            interval=settings.job_retry_intervals,
        )
        if settings.job_max_attempts > 1
        else None
    )
    return queue.enqueue(
        "evistream.jobs.worker.execute_job",
        job_id,
        job_timeout=settings.rq_job_timeout_seconds,
        result_ttl=settings.rq_result_ttl_seconds,
        retry=retry,
    )


def execute_persisted_job(settings: Settings, job_id: str) -> JobExecution:
    request = PersistedJobRepository(Database(settings.database_url)).request(job_id)
    return asyncio.run(RuntimeFactory(settings).execute(request))


def requeue_due(settings: Settings, *, due_only: bool = True) -> int:
    service = JobService(Database(settings.database_url))
    jobs = (
        service.due(settings.job_requeue_batch_size)
        if due_only
        else service.unfinished(settings.job_requeue_batch_size)
    )
    for job in jobs:
        _enqueue(settings, job.job_id)
        service.mark_enqueued(job.job_id)
    return len(jobs)
