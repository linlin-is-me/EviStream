"""Process-isolated faster-whisper adapter for mixed native media runtimes."""

import importlib.util
import subprocess
import sys

from evistream.media.asr.types import ASRRequest, ASRResponse


class IsolatedFasterWhisperASR:
    def __init__(
        self,
        model_name: str = "tiny.en",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        timeout_seconds: float = 600,
    ) -> None:
        if importlib.util.find_spec("faster_whisper") is None:
            raise RuntimeError("install EviStream with the 'asr' extra")
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._timeout_seconds = timeout_seconds

    def transcribe(self, request: ASRRequest) -> ASRResponse:
        if not request.media_path.is_file():
            raise FileNotFoundError(request.media_path)
        arguments = [
            sys.executable,
            "-m",
            "evistream.media.asr.worker",
            str(request.media_path),
            "--model",
            self._model_name,
            "--device",
            self._device,
            "--compute-type",
            self._compute_type,
        ]
        if request.language:
            arguments.extend(["--language", request.language])
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1:] or ["unknown error"]
            raise RuntimeError(f"faster-whisper worker failed: {detail[0]}")
        return ASRResponse.model_validate_json(completed.stdout)
