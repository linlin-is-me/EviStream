"""Optional faster-whisper adapter loaded only when selected."""

from collections.abc import Iterable
from typing import Any

from evistream.media.asr.types import ASRRequest, ASRResponse, ASRSegment


class FasterWhisperASR:
    def __init__(
        self,
        model_name: str = "tiny.en",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError("install EviStream with the 'asr' extra") from error
        self._model_name = model_name
        self._model: Any = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )

    def transcribe(self, request: ASRRequest) -> ASRResponse:
        if not request.media_path.is_file():
            raise FileNotFoundError(request.media_path)
        raw_segments, info = self._model.transcribe(
            str(request.media_path),
            language=request.language,
            vad_filter=False,
        )
        segments = _convert_segments(raw_segments)
        duration_ms = max((segment.end_ms for segment in segments), default=0)
        return ASRResponse(
            segments=segments,
            language=getattr(info, "language", request.language),
            model=self._model_name,
            duration_ms=duration_ms,
        )


def _convert_segments(raw_segments: Iterable[Any]) -> list[ASRSegment]:
    converted: list[ASRSegment] = []
    for segment in raw_segments:
        start_ms = max(0, round(float(segment.start) * 1000))
        end_ms = max(start_ms + 1, round(float(segment.end) * 1000))
        converted.append(
            ASRSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                text=str(segment.text).strip(),
            )
        )
    return converted
