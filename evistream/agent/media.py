"""Bounded frame extraction for inspection model calls."""

import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from evistream.config import Settings
from evistream.models.types import MediaReference
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database
from evistream.storage.models import CaseRecord, VideoRecord


class InspectionFrameSampler:
    def __init__(
        self,
        database: Database,
        artifacts: LocalArtifactStore,
        settings: Settings,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.settings = settings

    def sample(self, case_id: str, items: list[dict[str, Any]]) -> list[MediaReference]:
        if not items:
            return []
        with self.database.session() as session:
            case = session.get(CaseRecord, case_id)
            video = session.get(VideoRecord, case.video_id) if case is not None else None
            if video is None:
                return []
            source = self.artifacts.resolve(video.artifact_uri)
        points = _sample_points(items, self.settings.agent_inspection_frame_count)
        frames: list[MediaReference] = []
        with tempfile.TemporaryDirectory(prefix="evistream-agent-frames-") as directory:
            root = Path(directory)
            for index, point_ms in enumerate(points):
                target = root / f"frame-{index}.jpg"
                completed = subprocess.run(
                    [
                        self.settings.ffmpeg_binary,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{point_ms / 1000:.3f}",
                        "-i",
                        str(source),
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        "-y",
                        str(target),
                    ],
                    capture_output=True,
                    check=False,
                    timeout=self.settings.process_timeout_seconds,
                )
                if completed.returncode == 0 and target.is_file():
                    encoded = base64.b64encode(target.read_bytes()).decode("ascii")
                    frames.append(
                        MediaReference(kind="image", uri=f"data:image/jpeg;base64,{encoded}")
                    )
        return frames


def _sample_points(items: list[dict[str, Any]], limit: int) -> list[int]:
    points: list[int] = []
    for item in items:
        start = int(item["start_ms"])
        end = int(item["end_ms"])
        point = start + max(0, end - start) // 2
        if point not in points:
            points.append(point)
        if len(points) >= limit:
            break
    return points
