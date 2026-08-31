"""OCR and visual-description adapter contracts with deterministic CI implementations."""

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from evistream.models.types import (
    MediaReference,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelRole,
)


class TextObservation(BaseModel):
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    model: str


class VisualDescriptionPayload(BaseModel):
    description: str = Field(min_length=1)
    objects: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    uncertainty: str | None = None


class OCRAdapter(Protocol):
    def extract(self, image: Path) -> list[TextObservation]: ...


class VisualDescriptionAdapter(Protocol):
    def describe(self, image: Path) -> TextObservation: ...


class MockOCR:
    def extract(self, image: Path) -> list[TextObservation]:
        if not image.is_file():
            raise FileNotFoundError(image)
        return []


class MockVisualDescription:
    def describe(self, image: Path) -> TextObservation:
        if not image.is_file():
            raise FileNotFoundError(image)
        return TextObservation(
            text="Synthetic Stage 1 keyframe.", confidence=1.0, model="mock-vision"
        )


class GatewayVisualDescription:
    def __init__(self, gateway: ModelGateway, timeout_seconds: float = 30) -> None:
        self.gateway = gateway
        self.timeout_seconds = timeout_seconds

    def describe(self, image: Path) -> TextObservation:
        if not image.is_file():
            raise FileNotFoundError(image)
        mime_type = mimetypes.guess_type(image.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        request = ModelRequest(
            role=ModelRole.TRIAGE,
            messages=[
                ModelMessage(
                    role="user",
                    content=(
                        "Describe the visible objects, actions and uncertainty. "
                        "Return structured JSON."
                    ),
                )
            ],
            media=[
                MediaReference(kind="image", uri=f"data:{mime_type};base64,{encoded}")
            ],
            response_schema=VisualDescriptionPayload,
            timeout_seconds=self.timeout_seconds,
            trace_id="stage1-visual-description",
        )
        response = asyncio.run(self.gateway.generate(request))
        payload = VisualDescriptionPayload.model_validate(response.data)
        return TextObservation(text=payload.description, model=response.actual_model)


class PaddleOCRAdapter:
    def __init__(self, language: str = "en") -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as error:
            raise RuntimeError("PaddleOCR is not installed") from error
        self._engine = PaddleOCR(use_doc_orientation_classify=False, lang=language)

    def extract(self, image: Path) -> list[TextObservation]:
        if not image.is_file():
            raise FileNotFoundError(image)
        result = self._engine.predict(str(image))
        observations: list[TextObservation] = []
        for page in result:
            payload = page.json.get("res", {})
            texts = payload.get("rec_texts", [])
            scores = payload.get("rec_scores", [])
            observations.extend(
                TextObservation(text=str(text), confidence=float(score), model="paddleocr")
                for text, score in zip(texts, scores, strict=False)
                if str(text).strip()
            )
        return observations
