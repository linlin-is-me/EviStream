import builtins

import pytest

from evistream.media.extractors import (
    GatewayVisualDescription,
    MockOCR,
    MockVisualDescription,
    PaddleOCRAdapter,
)
from evistream.models.mock import MockGateway


def test_mock_extractors_return_stable_results(tmp_path) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"frame")

    assert MockOCR().extract(image) == []
    description = MockVisualDescription().describe(image)
    assert description.model == "mock-vision"
    assert description.confidence == 1


def test_mock_extractors_require_existing_file(tmp_path) -> None:
    missing = tmp_path / "missing.jpg"
    with pytest.raises(FileNotFoundError):
        MockOCR().extract(missing)
    with pytest.raises(FileNotFoundError):
        MockVisualDescription().describe(missing)


def test_paddle_adapter_reports_missing_optional_dependency(monkeypatch) -> None:
    original_import = builtins.__import__

    def reject_paddle(name: str, *args: object, **kwargs: object) -> object:
        if name == "paddleocr":
            raise ImportError
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_paddle)
    with pytest.raises(RuntimeError, match="not installed"):
        PaddleOCRAdapter()


def test_gateway_visual_description_uses_provider_neutral_contract(tmp_path) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"frame")
    gateway = MockGateway(
        payload={
            "description": "A synthetic frame.",
            "objects": ["frame"],
            "actions": [],
            "uncertainty": None,
        }
    )
    result = GatewayVisualDescription(gateway).describe(image)
    assert result.text == "A synthetic frame."
    assert result.model == "mock-stage0"
