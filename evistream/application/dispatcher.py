"""In-process task execution using the same handlers planned for RQ."""

from time import perf_counter

from evistream.application.types import JobExecution, JobHandler, JobRequest, JobStatus


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        if not job_type:
            raise ValueError("job_type cannot be empty")
        if job_type in self._handlers:
            raise ValueError(f"handler already registered for {job_type}")
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as error:
            raise LookupError(f"no handler registered for {job_type}") from error


class InlineExecutor:
    """Execute a registered handler synchronously within the current process."""

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry

    async def dispatch(self, request: JobRequest) -> JobExecution:
        started = perf_counter()
        try:
            handler = self._registry.get(request.job_type)
            result = await handler.handle(request)
        except (LookupError, ValueError) as error:
            return JobExecution(
                job_id=request.job_id,
                job_type=request.job_type,
                status=JobStatus.FAILED,
                error_code="JOB_INVALID",
                error_message=str(error),
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception as error:
            return JobExecution(
                job_id=request.job_id,
                job_type=request.job_type,
                status=JobStatus.FAILED,
                error_code="INTERNAL_ERROR",
                error_message=type(error).__name__,
                elapsed_ms=_elapsed_ms(started),
            )

        return JobExecution(
            job_id=request.job_id,
            job_type=request.job_type,
            status=JobStatus.SUCCEEDED,
            result=result,
            elapsed_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
