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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

