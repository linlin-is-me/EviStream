"""FastAPI entrypoint for the Stage 0 service."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from evistream import __version__
from evistream.config import Settings, get_settings


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    mode: str


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    application = FastAPI(title="EviStream API", version=__version__)

    if runtime_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=runtime_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    @application.get("/api/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="evistream-api",
            version=__version__,
            mode=runtime_settings.environment,
        )

    return application


app = create_app()

