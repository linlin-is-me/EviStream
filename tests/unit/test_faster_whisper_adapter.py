from types import SimpleNamespace
from unittest.mock import Mock, patch

from evistream.media.asr import ASRRequest, IsolatedFasterWhisperASR
from evistream.media.asr.faster_whisper import _convert_segments


def test_faster_whisper_segments_are_normalized_to_milliseconds() -> None:
    raw = [SimpleNamespace(start=0.25, end=1.75, text=" stage zero ")]

    segments = _convert_segments(raw)

    assert segments[0].start_ms == 250
    assert segments[0].end_ms == 1750
    assert segments[0].text == "stage zero"


def test_isolated_faster_whisper_returns_worker_schema(tmp_path) -> None:
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"media")
    payload = '{"segments":[],"language":"en","model":"tiny.en","duration_ms":0}'
    with (
        patch("evistream.media.asr.isolated.importlib.util.find_spec", return_value=Mock()),
        patch(
            "evistream.media.asr.isolated.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ) as run,
    ):
        response = IsolatedFasterWhisperASR(timeout_seconds=45).transcribe(
            ASRRequest(media_path=media)
        )

    assert response.model == "tiny.en"
    assert run.call_args.kwargs["timeout"] == 45
    assert run.call_args.kwargs["check"] is False
