"""Stage 1 media registration and preprocessing application services."""

from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update

from evistream.application.types import JobHandlerError, JobRequest, JobStatus
from evistream.config import Settings
from evistream.media.asr.types import ASRAdapter, ASRRequest, ASRResponse
from evistream.media.extractors import OCRAdapter, VisualDescriptionAdapter
from evistream.media.probe import probe_video
from evistream.media.segmenter import FFmpegSceneSegmenter, FixedWindowSegmenter, extract_keyframe
from evistream.media.types import ArtifactType, MediaJob, SegmentBoundary, Video, VideoStatus
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    ArtifactRecord,
    ProcessingJobRecord,
    SearchDocumentRecord,
    SegmentRecord,
    VideoRecord,
)


class MediaApplicationService:
    def __init__(
        self,
        database: Database,
        artifacts: LocalArtifactStore,
        settings: Settings,
        asr: ASRAdapter,
        ocr: OCRAdapter,
        vision: VisualDescriptionAdapter,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.settings = settings
        self.asr = asr
        self.ocr = ocr
        self.vision = vision

    def register_file(
        self,
        source: Path,
        original_name: str | None = None,
        model_profile: str | None = None,
    ) -> tuple[Video, MediaJob]:
        resolved = source.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        if resolved.stat().st_size > self.settings.upload_max_bytes:
            raise ValueError("uploaded file exceeds configured limit")
        metadata = probe_video(
            resolved,
            ffprobe_binary=self.settings.ffprobe_binary,
            timeout_seconds=self.settings.process_timeout_seconds,
        )
        if metadata.duration_ms > self.settings.video_max_duration_seconds * 1000:
            raise ValueError("video duration exceeds configured limit")
        if (
            metadata.width > self.settings.video_max_width
            or metadata.height > self.settings.video_max_height
        ):
            raise ValueError("video resolution exceeds configured limit")
        fingerprint = _sha256_file(resolved)
        selected_profile = model_profile or self.settings.model_profile
        request_key = sha256(f"MEDIA_PREPROCESS:{fingerprint}".encode()).hexdigest()
        with self.database.session() as session:
            existing_job = session.scalar(
                select(ProcessingJobRecord).where(
                    ProcessingJobRecord.request_key == request_key
                )
            )
            if existing_job is not None:
                existing_video = session.get(VideoRecord, existing_job.subject_id)
                if existing_job.type != "MEDIA_PREPROCESS" or existing_video is None:
                    raise RuntimeError("media request key belongs to an invalid job")
                if existing_video.model_profile != selected_profile:
                    raise JobHandlerError(
                        "VIDEO_PROFILE_CONFLICT",
                        "the existing video uses another model profile",
                    )
                return _video(existing_video), _job(existing_job)
            existing = session.scalar(
                select(VideoRecord).where(VideoRecord.fingerprint == fingerprint)
            )
            if existing is not None:
                if existing.model_profile != selected_profile:
                    raise JobHandlerError(
                        "VIDEO_PROFILE_CONFLICT",
                        "the existing video uses another model profile",
                    )
                job = session.scalar(
                    select(ProcessingJobRecord).where(
                        ProcessingJobRecord.subject_id == existing.id,
                        ProcessingJobRecord.type == "MEDIA_PREPROCESS",
                    )
                )
                if job is None:
                    raise RuntimeError("video exists without preprocessing job")
                return _video(existing), _job(job)

        video_id = f"vid_{uuid4().hex}"
        suffix = resolved.suffix.lower() or ".bin"
        uri = self.artifacts.put_file(resolved, f"videos/{video_id}/source{suffix}")
        now = utc_now()
        video_record = VideoRecord(
            id=video_id,
            original_name=original_name or resolved.name,
            artifact_uri=uri,
            fingerprint=fingerprint,
            duration_ms=metadata.duration_ms,
            width=metadata.width,
            height=metadata.height,
            container=metadata.container,
            video_codec=metadata.video_codec,
            has_audio=metadata.has_audio,
            audio_codec=metadata.audio_codec,
            status=VideoStatus.UPLOADED,
            model_profile=selected_profile,
            triage_status="PENDING",
            created_at=now,
            updated_at=now,
        )
        job_record = ProcessingJobRecord(
            id=f"job_{uuid4().hex}",
            type="MEDIA_PREPROCESS",
            subject_id=video_id,
            request_key=request_key,
            correlation_id=f"corr_{uuid4().hex}",
            status=JobStatus.PENDING,
            attempt=0,
            max_attempts=self.settings.job_max_attempts,
            payload={"video_id": video_id, "model_profile": selected_profile},
            retryable=False,
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(video_record)
            session.flush()
            session.add_all(
                [
                    ArtifactRecord(
                        id=f"art_{uuid4().hex}",
                        video_id=video_id,
                        type=ArtifactType.SOURCE_VIDEO,
                        uri=uri,
                        artifact_metadata={"sha256": fingerprint},
                        created_at=now,
                        updated_at=now,
                    ),
                    job_record,
                ]
            )
        return _video(video_record), _job(job_record)

    def build_job_request(self, job_id: str) -> JobRequest:
        with self.database.session() as session:
            record = session.get(ProcessingJobRecord, job_id)
            if record is None:
                raise LookupError(job_id)
            return JobRequest(
                job_id=record.id,
                job_type=record.type,
                request_key=record.request_key,
                correlation_id=record.correlation_id,
                payload=record.payload or {
                    "video_id": record.subject_id,
                    "model_profile": "mock",
                },
            )

    def process_job(self, job_id: str, *, finalize: bool = True) -> MediaJob:
        with self.database.session() as session:
            job = session.get(ProcessingJobRecord, job_id)
            if job is None:
                raise LookupError(job_id)
            if job.status == JobStatus.SUCCEEDED:
                return _job(job)
            if (
                job.status == JobStatus.RUNNING
                and job.lease_until is not None
                and job.lease_until > utc_now()
            ):
                raise JobHandlerError("JOB_ALREADY_RUNNING", f"job is already running: {job_id}")
            if (
                job.status in {JobStatus.FAILED, JobStatus.CANCELLED}
                or job.attempt >= job.max_attempts
            ):
                raise JobHandlerError("JOB_NOT_RETRYABLE", f"job cannot be retried: {job_id}")
            now = utc_now()
            claimed = session.execute(
                update(ProcessingJobRecord)
                .where(
                    ProcessingJobRecord.id == job_id,
                    (
                        ProcessingJobRecord.status.in_([JobStatus.PENDING, JobStatus.RETRY_WAIT])
                        | (
                            (ProcessingJobRecord.status == JobStatus.RUNNING)
                            & (ProcessingJobRecord.lease_until < now)
                        )
                    ),
                    ProcessingJobRecord.attempt < ProcessingJobRecord.max_attempts,
                )
                .values(
                    status=JobStatus.RUNNING,
                    attempt=ProcessingJobRecord.attempt + 1,
                    started_at=now,
                    finished_at=None,
                    error_code=None,
                    lease_until=now + timedelta(seconds=self.settings.media_job_lease_seconds),
                    updated_at=now,
                )
                .returning(ProcessingJobRecord.subject_id)
            ).scalar_one_or_none()
            if claimed is None:
                raise JobHandlerError("JOB_ALREADY_RUNNING", f"job was claimed: {job_id}")
            video = session.get(VideoRecord, claimed)
            if video is None:
                raise LookupError(claimed)
            already_extracted = video.status == VideoStatus.READY
            video.status = VideoStatus.PROCESSING if not already_extracted else VideoStatus.READY
            source_path = self.artifacts.resolve(video.artifact_uri)

        try:
            if not already_extracted:
                self._extract(video.id, source_path, video.duration_ms, video.has_audio)
        except Exception:
            with self.database.session() as session:
                failed_job = session.get(ProcessingJobRecord, job_id)
                failed_video = session.get(VideoRecord, video.id)
                if failed_job is not None:
                    failed_job.status = JobStatus.FAILED
                    failed_job.error_code = "MEDIA_DECODE_FAILED"
                    failed_job.finished_at = utc_now()
                    failed_job.lease_until = None
                if failed_video is not None:
                    failed_video.status = VideoStatus.FAILED
            raise

        with self.database.session() as session:
            completed_job = session.get(ProcessingJobRecord, job_id)
            completed_video = session.get(VideoRecord, video.id)
            if completed_job is None or completed_video is None:
                raise RuntimeError("media state disappeared")
            completed_video.status = VideoStatus.READY
            if finalize:
                completed_job.status = JobStatus.SUCCEEDED
                completed_job.finished_at = utc_now()
                completed_job.lease_until = None
            return _job(completed_job)

    def complete_job(self, job_id: str) -> MediaJob:
        with self.database.session() as session:
            job = session.get(ProcessingJobRecord, job_id)
            if job is None:
                raise LookupError(job_id)
            job.status = JobStatus.SUCCEEDED
            job.finished_at = utc_now()
            job.lease_until = None
            job.next_attempt_at = None
            job.retryable = False
            job.error_code = None
            job.error_message = None
            return _job(job)

    def fail_job(self, job_id: str, code: str, message: str, *, retryable: bool) -> MediaJob:
        with self.database.session() as session:
            job = session.get(ProcessingJobRecord, job_id)
            if job is None:
                raise LookupError(job_id)
            can_retry = retryable and job.attempt < job.max_attempts
            job.status = JobStatus.RETRY_WAIT if can_retry else JobStatus.FAILED
            job.retryable = can_retry
            job.error_code = code
            job.error_message = message[:2000]
            job.lease_until = None
            job.finished_at = None if can_retry else utc_now()
            if can_retry:
                interval_index = min(
                    max(job.attempt - 1, 0), len(self.settings.job_retry_intervals) - 1
                )
                delay_seconds = (
                    self.settings.job_retry_intervals[interval_index]
                    if self.settings.job_retry_intervals
                    else 0
                )
                job.next_attempt_at = utc_now() + timedelta(seconds=delay_seconds)
            else:
                job.next_attempt_at = None
            return _job(job)

    def get_video(self, video_id: str) -> Video:
        with self.database.session() as session:
            record = session.get(VideoRecord, video_id)
            if record is None:
                raise LookupError(video_id)
            return _video(record)

    def get_job(self, job_id: str) -> MediaJob:
        with self.database.session() as session:
            record = session.get(ProcessingJobRecord, job_id)
            if record is None:
                raise LookupError(job_id)
            return _job(record)

    def simulate_stream(self, video_id: str) -> list[SegmentBoundary]:
        video = self.get_video(video_id)
        return FixedWindowSegmenter(
            self.settings.simulated_stream_segment_seconds * 1000
        ).segment(video.duration_ms)

    def _extract(self, video_id: str, source: Path, duration_ms: int, has_audio: bool) -> None:
        segmenter = FFmpegSceneSegmenter(self.settings.ffmpeg_binary, self.settings.scene_threshold)
        boundaries = segmenter.segment(source, duration_ms, self.settings.process_timeout_seconds)
        now = utc_now()
        with self.database.session() as session:
            for sequence, boundary in enumerate(boundaries):
                segment_id = f"seg_{uuid4().hex}"
                segment = SegmentRecord(
                    id=segment_id,
                    video_id=video_id,
                    start_ms=boundary.start_ms,
                    end_ms=boundary.end_ms,
                    sequence=sequence,
                    created_at=now,
                    updated_at=now,
                )
                session.add(segment)
                session.flush()
                key = f"videos/{video_id}/keyframes/{sequence:04d}.jpg"
                target = self.artifacts.path_for_key(key)
                extract_keyframe(
                    source,
                    target,
                    (boundary.start_ms + boundary.end_ms) // 2,
                    ffmpeg_binary=self.settings.ffmpeg_binary,
                    timeout_seconds=self.settings.process_timeout_seconds,
                )
                uri = self.artifacts.uri_for_key(key)
                artifact = ArtifactRecord(
                    id=f"art_{uuid4().hex}",
                    video_id=video_id,
                    segment_id=segment_id,
                    type=ArtifactType.KEYFRAME,
                    uri=uri,
                    artifact_metadata={"at_ms": (boundary.start_ms + boundary.end_ms) // 2},
                    created_at=now,
                    updated_at=now,
                )
                session.add(artifact)
                session.flush()
                observations = self.ocr.extract(target)
                description = self.vision.describe(target)
                for modality, observation in [
                    *(("ocr", item) for item in observations),
                    ("vision", description),
                ]:
                    text_artifact = ArtifactRecord(
                        id=f"art_{uuid4().hex}",
                        video_id=video_id,
                        segment_id=segment_id,
                        type=(
                            ArtifactType.OCR
                            if modality == "ocr"
                            else ArtifactType.VISUAL_DESCRIPTION
                        ),
                        uri=self.artifacts.write_text(
                            observation.model_dump_json(),
                            f"videos/{video_id}/{modality}/{sequence:04d}-{uuid4().hex}.json",
                        ),
                        artifact_metadata={"model": observation.model},
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(text_artifact)
                    session.flush()
                    session.add(
                        SearchDocumentRecord(
                            id=f"doc_{uuid4().hex}",
                            video_id=video_id,
                            segment_id=segment_id,
                            artifact_id=text_artifact.id,
                            modality=modality,
                            start_ms=boundary.start_ms,
                            end_ms=boundary.end_ms,
                            text=observation.text,
                            normalized_text=normalize_text(observation.text),
                            keyword_lexemes=search_lexemes(observation.text),
                            created_at=now,
                            updated_at=now,
                        )
                    )

            transcript = (
                self.asr.transcribe(ASRRequest(media_path=source))
                if has_audio
                else ASRResponse(
                    segments=[],
                    language=None,
                    model="none",
                    duration_ms=duration_ms,
                )
            )
            transcript_artifact = ArtifactRecord(
                id=f"art_{uuid4().hex}",
                video_id=video_id,
                type=ArtifactType.TRANSCRIPT,
                uri=self.artifacts.write_text(
                    transcript.model_dump_json(indent=2),
                    f"videos/{video_id}/transcript.json",
                ),
                artifact_metadata={"model": transcript.model},
                created_at=now,
                updated_at=now,
            )
            session.add(transcript_artifact)
            session.flush()
            for item in transcript.segments:
                session.add(
                    SearchDocumentRecord(
                        id=f"doc_{uuid4().hex}",
                        video_id=video_id,
                        artifact_id=transcript_artifact.id,
                        modality="transcript",
                        start_ms=item.start_ms,
                        end_ms=item.end_ms,
                        text=item.text,
                        normalized_text=normalize_text(item.text),
                        keyword_lexemes=search_lexemes(item.text),
                        created_at=now,
                        updated_at=now,
                    )
                )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _video(record: VideoRecord) -> Video:
    return Video(
        video_id=record.id,
        original_name=record.original_name,
        artifact_uri=record.artifact_uri,
        fingerprint=record.fingerprint,
        duration_ms=record.duration_ms,
        width=record.width,
        height=record.height,
        container=record.container,
        video_codec=record.video_codec,
        has_audio=record.has_audio,
        audio_codec=record.audio_codec,
        status=record.status,
        model_profile=record.model_profile,
        triage_status=record.triage_status,
    )


def _job(record: ProcessingJobRecord) -> MediaJob:
    return MediaJob(
        job_id=record.id,
        video_id=record.subject_id,
        request_key=record.request_key,
        status=record.status,
        attempt=record.attempt,
        error_code=record.error_code,
        error_message=record.error_message,
        retryable=record.retryable,
    )
