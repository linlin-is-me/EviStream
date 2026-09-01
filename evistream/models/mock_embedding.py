"""Deterministic local embeddings for tests and offline development."""

import math
import re
import unicodedata
from hashlib import sha256
from time import perf_counter

from evistream.models.embedding_types import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingVector,
)
from evistream.models.types import ModelError, ModelErrorCode, ModelUsage

LATIN_TOKEN = re.compile(r"[a-z0-9]+")
CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


class MockEmbeddingGateway:
    def __init__(self, model_name: str = "mock-embedding-v1") -> None:
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        started = perf_counter()
        if not request.texts or request.dimensions <= 0:
            raise ModelError(
                ModelErrorCode.OUTPUT_INVALID,
                "embedding request requires text and positive dimensions",
                retryable=False,
            )
        vectors = [
            EmbeddingVector(index=index, values=_feature_hash(text, request.dimensions))
            for index, text in enumerate(request.texts)
        ]
        tokens = sum(len(_tokens(text)) for text in request.texts)
        return EmbeddingResponse(
            vectors=vectors,
            actual_model=self._model_name,
            usage=ModelUsage(prompt_tokens=tokens, total_tokens=tokens),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            provider_request_id=f"mock-embedding-{request.trace_id or 'local'}",
        )


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens = LATIN_TOKEN.findall(normalized)
    for run in CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens or [normalized.strip() or "empty"]


def _feature_hash(text: str, dimensions: int) -> list[float]:
    values = [0.0] * dimensions
    for token in _tokens(text):
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % dimensions
        sign = 1.0 if digest[8] & 1 else -1.0
        values[index] += sign
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]
