import hashlib
import os
import shutil
from pathlib import Path

import pytest

from evistream.media.probe import probe_video

FIXTURE = Path("tests/fixtures/media/stage0_sample.mp4")


@pytest.mark.integration
def test_committed_stage0_fixture_is_intact_and_probeable() -> None:
    expected_hash = FIXTURE.with_suffix(".mp4.sha256").read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == expected_hash

    ffprobe = os.getenv("EVISTREAM_FFPROBE_BINARY") or shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not installed")

    result = probe_video(FIXTURE, ffprobe_binary=ffprobe)
    assert result.duration_ms == 30_000
    assert (result.width, result.height) == (640, 360)
    assert result.video_codec == "h264"
    assert result.has_audio is True
    assert result.audio_codec == "aac"
