"""Persistent media pipeline contracts."""

from enum import StrEnum

from pydantic import BaseModel, Field

from evistream.application.types import JobStatus


class VideoStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class ArtifactType(StrEnum):
    SOURCE_VIDEO = "SOURCE_VIDEO"
    KEYFRAME = "KEYFRAME"
    CLIP = "CLIP"
    TRANSCRIPT = "TRANSCRIPT"
    OCR = "OCR"
    VISUAL_DESCRIPTION = "VISUAL_DESCRIPTION"


class Video(BaseModel):
    video_id: str
    original_name: str
    artifact_uri: str
    fingerprint: str | None
    duration_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    container: str
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    status: VideoStatus


class MediaJob(BaseModel):
    job_id: str
    video_id: str
    request_key: str
    status: JobStatus
    attempt: int = Field(ge=0)
    error_code: str | None = None


class SegmentBoundary(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
