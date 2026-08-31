"""FFmpeg scene-boundary and fixed-window segmentation."""

import re
import subprocess
from itertools import pairwise
from pathlib import Path

from evistream.media.types import SegmentBoundary

PTS_PATTERN = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


class FFmpegSceneSegmenter:
    def __init__(self, ffmpeg_binary: str = "ffmpeg", threshold: float = 0.3) -> None:
        self.ffmpeg_binary = ffmpeg_binary
        self.threshold = threshold

    def segment(
        self, path: Path, duration_ms: int, timeout_seconds: float
    ) -> list[SegmentBoundary]:
        command = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            f"select='gt(scene,{self.threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("FFmpeg scene detection failed")
        cuts = sorted(
            {
                round(float(match.group(1)) * 1000)
                for match in PTS_PATTERN.finditer(completed.stderr)
                if 0 < round(float(match.group(1)) * 1000) < duration_ms
            }
        )
        points = [0, *cuts, duration_ms]
        return [
            SegmentBoundary(start_ms=start, end_ms=end)
            for start, end in pairwise(points)
            if end > start
        ]


class FixedWindowSegmenter:
    def __init__(self, window_ms: int) -> None:
        self.window_ms = window_ms

    def segment(self, duration_ms: int) -> list[SegmentBoundary]:
        return [
            SegmentBoundary(start_ms=start, end_ms=min(start + self.window_ms, duration_ms))
            for start in range(0, duration_ms, self.window_ms)
        ]


def extract_keyframe(
    source: Path,
    target: Path,
    at_ms: int,
    *,
    ffmpeg_binary: str,
    timeout_seconds: float,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{at_ms / 1000:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-y",
            str(target),
        ],
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("FFmpeg keyframe extraction failed")
