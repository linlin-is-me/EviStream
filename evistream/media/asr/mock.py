"""Deterministic offline ASR implementation."""

from evistream.media.asr.types import ASRRequest, ASRResponse, ASRSegment


class MockASR:
    def transcribe(self, request: ASRRequest) -> ASRResponse:
        if not request.media_path.is_file():
            raise FileNotFoundError(request.media_path)
        return ASRResponse(
            segments=[
                ASRSegment(
                    start_ms=0,
                    end_ms=3000,
                    text="EviStream stage zero media verification.",
                    confidence=1.0,
                )
            ],
            language=request.language or "en",
            model="mock-asr",
            duration_ms=3000,
        )
