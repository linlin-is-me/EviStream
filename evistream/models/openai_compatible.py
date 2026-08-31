"""OpenAI Chat Completions adapter without provider-specific branches."""

import asyncio
import json
from time import perf_counter
from typing import Any, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import ValidationError

from evistream.models.types import (
    ModelCapability,
    ModelError,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


class OpenAICompatibleGateway:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        capability: ModelCapability,
        temperature: float = 0,
        structured_output: bool = True,
        max_attempts: int = 2,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._capability = capability
        self._temperature = temperature
        self._structured_output = structured_output
        self._max_attempts = max_attempts
        self._client = client or AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,
        )

    @property
    def capability(self) -> ModelCapability:
        return self._capability

    async def generate(self, request: ModelRequest) -> ModelResponse:
        last_error: ModelError | None = None
        for attempt in range(self._max_attempts):
            try:
                return await self._generate_once(request)
            except ModelError as error:
                last_error = error
                if not error.retryable or attempt + 1 >= self._max_attempts:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("model attempt loop ended without a result")

    async def _generate_once(self, request: ModelRequest) -> ModelResponse:
        started = perf_counter()
        messages = self._build_messages(request)
        arguments: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "timeout": request.timeout_seconds,
        }
        if self._structured_output:
            arguments["response_format"] = {"type": "json_object"}
        if request.trace_id:
            arguments["extra_headers"] = {"X-Request-ID": request.trace_id}

        try:
            completion = await self._client.chat.completions.create(**cast(Any, arguments))
        except APITimeoutError as error:
            raise ModelError(
                ModelErrorCode.TIMEOUT,
                "model request timed out",
                retryable=True,
            ) from error
        except RateLimitError as error:
            raise ModelError(
                ModelErrorCode.RATE_LIMITED,
                "model request was rate limited",
                retryable=True,
            ) from error
        except APIConnectionError as error:
            raise ModelError(
                ModelErrorCode.UNAVAILABLE,
                "model endpoint is unavailable",
                retryable=True,
            ) from error
        except APIStatusError as error:
            retryable = error.status_code >= 500
            raise ModelError(
                ModelErrorCode.UNAVAILABLE,
                f"model endpoint returned HTTP {error.status_code}",
                retryable=retryable,
            ) from error

        if not completion.choices:
            raise ModelError(
                ModelErrorCode.OUTPUT_INVALID,
                "model response contains no choices",
                retryable=False,
            )
        content = completion.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise ModelError(
                ModelErrorCode.OUTPUT_INVALID,
                "model response contains no text output",
                retryable=False,
            )

        try:
            raw_data = json.loads(content)
            validated = request.response_schema.model_validate(raw_data)
        except (json.JSONDecodeError, ValidationError) as error:
            raise ModelError(
                ModelErrorCode.OUTPUT_INVALID,
                "model output is not valid structured data",
                retryable=False,
            ) from error

        usage = completion.usage
        return ModelResponse(
            data=validated.model_dump(mode="json"),
            actual_model=completion.model or self._model,
            usage=ModelUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            finish_reason=completion.choices[0].finish_reason,
            provider_request_id=completion.id,
        )

    def _build_messages(self, request: ModelRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": message.role, "content": message.content} for message in request.messages
        ]
        if not request.media:
            return messages

        user_indexes = [
            index for index, message in enumerate(messages) if message["role"] == "user"
        ]
        if not user_indexes:
            raise ModelError(
                ModelErrorCode.UNAVAILABLE,
                "media input requires a user message",
                retryable=False,
            )

        target_index = user_indexes[-1]
        content: list[dict[str, Any]] = [
            {"type": "text", "text": messages[target_index]["content"]}
        ]
        for media in request.media:
            if media.kind == "image":
                if not self._capability.image:
                    raise ModelError(
                        ModelErrorCode.UNAVAILABLE,
                        "configured model does not support image input",
                        retryable=False,
                    )
                content.append({"type": "image_url", "image_url": {"url": media.uri}})
            else:
                if not self._capability.video:
                    raise ModelError(
                        ModelErrorCode.UNAVAILABLE,
                        "configured model does not support video input",
                        retryable=False,
                    )
                content.append({"type": "video_url", "video_url": {"url": media.uri}})
        messages[target_index]["content"] = content
        return messages
