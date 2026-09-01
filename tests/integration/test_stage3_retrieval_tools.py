import asyncio
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy import text as sql_text

from evistream.config import Settings
from evistream.models import EmbeddingRequest, EmbeddingResponse, MockEmbeddingGateway
from evistream.models.profiles import ResolvedEmbeddingProfile
from evistream.models.types import ModelError, ModelErrorCode
from evistream.retrieval import EmbeddingIndexService, HybridRetrievalService
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.retrieval.types import RetrievalRequest
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    ArtifactRecord,
    CaseRecord,
    PolicyRecord,
    RequirementRecord,
    SearchDocumentRecord,
    SegmentRecord,
    ToolRunRecord,
    VideoRecord,
)
from evistream.tools import ToolExecutor, ToolRequest, build_default_registry


def mock_profile() -> ResolvedEmbeddingProfile:
    return ResolvedEmbeddingProfile(
        name="mock",
        gateway="mock",
        base_url=None,
        api_key=None,
        model="mock-embedding-v1",
        dimensions=1536,
        batch_size=10,
        timeout_seconds=5,
        max_attempts=1,
    )


class FailingEmbeddingGateway:
    @property
    def model_name(self) -> str:
        return "unavailable"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        raise ModelError(ModelErrorCode.UNAVAILABLE, "offline", retryable=True)


class PartiallyFailingEmbeddingGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.mock = MockEmbeddingGateway()

    @property
    def model_name(self) -> str:
        return "partial"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.calls += 1
        if self.calls == 2:
            raise ModelError(ModelErrorCode.UNAVAILABLE, "offline", retryable=True)
        return await self.mock.embed(request)


class SourceChangingEmbeddingGateway:
    def __init__(self, database: Database, document_id: str) -> None:
        self.database = database
        self.document_id = document_id
        self.mock = MockEmbeddingGateway()

    @property
    def model_name(self) -> str:
        return "source-changing"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        with self.database.session() as session:
            record = session.get(SearchDocumentRecord, self.document_id)
            assert record is not None
            record.text = f"{record.text} changed"
        return await self.mock.embed(request)


@pytest.mark.integration
def test_stage3_hybrid_retrieval_tools_and_clip_reuse(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is not installed")
    database = Database(database_url)
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    suffix = uuid4().hex[:10]
    video_id = f"vid_s3_{suffix}"
    case_id = f"case_s3_{suffix}"
    requirement_id = f"req_s3_{suffix}"
    source_uri = artifacts.put_file(
        Path("tests/fixtures/media/stage0_sample.mp4"), f"videos/{video_id}/source.mp4"
    )
    now = utc_now()
    with database.session() as session:
        video = VideoRecord(
            id=video_id,
            original_name="stage0_sample.mp4",
            artifact_uri=source_uri,
            fingerprint=None,
            duration_ms=30_000,
            width=640,
            height=360,
            container="mp4",
            video_codec="h264",
            has_audio=True,
            audio_codec="aac",
            status="READY",
            created_at=now,
            updated_at=now,
        )
        session.add(video)
        session.flush()
        source = ArtifactRecord(
            id=f"art_source_{suffix}",
            video_id=video_id,
            type="SOURCE_VIDEO",
            uri=source_uri,
            artifact_metadata={},
            created_at=now,
            updated_at=now,
        )
        session.add(source)
        segment = SegmentRecord(
            id=f"seg_s3_{suffix}",
            video_id=video_id,
            start_ms=0,
            end_ms=10_000,
            sequence=0,
            created_at=now,
            updated_at=now,
        )
        session.add(segment)
        session.flush()
        transcript_artifact = ArtifactRecord(
            id=f"art_transcript_{suffix}",
            video_id=video_id,
            type="TRANSCRIPT",
            uri=artifacts.write_text("evidence", f"videos/{video_id}/transcript.json"),
            artifact_metadata={},
            created_at=now,
            updated_at=now,
        )
        vision_artifact = ArtifactRecord(
            id=f"art_vision_{suffix}",
            video_id=video_id,
            segment_id=segment.id,
            type="VISUAL_DESCRIPTION",
            uri=artifacts.write_text("vision", f"videos/{video_id}/vision/0.json"),
            artifact_metadata={},
            created_at=now,
            updated_at=now,
        )
        ocr_artifact = ArtifactRecord(
            id=f"art_ocr_{suffix}",
            video_id=video_id,
            segment_id=segment.id,
            type="OCR",
            uri=artifacts.write_text("ocr", f"videos/{video_id}/ocr/0.json"),
            artifact_metadata={},
            created_at=now,
            updated_at=now,
        )
        session.add_all([transcript_artifact, vision_artifact, ocr_artifact])
        session.flush()
        for document_id, artifact, modality, start, end, text in [
            (
                f"doc_transcript_{suffix}",
                transcript_artifact,
                "transcript",
                1_000,
                3_000,
                "EviStream evidence retrieval verification",
            ),
            (
                f"doc_transcript_weapon_{suffix}",
                transcript_artifact,
                "transcript",
                5_000,
                7_000,
                "Weapon evidence context",
            ),
            (
                f"doc_transcript_context_{suffix}",
                transcript_artifact,
                "transcript",
                8_000,
                9_000,
                "Retrieval context explanation",
            ),
            (
                f"doc_ocr_{suffix}",
                ocr_artifact,
                "ocr",
                2_000,
                3_500,
                "WARNING EVIDENCE",
            ),
            (
                f"doc_vision_{suffix}",
                vision_artifact,
                "vision",
                4_000,
                8_000,
                "A synthetic safety keyframe",
            ),
        ]:
            session.add(
                SearchDocumentRecord(
                    id=document_id,
                    video_id=video_id,
                    segment_id=segment.id if modality == "vision" else None,
                    artifact_id=artifact.id,
                    modality=modality,
                    start_ms=start,
                    end_ms=end,
                    text=text,
                    normalized_text=normalize_text(text),
                    keyword_lexemes=search_lexemes(text),
                    created_at=now,
                    updated_at=now,
                )
            )
        policy = PolicyRecord(
            policy_id=f"test.stage3.{suffix}",
            version=1,
            name="Stage 3 test policy",
            severity="LOW",
            enabled=True,
            lifecycle="PUBLISHED",
            source_yaml="id: test",
            compiled_policy={},
            source_sha256="0" * 64,
            semantic_sha256="1" * 64,
            compiler_version="test",
            created_at=now,
            updated_at=now,
        )
        session.add(policy)
        session.flush()
        session.add(
            CaseRecord(
                id=case_id,
                video_id=video_id,
                policy_id=policy.policy_id,
                policy_version=1,
                model_profile="mock",
                status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            RequirementRecord(
                id=requirement_id,
                case_id=case_id,
                requirement_key="evidence",
                requirement_type="speech_content",
                source_kind="requirement",
                required=True,
                description="Find retrieval evidence",
                suggested_queries=["evidence retrieval"],
                modalities=["transcript"],
                tool_capabilities=["search_transcript"],
                semantic_sha256="2" * 64,
                status="PENDING",
                created_at=now,
                updated_at=now,
            )
        )

    gateway = MockEmbeddingGateway()
    profile = mock_profile()
    indexer = EmbeddingIndexService(database, gateway, profile)
    first = asyncio.run(indexer.index_video(video_id))
    second = asyncio.run(indexer.index_video(video_id))
    forced = asyncio.run(indexer.index_video(video_id, force=True))
    assert first.indexed == 5
    assert second.skipped == 5
    assert forced.indexed == 5

    retrieval = HybridRetrievalService(database, gateway, profile)
    result = asyncio.run(
        retrieval.search(
            RetrievalRequest(
                video_id=video_id,
                query="evidence retrieval",
                modalities=["transcript"],
                start_ms=0,
                end_ms=4_000,
            )
        )
    )
    assert result.status == "success"
    assert result.hits[0].start_ms == 1_000
    assert result.hits[0].keyword_rank == 1
    assert result.hits[0].vector_rank == 1

    fused = asyncio.run(
        retrieval.search(
            RetrievalRequest(
                video_id=video_id,
                query="evidence retrieval",
                modalities=["transcript"],
                limit=5,
            )
        )
    )
    assert len(fused.hits) >= 3
    assert [hit.document_id for hit in fused.hits] == [
        hit.document_id
        for hit in sorted(fused.hits, key=lambda hit: (-hit.score, hit.document_id))
    ]
    for hit in fused.hits:
        expected_score = sum(
            1 / (60 + rank)
            for rank in [hit.keyword_rank, hit.vector_rank]
            if rank is not None
        )
        assert hit.score == pytest.approx(expected_score)

    before_boundary = asyncio.run(
        retrieval.search(
            RetrievalRequest(
                video_id=video_id,
                query="evidence retrieval",
                modalities=["transcript"],
                start_ms=3_000,
                end_ms=5_000,
            )
        )
    )
    crossing_boundary = asyncio.run(
        retrieval.search(
            RetrievalRequest(
                video_id=video_id,
                query="evidence retrieval",
                modalities=["transcript"],
                start_ms=2_999,
                end_ms=3_001,
            )
        )
    )
    assert before_boundary.hits == []
    assert crossing_boundary.hits[0].document_id == f"doc_transcript_{suffix}"

    other_profile = profile.model_copy(update={"name": "mock-other-space"})
    other_space = HybridRetrievalService(database, gateway, other_profile)
    isolated = asyncio.run(
        other_space.search(
            RetrievalRequest(
                video_id=video_id,
                query="safety keyframe",
                modalities=["vision"],
            )
        )
    )
    assert isolated.status == "success"
    assert isolated.hits == []

    degraded = HybridRetrievalService(database, FailingEmbeddingGateway(), profile)
    degraded_text = asyncio.run(
        degraded.search(
            RetrievalRequest(
                video_id=video_id,
                query="evidence retrieval",
                modalities=["transcript"],
            )
        )
    )
    unavailable_vision = asyncio.run(
        degraded.search(
            RetrievalRequest(
                video_id=video_id,
                query="safety keyframe",
                modalities=["vision"],
            )
        )
    )
    assert degraded_text.status == "partial"
    assert degraded_text.hits[0].keyword_rank == 1
    assert unavailable_vision.status == "failed"
    assert unavailable_vision.error_code == "RETRIEVAL_VECTOR_UNAVAILABLE"

    settings = Settings(
        database_url=database_url,
        artifact_root=artifacts.root,
        process_timeout_seconds=60,
    )
    executor = ToolExecutor(
        database, build_default_registry(database, artifacts, settings, retrieval)
    )
    request = ToolRequest(
        correlation_id=f"corr_{suffix}",
        run_id=f"run_{suffix}",
        case_id=case_id,
        requirement_id=requirement_id,
        query="evidence retrieval",
        start_ms=0,
        end_ms=4_000,
    )
    tool_result = asyncio.run(executor.execute("search_transcript", request))
    duplicate = asyncio.run(executor.execute("search_transcript", request))
    assert tool_result.status == "success"
    assert tool_result.items[0].start_ms == 1_000
    assert duplicate.tool_run_id == tool_result.tool_run_id

    clip_request = request.model_copy(
        update={"query": "", "start_ms": 0, "end_ms": 2_000}
    )
    clip = asyncio.run(executor.execute("inspect_clip", clip_request))
    clip_duplicate = asyncio.run(executor.execute("inspect_clip", clip_request))
    assert clip.status == "success"
    assert clip.items[0].artifact_id == clip_duplicate.items[0].artifact_id
    expected_tools = {
        "expand_temporal_context",
        "find_counter_evidence",
        "get_neighbor_segments",
        "get_policy_requirement",
        "inspect_clip",
        "search_ocr",
        "search_transcript",
        "search_visual_caption",
    }
    registered = build_default_registry(database, artifacts, settings, retrieval).names()
    assert set(registered) == expected_tools
    requests = {
        "search_ocr": request.model_copy(update={"start_ms": None, "end_ms": None}),
        "search_visual_caption": request.model_copy(
            update={"query": "safety keyframe", "start_ms": None, "end_ms": None}
        ),
        "expand_temporal_context": clip_request,
        "get_neighbor_segments": clip_request,
        "find_counter_evidence": request.model_copy(
            update={"start_ms": None, "end_ms": None}
        ),
        "get_policy_requirement": request.model_copy(
            update={"query": "", "start_ms": None, "end_ms": None}
        ),
    }
    tool_outputs = {
        tool_name: asyncio.run(executor.execute(tool_name, tool_request))
        for tool_name, tool_request in requests.items()
    }
    assert all(result.status == "success" for result in tool_outputs.values())
    assert tool_outputs["search_ocr"].items[0].modality == "ocr"
    assert tool_outputs["search_visual_caption"].items[0].modality == "vision"

    degraded_executor = ToolExecutor(
        database,
        build_default_registry(database, artifacts, settings, degraded),
    )
    degraded_request = request.model_copy(update={"run_id": f"run_degraded_{suffix}"})
    partial_tool = asyncio.run(
        degraded_executor.execute(
            "search_transcript",
            degraded_request.model_copy(update={"start_ms": None, "end_ms": None}),
        )
    )
    failed_tool = asyncio.run(
        degraded_executor.execute(
            "search_visual_caption",
            degraded_request.model_copy(
                update={
                    "query": "safety keyframe",
                    "start_ms": None,
                    "end_ms": None,
                }
            ),
        )
    )
    assert partial_tool.status == "partial"
    assert failed_tool.status == "failed"
    with database.session() as session:
        assert session.scalar(
            select(func.count())
            .select_from(ToolRunRecord)
            .where(ToolRunRecord.run_id == request.run_id)
        ) == 8
        persisted_statuses = set(
            session.scalars(
                select(ToolRunRecord.status).where(
                    ToolRunRecord.run_id == degraded_request.run_id
                )
            ).all()
        )
        index_names = set(
            session.execute(
                sql_text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'search_documents'"
                )
            ).scalars()
        )
    assert persisted_statuses == {"partial", "failed"}
    assert {
        "ix_search_documents_search_vector",
        "ix_search_documents_embedding_hnsw",
    }.issubset(index_names)

    failed_profile = profile.model_copy(update={"name": "failed-space"})
    failed_summary = asyncio.run(
        EmbeddingIndexService(
            database, FailingEmbeddingGateway(), failed_profile
        ).index_video(video_id)
    )
    assert failed_summary.status == "failed"
    assert failed_summary.error_code == "EMBEDDING_INDEX_FAILED"
    assert failed_summary.failures[0].error_code == "MODEL_UNAVAILABLE"

    partial_profile = profile.model_copy(
        update={"name": "partial-space", "batch_size": 1}
    )
    partial_summary = asyncio.run(
        EmbeddingIndexService(
            database, PartiallyFailingEmbeddingGateway(), partial_profile
        ).index_video(video_id)
    )
    assert partial_summary.status == "partial"
    assert partial_summary.indexed == 4
    assert partial_summary.failed == 1
    assert partial_summary.error_code == "EMBEDDING_INDEX_PARTIAL"

    changed_document_id = f"doc_transcript_{suffix}"
    changed_profile = profile.model_copy(update={"name": "source-changed-space"})
    changed_summary = asyncio.run(
        EmbeddingIndexService(
            database,
            SourceChangingEmbeddingGateway(database, changed_document_id),
            changed_profile,
        ).index_video(video_id)
    )
    assert changed_summary.status == "partial"
    assert changed_summary.failures[0].document_ids == [changed_document_id]
    assert changed_summary.failures[0].error_code == "INDEX_SOURCE_CHANGED"
