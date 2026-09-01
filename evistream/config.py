"""Environment-backed application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by API, CLI, and job handlers."""

    model_config = SettingsConfigDict(
        env_prefix="EVISTREAM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    model_profile: str = "mock"
    model_config_dir: Path = Path("configs/models")
    model_base_url: str | None = None
    model_api_key: SecretStr | None = None
    agent_model: str | None = None
    triage_model: str | None = None
    verify_model: str | None = None
    judge_model: str | None = None
    embedding_model: str | None = None
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    process_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    asr_backend: Literal["mock", "faster-whisper"] = "mock"
    asr_model: str = "tiny.en"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    ocr_backend: Literal["mock", "paddleocr"] = "mock"
    ocr_language: str = "en"
    vision_backend: Literal["mock", "gateway"] = "mock"
    database_url: str = "postgresql+psycopg://evistream:evistream@localhost:54329/evistream"
    artifact_root: Path = Path("data/artifacts")
    upload_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    video_max_duration_seconds: int = Field(default=3600, gt=0)
    video_max_width: int = Field(default=3840, gt=0)
    video_max_height: int = Field(default=2160, gt=0)
    media_job_lease_seconds: int = Field(default=3600, gt=0)
    scene_threshold: float = Field(default=0.3, gt=0, lt=1)
    simulated_stream_segment_seconds: int = Field(default=10, gt=0)
    retrieval_rrf_k: int = Field(default=60, gt=0)
    retrieval_candidate_limit: int = Field(default=100, ge=20, le=1000)
    retrieval_context_ms: int = Field(default=10_000, ge=0)
    tool_clip_max_seconds: int = Field(default=30, gt=0, le=300)

    def model_environment(self) -> dict[str, str]:
        values = {
            "EVISTREAM_MODEL_BASE_URL": self.model_base_url,
            "EVISTREAM_MODEL_API_KEY": (
                self.model_api_key.get_secret_value() if self.model_api_key else None
            ),
            "EVISTREAM_AGENT_MODEL": self.agent_model,
            "EVISTREAM_TRIAGE_MODEL": self.triage_model,
            "EVISTREAM_VERIFY_MODEL": self.verify_model,
            "EVISTREAM_JUDGE_MODEL": self.judge_model,
            "EVISTREAM_EMBEDDING_MODEL": self.embedding_model,
        }
        return {key: value for key, value in values.items() if value is not None}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
