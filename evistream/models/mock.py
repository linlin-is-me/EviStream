"""Deterministic model gateway for CI and offline development."""

from time import perf_counter
from typing import Any

from pydantic import ValidationError

from evistream.models.types import (
    ModelCapability,
    ModelError,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


class MockGateway:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        model_name: str = "mock-stage0",
        failure: ModelError | None = None,
    ) -> None:
        self._payload = payload or {"ok": True, "summary": "stage0"}
        self._model_name = model_name
        self._failure = failure

    @property
    def capability(self) -> ModelCapability:
        return ModelCapability(text=True, structured_output=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        started = perf_counter()
        if self._failure is not None:
            raise self._failure
        try:
            validated = request.response_schema.model_validate(self._payload)
        except ValidationError as error:
            raise ModelError(
                ModelErrorCode.OUTPUT_INVALID,
                "mock payload does not satisfy response schema",
                retryable=False,
            ) from error
        return ModelResponse(
            data=validated.model_dump(mode="json"),
            actual_model=self._model_name,
            usage=ModelUsage(),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            finish_reason="stop",
            provider_request_id="mock-request",
        )

