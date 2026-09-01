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
from evistream.models import ModelRole, build_model_gateway, resolve_embedding_gateway
from evistream.retrieval.indexing import EmbeddingIndexService
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database
from evistream.triage import VideoTriageService


class MediaAdapterUnavailable(RuntimeError):
    code = "MEDIA_ADAPTER_UNAVAILABLE"


@dataclass(frozen=True)
class MediaRuntime:
    service: MediaApplicationService
    dispatcher: InlineExecutor


def build_media_runtime(settings: Settings, profile_name: str | None = None) -> MediaRuntime:
    selected_profile = profile_name or settings.model_profile
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
            MockOCR() if settings.ocr_backend == "mock" else PaddleOCRAdapter(settings.ocr_language)
        )
        if settings.vision_backend == "mock":
            vision: VisualDescriptionAdapter = MockVisualDescription()
        else:
            vision = GatewayVisualDescription(
                build_model_gateway(
                    settings.model_config_dir,
                    selected_profile,
                    ModelRole.TRIAGE,
                    environment=settings.model_environment(),
                ),
                timeout_seconds=settings.process_timeout_seconds,
            )
    except Exception as error:
        raise MediaAdapterUnavailable(str(error)) from error

    database = Database(settings.database_url)
    service = MediaApplicationService(
        database,
        LocalArtifactStore(settings.artifact_root),
        settings,
        asr,
        ocr,
        vision,
    )
    embedding, embedding_profile = resolve_embedding_gateway(
        settings.model_config_dir,
        selected_profile,
        environment=settings.model_environment(),
    )
    indexer = EmbeddingIndexService(database, embedding, embedding_profile)
    registry = HandlerRegistry()
    registry.register(
        "MEDIA_PREPROCESS",
        MediaPreprocessJobHandler(
            service,
            indexer,
            VideoTriageService(database, settings),
        ),
    )
    return MediaRuntime(service=service, dispatcher=InlineExecutor(registry))
