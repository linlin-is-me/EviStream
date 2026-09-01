"""Internal command used to isolate faster-whisper native libraries."""

import argparse
from pathlib import Path

from evistream.media.asr.faster_whisper import FasterWhisperASR
from evistream.media.asr.types import ASRRequest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media_path", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--compute-type", required=True)
    parser.add_argument("--language")
    arguments = parser.parse_args()
    adapter = FasterWhisperASR(
        arguments.model,
        device=arguments.device,
        compute_type=arguments.compute_type,
    )
    response = adapter.transcribe(
        ASRRequest(media_path=arguments.media_path, language=arguments.language)
    )
    print(response.model_dump_json())


if __name__ == "__main__":
    main()
