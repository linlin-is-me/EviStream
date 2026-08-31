import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from evistream.config import Settings
from evistream.media.asr import MockASR
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
    with database.session() as session:
        for table in [
            SearchDocumentRecord,
            ArtifactRecord,
            SegmentRecord,
            ProcessingJobRecord,
            VideoRecord,
        ]:
            session.execute(delete(table))

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

    completed = service.process_job(job.job_id)
    assert completed.status == "SUCCEEDED"
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
        assert session.scalar(select(func.count()).select_from(VideoRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ProcessingJobRecord)) == 1
        assert session.scalar(select(func.count()).select_from(SegmentRecord)) >= 1
        assert session.scalar(select(func.count()).select_from(ArtifactRecord)) >= 4
        assert session.scalar(select(func.count()).select_from(SearchDocumentRecord)) >= 2
