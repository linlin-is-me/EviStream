from unittest.mock import patch

import pytest

from evistream.config import Settings
from evistream.media.asr import MockASR
from evistream.media.extractors import (
    GatewayVisualDescription,
    MockOCR,
    MockVisualDescription,
)
from evistream.media.runtime import MediaAdapterUnavailable, build_media_runtime
from evistream.models import MockGateway


def test_media_runtime_defaults_to_deterministic_adapters(tmp_path) -> None:
    runtime = build_media_runtime(
        Settings(artifact_root=tmp_path / "artifacts", _env_file=None)
    )

    assert isinstance(runtime.service.asr, MockASR)
    assert isinstance(runtime.service.ocr, MockOCR)
    assert isinstance(runtime.service.vision, MockVisualDescription)


def test_media_runtime_selects_configured_real_adapters(tmp_path) -> None:
    settings = Settings(
        artifact_root=tmp_path / "artifacts",
        asr_backend="faster-whisper",
        ocr_backend="paddleocr",
        vision_backend="gateway",
        model_profile="dashscope-test",
        _env_file=None,
    )
    with (
        patch(
            "evistream.media.runtime.IsolatedFasterWhisperASR", return_value=MockASR()
        ) as asr,
        patch("evistream.media.runtime.PaddleOCRAdapter", return_value=MockOCR()) as ocr,
        patch(
            "evistream.media.runtime.build_model_gateway",
            return_value=MockGateway(),
        ) as gateway,
    ):
        runtime = build_media_runtime(settings)

    asr.assert_called_once_with(
        "tiny.en", device="cpu", compute_type="int8", timeout_seconds=30.0
    )
    ocr.assert_called_once_with("en")
    gateway.assert_called_once()
    assert isinstance(runtime.service.vision, GatewayVisualDescription)


def test_media_runtime_does_not_fall_back_when_adapter_is_unavailable(tmp_path) -> None:
    settings = Settings(
        artifact_root=tmp_path / "artifacts",
        ocr_backend="paddleocr",
        _env_file=None,
    )
    with (
        patch(
            "evistream.media.runtime.PaddleOCRAdapter",
            side_effect=RuntimeError("PaddleOCR is not installed"),
        ),
        pytest.raises(MediaAdapterUnavailable, match="PaddleOCR is not installed"),
    ):
        build_media_runtime(settings)
