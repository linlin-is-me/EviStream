from pathlib import Path

from evistream.media.asr import ASRRequest, ASRResponse, MockASR


def test_mock_asr_returns_common_contract(tmp_path: Path) -> None:
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"synthetic")

    result = MockASR().transcribe(ASRRequest(media_path=media, language="en"))

    assert isinstance(result, ASRResponse)
    assert result.model == "mock-asr"
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms > result.segments[0].start_ms
