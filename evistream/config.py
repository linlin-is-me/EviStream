"""Environment-backed application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    process_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    asr_backend: Literal["mock", "faster-whisper"] = "mock"
    asr_model: str = "tiny.en"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    database_url: str = "postgresql+psycopg://evistream:evistream@localhost:54329/evistream"
    artifact_root: Path = Path("data/artifacts")
    upload_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    video_max_duration_seconds: int = Field(default=3600, gt=0)
    scene_threshold: float = Field(default=0.3, gt=0, lt=1)
    simulated_stream_segment_seconds: int = Field(default=10, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
