"""Use-case orchestration independent from HTTP and queue frameworks."""

from hashlib import sha256
from uuid import uuid4

from evistream.application.types import JobExecution, JobRequest, TaskDispatcher


class ApplicationService:
    def __init__(self, dispatcher: TaskDispatcher) -> None:
        self._dispatcher = dispatcher

    async def run_demo_job(
        self,
        message: str,
        *,
        correlation_id: str | None = None,
    ) -> JobExecution:
        normalized = message.strip()
        request_key = sha256(f"DEMO:{normalized}".encode()).hexdigest()
        request = JobRequest(
            job_id=f"job_{uuid4().hex}",
            job_type="DEMO",
            request_key=request_key,
            correlation_id=correlation_id or f"corr_{uuid4().hex}",
            payload={"message": normalized},
        )
        return await self._dispatcher.dispatch(request)
