"""Hybrid text and vector retrieval services."""

from evistream.retrieval.indexing import EmbeddingIndexService, embedding_space_id
from evistream.retrieval.service import HybridRetrievalService
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.retrieval.types import (
    IndexFailure,
    IndexSummary,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
)

__all__ = [
    "EmbeddingIndexService",
    "HybridRetrievalService",
    "IndexFailure",
    "IndexSummary",
    "RetrievalHit",
    "RetrievalRequest",
    "RetrievalResult",
    "embedding_space_id",
    "normalize_text",
    "search_lexemes",
]
