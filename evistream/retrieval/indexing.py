"""Idempotent SearchDocument text and embedding indexing."""

from hashlib import sha256
from urllib.parse import urlsplit

from sqlalchemy import select

from evistream.models.embedding_types import EmbeddingGateway, EmbeddingRequest
from evistream.models.profiles import ResolvedEmbeddingProfile
from evistream.models.types import ModelError
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.retrieval.types import IndexFailure, IndexSummary
from evistream.storage.database import Database, utc_now
from evistream.storage.models import SearchDocumentRecord, VideoRecord


def embedding_space_id(profile: ResolvedEmbeddingProfile) -> str:
    endpoint = urlsplit(profile.base_url or "mock://local").netloc.lower()
    material = (
        f"{profile.name}\n{profile.gateway}\n{endpoint}\n"
        f"{profile.model}\n{profile.dimensions}"
    )
    return sha256(material.encode("utf-8")).hexdigest()


class EmbeddingIndexService:
    def __init__(
        self,
        database: Database,
        gateway: EmbeddingGateway,
        profile: ResolvedEmbeddingProfile,
    ) -> None:
        self.database = database
        self.gateway = gateway
        self.profile = profile
        self.space = embedding_space_id(profile)

    async def index_video(self, video_id: str, *, force: bool = False) -> IndexSummary:
        with self.database.session() as session:
            if session.get(VideoRecord, video_id) is None:
                raise LookupError(video_id)
            records = session.scalars(
                select(SearchDocumentRecord)
                .where(SearchDocumentRecord.video_id == video_id)
                .order_by(SearchDocumentRecord.id)
            ).all()
            snapshots = [(record.id, record.text) for record in records]

        pending: list[tuple[str, str, str]] = []
        skipped = 0
        with self.database.session() as session:
            for document_id, text in snapshots:
                record = session.get(SearchDocumentRecord, document_id)
                if record is None:
                    continue
                record.normalized_text = normalize_text(text)
                record.keyword_lexemes = search_lexemes(text)
                source_hash = sha256(text.encode("utf-8")).hexdigest()
                current = (
                    record.embedding is not None
                    and record.embedding_space == self.space
                    and record.embedding_source_sha256 == source_hash
                )
                if current and not force:
                    skipped += 1
                else:
                    pending.append((document_id, text, source_hash))

        indexed = 0
        failed = 0
        prompt_tokens = 0
        failures: list[IndexFailure] = []
        actual_model = self.profile.model
        batch_size = self.profile.batch_size
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            try:
                response = await self.gateway.embed(
                    EmbeddingRequest(
                        texts=tuple(text for _, text, _ in batch),
                        dimensions=self.profile.dimensions,
                        timeout_seconds=self.profile.timeout_seconds,
                        trace_id=f"index-{video_id}-{offset // batch_size}",
                    )
                )
            except ModelError as error:
                failed += len(batch)
                failures.append(
                    IndexFailure(
                        batch_index=offset // batch_size,
                        document_ids=[document_id for document_id, _, _ in batch],
                        error_code=str(error.code),
                        retryable=error.retryable,
                    )
                )
                continue
            prompt_tokens += response.usage.prompt_tokens
            actual_model = response.actual_model
            with self.database.session() as session:
                for (document_id, source_text, source_hash), vector in zip(
                    batch, response.vectors, strict=True
                ):
                    record = session.get(SearchDocumentRecord, document_id)
                    if record is None or record.text != source_text:
                        failed += 1
                        failures.append(
                            IndexFailure(
                                batch_index=offset // batch_size,
                                document_ids=[document_id],
                                error_code="INDEX_SOURCE_CHANGED",
                                retryable=True,
                            )
                        )
                        continue
                    record.embedding = vector.values
                    record.embedding_space = self.space
                    record.embedding_model = response.actual_model
                    record.embedding_source_sha256 = source_hash
                    record.embedding_updated_at = utc_now()
                    indexed += 1
        status = "success"
        error_code = None
        if failed:
            status = "partial" if indexed or skipped else "failed"
            error_code = (
                "EMBEDDING_INDEX_PARTIAL"
                if status == "partial"
                else "EMBEDDING_INDEX_FAILED"
            )
        return IndexSummary(
            status=status,
            error_code=error_code,
            video_id=video_id,
            total=len(snapshots),
            indexed=indexed,
            skipped=skipped,
            failed=failed,
            actual_model=actual_model,
            embedding_space=self.space,
            dimensions=self.profile.dimensions,
            prompt_tokens=prompt_tokens,
            failures=failures,
        )
