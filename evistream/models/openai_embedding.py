"""OpenAI-compatible embedding adapter without provider-specific fields."""

import asyncio
import math
from time import perf_counter
from typing import Any, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from evistream.models.embedding_types import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingVector,
)
from evistream.models.types import ModelError, ModelErrorCode, ModelUsage


class OpenAICompatibleEmbeddingGateway:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_attempts: int = 2,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._max_attempts = max_attempts
        self._client = client or AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    @property
    def model_name(self) -> str:
        return self._model

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        last_error: ModelError | None = None
        for attempt in range(self._max_attempts):
            try:
                return await self._embed_once(request)
            except ModelError as error:
                last_error = error
                if not error.retryable or attempt + 1 >= self._max_attempts:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("embedding attempt loop ended without a result")

    async def _embed_once(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if not request.texts or any(not text.strip() for text in request.texts):
            raise ModelError(
                ModelErrorCode.OUTPUT_INVALID,
                "embedding request contains no usable text",
                retryable=False,
            )
        started = perf_counter()
        arguments: dict[str, Any] = {
            "model": self._model,
            "input": list(request.texts),
            "dimensions": request.dimensions,
            "encoding_format": "float",
            "timeout": request.timeout_seconds,
        }
        if request.trace_id:
            arguments["extra_headers"] = {"X-Request-ID": request.trace_id}
        try:
            response = await self._client.embeddings.create(**cast(Any, arguments))
        except APITimeoutError as error:
            raise ModelError(
                ModelErrorCode.TIMEOUT, "embedding request timed out", retryable=True
            ) from error
        except RateLimitError as error:
            raise ModelError(
                ModelErrorCode.RATE_LIMITED,
                "embedding request was rate limited",
                retryable=True,
            ) from error
        except APIConnectionError as error:
            raise ModelError(
                ModelErrorCode.UNAVAILABLE,
                "embedding endpoint is unavailable",
                retryable=True,
            ) from error
        except APIStatusError as error:
            raise ModelError(
                ModelErrorCode.UNAVAILABLE,
                f"embedding endpoint returned HTTP {error.status_code}",
                retryable=error.status_code >= 500,
            ) from error

        ordered = sorted(response.data, key=lambda item: item.index)
        indexes = [item.index for item in ordered]
        if len(ordered) != len(request.texts) or indexes != list(range(len(request.texts))):
            raise _invalid("embedding response count or indexes do not match the request")
        vectors: list[EmbeddingVector] = []
        for item in ordered:
            values = [float(value) for value in item.embedding]
            valid_values = all(math.isfinite(value) for value in values)
            if len(values) != request.dimensions or not valid_values:
                raise _invalid("embedding response contains an invalid vector")
            vectors.append(EmbeddingVector(index=item.index, values=values))
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage is not None else 0
        total_tokens = usage.total_tokens if usage is not None else prompt_tokens
        return EmbeddingResponse(
            vectors=vectors,
            actual_model=response.model or self._model,
            usage=ModelUsage(prompt_tokens=prompt_tokens, total_tokens=total_tokens),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            provider_request_id=getattr(response, "id", None),
        )


def _invalid(message: str) -> ModelError:
    return ModelError(ModelErrorCode.OUTPUT_INVALID, message, retryable=False)
