import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from evistream.media.probe import MediaProbeError, MediaProbeErrorCode, probe_video


def test_probe_normalizes_ffprobe_json(tmp_path: Path) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"placeholder")
    payload = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "30.004", "format_name": "mov,mp4"},
    }
    completed = subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )

    with patch("evistream.media.probe.subprocess.run", return_value=completed):
        result = probe_video(media)

    assert result.duration_ms == 30004
    assert result.width == 640
    assert result.height == 360
    assert result.has_audio is True
    assert result.audio_codec == "aac"


def test_probe_rejects_missing_input(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError) as caught:
        probe_video(tmp_path / "missing.mp4")

    assert caught.value.code is MediaProbeErrorCode.INPUT_INVALID


def test_probe_maps_process_timeout(tmp_path: Path) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"placeholder")
    with (
        patch(
            "evistream.media.probe.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ffprobe", 1),
        ),
        pytest.raises(MediaProbeError) as caught,
    ):
        probe_video(media, timeout_seconds=1)

    assert caught.value.code is MediaProbeErrorCode.MEDIA_DECODE_FAILED
