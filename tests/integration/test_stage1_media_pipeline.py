import asyncio
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from sqlalchemy import func, select

from evistream.application import (
    HandlerRegistry,
    InlineExecutor,
    JobExecution,
    MediaPreprocessJobHandler,
)
from evistream.config import Settings
from evistream.media.asr import ASRRequest, ASRResponse, MockASR
from evistream.media.extractors import MockOCR, MockVisualDescription
from evistream.media.service import MediaApplicationService
from evistream.media.types import VideoStatus
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database
from evistream.storage.models import (
    ArtifactRecord,
    ProcessingJobRecord,
    SearchDocumentRecord,
    SegmentRecord,
    VideoRecord,
)


@pytest.mark.integration
def test_stage1_pipeline_persists_and_deduplicates(tmp_path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg is not installed")
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    settings = Settings(
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
        process_timeout_seconds=60,
    )
    service = MediaApplicationService(
        database,
        LocalArtifactStore(settings.artifact_root),
        settings,
        MockASR(),
        MockOCR(),
        MockVisualDescription(),
    )
    fixture = Path("tests/fixtures/media/stage0_sample.mp4")
    video, job = service.register_file(fixture)
    duplicate_video, duplicate_job = service.register_file(fixture)
    assert duplicate_video.video_id == video.video_id
    assert duplicate_job.job_id == job.job_id

    executor = _executor(service)
    execution = asyncio.run(executor.dispatch(service.build_job_request(job.job_id)))
    assert execution.status == "SUCCEEDED"
    assert service.get_video(video.video_id).status == VideoStatus.READY
    assert len(service.simulate_stream(video.video_id)) == 3
    assert service.process_job(job.job_id).job_id == job.job_id

    restarted = MediaApplicationService(
        Database(database_url),
        LocalArtifactStore(settings.artifact_root),
        settings,
        MockASR(),
        MockOCR(),
        MockVisualDescription(),
    )
    assert restarted.get_video(video.video_id).status == VideoStatus.READY
    with database.session() as session:
        assert session.scalar(
            select(func.count()).select_from(VideoRecord).where(VideoRecord.id == video.video_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(ProcessingJobRecord)
            .where(ProcessingJobRecord.subject_id == video.video_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(SegmentRecord)
            .where(SegmentRecord.video_id == video.video_id)
        ) >= 1
        artifacts = session.scalars(
            select(ArtifactRecord).where(ArtifactRecord.video_id == video.video_id)
        ).all()
        documents = session.scalars(
            select(SearchDocumentRecord).where(SearchDocumentRecord.video_id == video.video_id)
        ).all()
        assert len(artifacts) >= 4
        assert len(documents) >= 2
        for artifact in artifacts:
            assert restarted.artifacts.resolve(artifact.uri).is_file()
            if artifact.type in {"TRANSCRIPT", "OCR", "VISUAL_DESCRIPTION"}:
                json.loads(restarted.artifacts.resolve(artifact.uri).read_text(encoding="utf-8"))


@pytest.mark.integration
def test_media_job_claim_prevents_concurrent_execution(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url or shutil.which("ffmpeg") is None:
        pytest.skip("PostgreSQL and FFmpeg are required")
    source = tmp_path / "concurrent.mp4"
    source.write_bytes(Path("tests/fixtures/media/stage0_sample.mp4").read_bytes() + b"claim")
    started = threading.Event()
    release = threading.Event()

    class BlockingASR:
        def transcribe(self, request: ASRRequest) -> ASRResponse:
            started.set()
            if not release.wait(timeout=10):
                raise TimeoutError("test did not release ASR")
            return MockASR().transcribe(request)

    settings = Settings(
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
        process_timeout_seconds=60,
    )
    service = MediaApplicationService(
        Database(database_url),
        LocalArtifactStore(settings.artifact_root),
        settings,
        BlockingASR(),
        MockOCR(),
        MockVisualDescription(),
    )
    _, job = service.register_file(source)
    request = service.build_job_request(job.job_id)
    executor = _executor(service)

    async def run_concurrently() -> tuple[JobExecution, JobExecution]:
        first_task = asyncio.create_task(executor.dispatch(request))
        assert await asyncio.to_thread(started.wait, 10)
        second = await executor.dispatch(request)
        release.set()
        return await first_task, second

    first, second = asyncio.run(run_concurrently())
    assert first.status == "SUCCEEDED"
    assert second.error_code == "JOB_ALREADY_RUNNING"
    assert service.get_job(job.job_id).attempt == 1


@pytest.mark.integration
def test_media_registration_rejects_resolution_before_copying_source(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url or shutil.which("ffprobe") is None:
        pytest.skip("PostgreSQL and FFprobe are required")
    settings = Settings(
        database_url=database_url,
        artifact_root=tmp_path / "limited-artifacts",
        video_max_width=320,
    )
    service = MediaApplicationService(
        Database(database_url),
        LocalArtifactStore(settings.artifact_root),
        settings,
        MockASR(),
        MockOCR(),
        MockVisualDescription(),
    )

    with pytest.raises(ValueError, match="video resolution exceeds configured limit"):
        service.register_file(Path("tests/fixtures/media/stage0_sample.mp4"))

    assert list(settings.artifact_root.rglob("*")) == []


@pytest.mark.integration
def test_silent_video_persists_empty_transcript_without_calling_asr(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url or shutil.which("ffmpeg") is None:
        pytest.skip("PostgreSQL and FFmpeg are required")
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "tests/fixtures/media/stage0_sample.mp4",
            "-an",
            "-c:v",
            "copy",
            str(silent),
        ],
        check=True,
    )

    class UnexpectedASR:
        def transcribe(self, request: ASRRequest) -> ASRResponse:
            raise AssertionError("ASR must not run for a silent video")

    settings = Settings(
        database_url=database_url,
        artifact_root=tmp_path / "silent-artifacts",
        process_timeout_seconds=60,
    )
    service = MediaApplicationService(
        Database(database_url),
        LocalArtifactStore(settings.artifact_root),
        settings,
        UnexpectedASR(),
        MockOCR(),
        MockVisualDescription(),
    )
    video, job = service.register_file(silent)
    result = asyncio.run(_executor(service).dispatch(service.build_job_request(job.job_id)))
    assert result.status == "SUCCEEDED"
    with service.database.session() as session:
        transcript = session.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.video_id == video.video_id,
                ArtifactRecord.type == "TRANSCRIPT",
            )
        )
        assert transcript is not None
        payload = json.loads(service.artifacts.resolve(transcript.uri).read_text(encoding="utf-8"))
        assert payload["segments"] == []
        assert payload["model"] == "none"


def _executor(service: MediaApplicationService) -> InlineExecutor:
    registry = HandlerRegistry()
    registry.register("MEDIA_PREPROCESS", MediaPreprocessJobHandler(service))
    return InlineExecutor(registry)
