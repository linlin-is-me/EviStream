"""Core retrieval, media and policy tools required by Stage 3."""

import json
import subprocess
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from evistream.config import Settings
from evistream.media.types import ArtifactType
from evistream.retrieval.service import HybridRetrievalService
from evistream.retrieval.temporal import expand_window
from evistream.retrieval.types import RetrievalRequest, RetrievalResult
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    ArtifactRecord,
    CaseRecord,
    RequirementRecord,
    SearchDocumentRecord,
    SegmentRecord,
    VideoRecord,
)
from evistream.tools.registry import ToolRegistry
from evistream.tools.types import ToolItem, ToolOutput, ToolRequest


class SearchTool:
    def __init__(
        self,
        name: str,
        retrieval: HybridRetrievalService,
        modalities: list[str],
    ) -> None:
        self.name = name
        self.retrieval = retrieval
        self.modalities = modalities

    async def execute(self, request: ToolRequest) -> ToolOutput:
        video_id = _case_video(self.retrieval.database, request.case_id)
        result = await self.retrieval.search(
            RetrievalRequest(
                video_id=video_id,
                query=request.query,
                modalities=self.modalities,
                start_ms=request.start_ms,
                end_ms=request.end_ms,
                limit=request.limit,
            )
        )
        return _retrieval_output(result)


class InspectClipTool:
    name = "inspect_clip"

    def __init__(
        self, database: Database, artifacts: LocalArtifactStore, settings: Settings
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.settings = settings

    async def execute(self, request: ToolRequest) -> ToolOutput:
        assert request.start_ms is not None and request.end_ms is not None
        if request.end_ms - request.start_ms > self.settings.tool_clip_max_seconds * 1000:
            return ToolOutput(status="failed", error_code="TOOL_INPUT_INVALID")
        with self.database.session() as session:
            case = session.get(CaseRecord, request.case_id)
            if case is None:
                return ToolOutput(status="failed", error_code="CASE_NOT_FOUND")
            video = session.get(VideoRecord, case.video_id)
            if video is None or request.end_ms > video.duration_ms:
                return ToolOutput(status="failed", error_code="TOOL_INPUT_INVALID")
            source = self.artifacts.resolve(video.artifact_uri)
            key_hash = sha256(
                f"{video.id}:{request.start_ms}:{request.end_ms}".encode()
            ).hexdigest()[:24]
            key = f"videos/{video.id}/clips/{key_hash}.mp4"
            uri = self.artifacts.uri_for_key(key)
            existing = session.scalar(select(ArtifactRecord).where(ArtifactRecord.uri == uri))
            if existing is not None:
                return ToolOutput(items=[_clip_item(existing, request.start_ms, request.end_ms)])

        target = self.artifacts.path_for_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        _extract_clip(
            source,
            target,
            request.start_ms,
            request.end_ms,
            self.settings.ffmpeg_binary,
            self.settings.process_timeout_seconds,
        )
        now = utc_now()
        with self.database.session() as session:
            existing = session.scalar(select(ArtifactRecord).where(ArtifactRecord.uri == uri))
            if existing is None:
                existing = ArtifactRecord(
                    id=f"art_{uuid4().hex}",
                    video_id=video.id,
                    type=ArtifactType.CLIP,
                    uri=uri,
                    artifact_metadata={
                        "start_ms": request.start_ms,
                        "end_ms": request.end_ms,
                    },
                    created_at=now,
                    updated_at=now,
                )
                session.add(existing)
                session.flush()
            return ToolOutput(items=[_clip_item(existing, request.start_ms, request.end_ms)])


class ExpandTemporalContextTool:
    name = "expand_temporal_context"

    def __init__(self, database: Database, context_ms: int) -> None:
        self.database = database
        self.context_ms = context_ms

    async def execute(self, request: ToolRequest) -> ToolOutput:
        assert request.start_ms is not None and request.end_ms is not None
        with self.database.session() as session:
            case = session.get(CaseRecord, request.case_id)
            if case is None:
                return ToolOutput(status="failed", error_code="CASE_NOT_FOUND")
            video = session.get(VideoRecord, case.video_id)
            if video is None:
                return ToolOutput(status="failed", error_code="MEDIA_ARTIFACT_NOT_FOUND")
            window = expand_window(
                request.start_ms, request.end_ms, video.duration_ms, self.context_ms
            )
            records = session.scalars(
                select(SearchDocumentRecord)
                .where(
                    SearchDocumentRecord.video_id == video.id,
                    SearchDocumentRecord.start_ms < window.end_ms,
                    SearchDocumentRecord.end_ms > window.start_ms,
                )
                .order_by(SearchDocumentRecord.start_ms, SearchDocumentRecord.id)
                .limit(request.limit)
            ).all()
            return ToolOutput(items=[_document_item(item) for item in records])


class NeighborSegmentsTool:
    name = "get_neighbor_segments"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def execute(self, request: ToolRequest) -> ToolOutput:
        assert request.start_ms is not None and request.end_ms is not None
        with self.database.session() as session:
            video_id = _case_video_from_session(session, request.case_id)
            overlaps = session.scalars(
                select(SegmentRecord).where(
                    SegmentRecord.video_id == video_id,
                    SegmentRecord.start_ms < request.end_ms,
                    SegmentRecord.end_ms > request.start_ms,
                )
            ).all()
            if not overlaps:
                return ToolOutput(items=[])
            low = min(item.sequence for item in overlaps) - 1
            high = max(item.sequence for item in overlaps) + 1
            segments = session.scalars(
                select(SegmentRecord)
                .where(
                    SegmentRecord.video_id == video_id,
                    SegmentRecord.sequence >= max(0, low),
                    SegmentRecord.sequence <= high,
                )
                .order_by(SegmentRecord.sequence)
                .limit(request.limit)
            ).all()
            items: list[ToolItem] = []
            for segment in segments:
                description = session.scalar(
                    select(SearchDocumentRecord).where(
                        SearchDocumentRecord.segment_id == segment.id,
                        SearchDocumentRecord.modality == "vision",
                    )
                )
                if description is not None:
                    items.append(_document_item(description))
                    continue
                keyframe = session.scalar(
                    select(ArtifactRecord).where(
                        ArtifactRecord.segment_id == segment.id,
                        ArtifactRecord.type == ArtifactType.KEYFRAME,
                    )
                )
                items.append(
                    ToolItem(
                        source_ref=f"segment:{segment.id}",
                        artifact_id=keyframe.id if keyframe else None,
                        modality="segment",
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        content=f"segment {segment.sequence}",
                    )
                )
            return ToolOutput(items=items)


class PolicyRequirementTool:
    name = "get_policy_requirement"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def execute(self, request: ToolRequest) -> ToolOutput:
        with self.database.session() as session:
            requirement = session.get(RequirementRecord, request.requirement_id)
            case = session.get(CaseRecord, request.case_id)
            if requirement is None or case is None:
                return ToolOutput(status="failed", error_code="REQUIREMENT_NOT_FOUND")
            video = session.get(VideoRecord, case.video_id)
            if video is None or video.duration_ms <= 0:
                return ToolOutput(status="failed", error_code="MEDIA_ARTIFACT_NOT_FOUND")
            content = json.dumps(
                {
                    "requirement_key": requirement.requirement_key,
                    "requirement_type": requirement.requirement_type,
                    "description": requirement.description,
                    "suggested_queries": requirement.suggested_queries,
                    "modalities": requirement.modalities,
                    "tool_capabilities": requirement.tool_capabilities,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            return ToolOutput(
                items=[
                    ToolItem(
                        source_ref=f"requirement:{requirement.id}",
                        modality="policy",
                        start_ms=0,
                        end_ms=video.duration_ms,
                        content=content,
                    )
                ]
            )


def build_default_registry(
    database: Database,
    artifacts: LocalArtifactStore,
    settings: Settings,
    retrieval: HybridRetrievalService,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SearchTool("search_transcript", retrieval, ["transcript"]))
    registry.register(SearchTool("search_ocr", retrieval, ["ocr"]))
    registry.register(SearchTool("search_visual_caption", retrieval, ["vision"]))
    registry.register(InspectClipTool(database, artifacts, settings))
    registry.register(ExpandTemporalContextTool(database, settings.retrieval_context_ms))
    registry.register(NeighborSegmentsTool(database))
    registry.register(
        SearchTool("find_counter_evidence", retrieval, ["transcript", "ocr", "vision"])
    )
    registry.register(PolicyRequirementTool(database))
    return registry


def _case_video(database: Database, case_id: str) -> str:
    with database.session() as session:
        return _case_video_from_session(session, case_id)


def _case_video_from_session(session: Session, case_id: str) -> str:
    case = session.get(CaseRecord, case_id)
    if case is None:
        raise LookupError(case_id)
    return case.video_id


def _retrieval_output(result: RetrievalResult) -> ToolOutput:
    return ToolOutput(
        status=result.status,
        error_code=result.error_code,
        items=[
            ToolItem(
                source_ref=hit.source_ref,
                artifact_id=hit.artifact_id,
                modality=hit.modality,
                start_ms=hit.start_ms,
                end_ms=hit.end_ms,
                content=hit.content,
                score=hit.score,
            )
            for hit in result.hits
        ],
    )


def _document_item(record: SearchDocumentRecord) -> ToolItem:
    return ToolItem(
        source_ref=f"search_document:{record.id}",
        artifact_id=record.artifact_id,
        modality=record.modality,
        start_ms=record.start_ms,
        end_ms=record.end_ms,
        content=record.text,
    )


def _clip_item(record: ArtifactRecord, start_ms: int, end_ms: int) -> ToolItem:
    return ToolItem(
        source_ref=f"artifact:{record.id}",
        artifact_id=record.id,
        modality="video",
        start_ms=start_ms,
        end_ms=end_ms,
        content=f"clip {start_ms}-{end_ms} ms",
    )


def _extract_clip(
    source: Path,
    target: Path,
    start_ms: int,
    end_ms: int,
    ffmpeg_binary: str,
    timeout_seconds: float,
) -> None:
    completed = subprocess.run(
        [
            ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            str(source),
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-y",
            str(target),
        ],
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file():
        target.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg clip extraction failed")
