from pathlib import Path
from subprocess import CompletedProcess

import pytest

from evistream.media.segmenter import (
    FFmpegSceneSegmenter,
    FixedWindowSegmenter,
    extract_keyframe,
)


def test_fixed_window_segmenter_handles_remainder() -> None:
    result = FixedWindowSegmenter(10_000).segment(25_000)
    assert [(item.start_ms, item.end_ms) for item in result] == [
        (0, 10_000),
        (10_000, 20_000),
        (20_000, 25_000),
    ]


def test_scene_segmenter_normalizes_unique_boundaries(monkeypatch) -> None:
    stderr = "pts_time:1.500\npts_time:1.500\npts_time:7.250\npts_time:12.0"
    monkeypatch.setattr(
        "evistream.media.segmenter.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0, stderr=stderr),
    )
    result = FFmpegSceneSegmenter().segment(Path("video.mp4"), 10_000, 30)
    assert [(item.start_ms, item.end_ms) for item in result] == [
        (0, 1500),
        (1500, 7250),
        (7250, 10_000),
    ]


def test_scene_segmenter_reports_ffmpeg_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "evistream.media.segmenter.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=1, stderr="failed"),
    )
    with pytest.raises(RuntimeError, match="scene detection"):
        FFmpegSceneSegmenter().segment(Path("video.mp4"), 1000, 30)


def test_extract_keyframe_builds_target_and_checks_exit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "evistream.media.segmenter.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=0),
    )
    target = tmp_path / "nested" / "frame.jpg"
    extract_keyframe(
        Path("video.mp4"),
        target,
        500,
        ffmpeg_binary="ffmpeg",
        timeout_seconds=5,
    )
    assert target.parent.is_dir()


def test_extract_keyframe_reports_ffmpeg_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "evistream.media.segmenter.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args=[], returncode=1),
    )
    with pytest.raises(RuntimeError, match="keyframe"):
        extract_keyframe(
            Path("video.mp4"),
            tmp_path / "frame.jpg",
            500,
            ffmpeg_binary="ffmpeg",
            timeout_seconds=5,
        )
