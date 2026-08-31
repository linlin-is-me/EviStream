import pytest

from evistream.application import (
    ApplicationService,
    DemoJobHandler,
    HandlerRegistry,
    InlineExecutor,
    JobRequest,
    JobStatus,
)


@pytest.mark.asyncio
async def test_inline_executor_runs_shared_handler() -> None:
    registry = HandlerRegistry()
    registry.register("DEMO", DemoJobHandler())
    service = ApplicationService(InlineExecutor(registry))

    execution = await service.run_demo_job(" stage zero ", correlation_id="corr_test")

    assert execution.status is JobStatus.SUCCEEDED
    assert execution.result == {"message": "stage zero", "uppercase": "STAGE ZERO"}
    assert execution.error_code is None


@pytest.mark.asyncio
async def test_inline_executor_returns_explicit_error_for_unknown_job() -> None:
    executor = InlineExecutor(HandlerRegistry())
    request = JobRequest(
        job_id="job_missing",
        job_type="UNKNOWN",
        request_key="request-key",
        correlation_id="corr_test",
    )

    execution = await executor.dispatch(request)

    assert execution.status is JobStatus.FAILED
    assert execution.error_code == "JOB_INVALID"
    assert execution.result is None


def test_registry_rejects_duplicate_handlers() -> None:
    registry = HandlerRegistry()
    registry.register("DEMO", DemoJobHandler())

    with pytest.raises(ValueError, match="already registered"):
        registry.register("DEMO", DemoJobHandler())

