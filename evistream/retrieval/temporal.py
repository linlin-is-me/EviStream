"""Deterministic time-window helpers."""

from evistream.media.types import SegmentBoundary


def expand_window(start_ms: int, end_ms: int, duration_ms: int, context_ms: int) -> SegmentBoundary:
    if start_ms < 0 or end_ms <= start_ms or duration_ms <= 0:
        raise ValueError("invalid time range")
    return SegmentBoundary(
        start_ms=max(0, start_ms - context_ms),
        end_ms=min(duration_ms, end_ms + context_ms),
    )


def merge_ranges(ranges: list[SegmentBoundary]) -> list[SegmentBoundary]:
    ordered = sorted(ranges, key=lambda item: (item.start_ms, item.end_ms))
    merged: list[SegmentBoundary] = []
    for item in ordered:
        if not merged or item.start_ms > merged[-1].end_ms:
            merged.append(item)
        else:
            merged[-1] = SegmentBoundary(
                start_ms=merged[-1].start_ms,
                end_ms=max(merged[-1].end_ms, item.end_ms),
            )
    return merged
