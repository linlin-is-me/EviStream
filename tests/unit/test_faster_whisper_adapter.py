from types import SimpleNamespace

from evistream.media.asr.faster_whisper import _convert_segments


def test_faster_whisper_segments_are_normalized_to_milliseconds() -> None:
    raw = [SimpleNamespace(start=0.25, end=1.75, text=" stage zero ")]

    segments = _convert_segments(raw)

    assert segments[0].start_ms == 250
    assert segments[0].end_ms == 1750
    assert segments[0].text == "stage zero"
