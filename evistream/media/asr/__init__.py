"""Speech recognition contracts and adapters."""

from evistream.media.asr.faster_whisper import FasterWhisperASR
from evistream.media.asr.mock import MockASR
from evistream.media.asr.types import ASRAdapter, ASRRequest, ASRResponse, ASRSegment

__all__ = ["ASRAdapter", "ASRRequest", "ASRResponse", "ASRSegment", "FasterWhisperASR", "MockASR"]

