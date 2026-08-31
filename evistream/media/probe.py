"""Safe ffprobe wrapper with normalized millisecond metadata."""

import json
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class MediaProbeErrorCode(StrEnum):
    INPUT_INVALID = "INPUT_INVALID"
    MEDIA_DECODE_FAILED = "MEDIA_DECODE_FAILED"


class MediaProbeError(RuntimeError):
    def __init__(self, code: MediaProbeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class MediaProbeResult(BaseModel):
    path: str
    duration_ms: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    container: str
    video_codec: str
    has_audio: bool
    audio_codec: str | None = None


def probe_video(
    path: Path,
    *,
    ffprobe_binary: str = "ffprobe",
    timeout_seconds: float = 30,
) -> MediaProbeResult:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise MediaProbeError(MediaProbeErrorCode.INPUT_INVALID, f"media file not found: {path}")

    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        str(resolved),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        raise MediaProbeError(
            MediaProbeErrorCode.MEDIA_DECODE_FAILED,
            f"ffprobe executable not found: {ffprobe_binary}",
        ) from error
    except subprocess.TimeoutExpired as error:
        raise MediaProbeError(
            MediaProbeErrorCode.MEDIA_DECODE_FAILED,
            "ffprobe timed out",
        ) from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown ffprobe error"
        raise MediaProbeError(
            MediaProbeErrorCode.MEDIA_DECODE_FAILED,
            f"ffprobe failed: {detail}",
        )

    try:
        document = json.loads(completed.stdout)
        return _normalize_probe(document, resolved)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ValidationError) as error:
        raise MediaProbeError(
            MediaProbeErrorCode.MEDIA_DECODE_FAILED,
            "ffprobe returned incomplete metadata",
        ) from error


def _normalize_probe(document: dict[str, Any], path: Path) -> MediaProbeResult:
    streams = document.get("streams")
    if not isinstance(streams, list):
        raise ValueError("streams missing")
    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video is None:
        raise ValueError("video stream missing")
    audio = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )
    format_info = document.get("format")
    if not isinstance(format_info, dict):
        raise ValueError("format missing")

    return MediaProbeResult(
        path=str(path),
        duration_ms=round(float(format_info["duration"]) * 1000),
        width=int(video["width"]),
        height=int(video["height"]),
        container=str(format_info.get("format_name", "unknown")),
        video_codec=str(video.get("codec_name", "unknown")),
        has_audio=audio is not None,
        audio_codec=str(audio.get("codec_name")) if audio else None,
    )
