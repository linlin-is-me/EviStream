"""Construct the media application and dispatcher from shared settings."""

from dataclasses import dataclass

from evistream.application import HandlerRegistry, InlineExecutor, MediaPreprocessJobHandler
from evistream.config import Settings
from evistream.media.asr import IsolatedFasterWhisperASR, MockASR
from evistream.media.extractors import (
    GatewayVisualDescription,
    MockOCR,
    MockVisualDescription,
    OCRAdapter,
    PaddleOCRAdapter,
    VisualDescriptionAdapter,
)
from evistream.media.service import MediaApplicationService
from evistream.models import ModelRole, build_model_gateway
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database


class MediaAdapterUnavailable(RuntimeError):
    code = "MEDIA_ADAPTER_UNAVAILABLE"


@dataclass(frozen=True)
class MediaRuntime:
    service: MediaApplicationService
    dispatcher: InlineExecutor


def build_media_runtime(settings: Settings) -> MediaRuntime:
    try:
        asr = (
            MockASR()
            if settings.asr_backend == "mock"
            else IsolatedFasterWhisperASR(
                settings.asr_model,
                device=settings.asr_device,
                compute_type=settings.asr_compute_type,
                timeout_seconds=settings.process_timeout_seconds,
            )
        )
        ocr: OCRAdapter = (
            MockOCR()
            if settings.ocr_backend == "mock"
            else PaddleOCRAdapter(settings.ocr_language)
        )
        if settings.vision_backend == "mock":
            vision: VisualDescriptionAdapter = MockVisualDescription()
        else:
            vision = GatewayVisualDescription(
                build_model_gateway(
                    settings.model_config_dir,
                    settings.model_profile,
                    ModelRole.TRIAGE,
                    environment=settings.model_environment(),
                ),
                timeout_seconds=settings.process_timeout_seconds,
            )
    except Exception as error:
        raise MediaAdapterUnavailable(str(error)) from error

    service = MediaApplicationService(
        Database(settings.database_url),
        LocalArtifactStore(settings.artifact_root),
        settings,
        asr,
        ocr,
        vision,
    )
    registry = HandlerRegistry()
    registry.register("MEDIA_PREPROCESS", MediaPreprocessJobHandler(service))
    return MediaRuntime(service=service, dispatcher=InlineExecutor(registry))
