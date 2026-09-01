"""PostgreSQL keyword, vector and RRF retrieval."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import case, func, or_, select

from evistream.models.embedding_types import EmbeddingGateway, EmbeddingRequest
from evistream.models.profiles import ResolvedEmbeddingProfile
from evistream.models.types import ModelError
from evistream.retrieval.indexing import embedding_space_id
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.retrieval.types import RetrievalHit, RetrievalRequest, RetrievalResult
from evistream.storage.database import Database
from evistream.storage.models import SearchDocumentRecord

VECTOR_UNAVAILABLE = "RETRIEVAL_VECTOR_UNAVAILABLE"


class HybridRetrievalService:
    def __init__(
        self,
        database: Database,
        gateway: EmbeddingGateway,
        profile: ResolvedEmbeddingProfile,
        *,
        rrf_k: int = 60,
        candidate_limit: int = 100,
    ) -> None:
        self.database = database
        self.gateway = gateway
        self.profile = profile
        self.rrf_k = rrf_k
        self.candidate_limit = candidate_limit
        self.space = embedding_space_id(profile)

    async def search(self, request: RetrievalRequest) -> RetrievalResult:
        candidate_count = min(self.candidate_limit, max(20, request.limit * 4))
        keyword_modalities = [item for item in request.modalities if item in {"transcript", "ocr"}]
        keyword = self._keyword(request, keyword_modalities, candidate_count)
        vector: list[SearchDocumentRecord] = []
        vector_failed = False
        try:
            response = await self.gateway.embed(
                EmbeddingRequest(
                    texts=(request.query,),
                    dimensions=self.profile.dimensions,
                    timeout_seconds=self.profile.timeout_seconds,
                    trace_id=f"retrieve-{request.video_id}",
                )
            )
            vector = self._vector(request, response.vectors[0].values, candidate_count)
        except (ModelError, IndexError):
            vector_failed = True

        if vector_failed and not keyword_modalities:
            return RetrievalResult(status="failed", hits=[], error_code=VECTOR_UNAVAILABLE)
        scores: dict[str, float] = {}
        keyword_ranks: dict[str, int] = {}
        vector_ranks: dict[str, int] = {}
        records: dict[str, SearchDocumentRecord] = {}
        for rank, record in enumerate(keyword, start=1):
            records[record.id] = record
            keyword_ranks[record.id] = rank
            scores[record.id] = scores.get(record.id, 0.0) + 1 / (self.rrf_k + rank)
        for rank, record in enumerate(vector, start=1):
            records[record.id] = record
            vector_ranks[record.id] = rank
            scores[record.id] = scores.get(record.id, 0.0) + 1 / (self.rrf_k + rank)
        ordered = sorted(records.values(), key=lambda item: (-scores[item.id], item.id))
        hits = [
            RetrievalHit(
                document_id=record.id,
                source_ref=f"search_document:{record.id}",
                artifact_id=record.artifact_id,
                modality=record.modality,
                start_ms=record.start_ms,
                end_ms=record.end_ms,
                content=record.text,
                keyword_rank=keyword_ranks.get(record.id),
                vector_rank=vector_ranks.get(record.id),
                score=scores[record.id],
            )
            for record in ordered[: request.limit]
        ]
        return RetrievalResult(
            status="partial" if vector_failed else "success",
            hits=hits,
            error_code=VECTOR_UNAVAILABLE if vector_failed else None,
        )

    def _filters(self, request: RetrievalRequest, modalities: Sequence[str]) -> list[Any]:
        filters: list[Any] = [
            SearchDocumentRecord.video_id == request.video_id,
            SearchDocumentRecord.modality.in_(modalities),
        ]
        if request.start_ms is not None and request.end_ms is not None:
            filters.extend(
                [
                    SearchDocumentRecord.start_ms < request.end_ms,
                    SearchDocumentRecord.end_ms > request.start_ms,
                ]
            )
        return filters

    def _keyword(
        self, request: RetrievalRequest, modalities: Sequence[str], limit: int
    ) -> list[SearchDocumentRecord]:
        if not modalities:
            return []
        normalized = normalize_text(request.query)
        lexemes = search_lexemes(request.query).split()
        expression = " | ".join(lexemes)
        with self.database.session() as session:
            match: Any
            rank: Any
            if expression:
                query = func.to_tsquery("simple", expression)
                match = SearchDocumentRecord.search_vector.op("@@")(query)
                rank = func.ts_rank_cd(SearchDocumentRecord.search_vector, query)
            else:
                match = SearchDocumentRecord.normalized_text.contains(normalized)
                rank = case((match, 1.0), else_=0.0)
            phrase = SearchDocumentRecord.normalized_text.contains(normalized)
            score = (rank + case((phrase, 1.0), else_=0.0)).label("keyword_score")
            statement = (
                select(SearchDocumentRecord)
                .where(*self._filters(request, modalities), or_(match, phrase))
                .order_by(score.desc(), SearchDocumentRecord.id)
                .limit(limit)
            )
            return list(session.scalars(statement).all())

    def _vector(
        self, request: RetrievalRequest, vector: list[float], limit: int
    ) -> list[SearchDocumentRecord]:
        distance = SearchDocumentRecord.embedding.cosine_distance(vector)
        with self.database.session() as session:
            statement = (
                select(SearchDocumentRecord)
                .where(
                    *self._filters(request, request.modalities),
                    SearchDocumentRecord.embedding.is_not(None),
                    SearchDocumentRecord.embedding_space == self.space,
                )
                .order_by(distance, SearchDocumentRecord.id)
                .limit(limit)
            )
            return list(session.scalars(statement).all())
