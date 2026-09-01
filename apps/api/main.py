"""FastAPI entrypoint for the Stage 0 service."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from evistream import __version__
from evistream.config import Settings, get_settings
from evistream.media.runtime import MediaAdapterUnavailable, MediaRuntime, build_media_runtime
from evistream.media.types import MediaJob, SegmentBoundary, Video


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    mode: str


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    application = FastAPI(title="EviStream API", version=__version__)
    media_runtime: MediaRuntime | None = None

    def get_media_runtime() -> MediaRuntime:
        nonlocal media_runtime
        if media_runtime is None:
            media_runtime = build_media_runtime(runtime_settings)
        return media_runtime

    if runtime_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=runtime_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
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

    @application.post("/api/v1/videos", response_model=Video, status_code=202)
    async def upload_video(
        file: UploadFile,
        background_tasks: BackgroundTasks,
    ) -> Video:
        suffix = Path(file.filename or "upload.bin").suffix
        try:
            with NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                total_bytes = 0
                while chunk := await file.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > runtime_settings.upload_max_bytes:
                        raise ValueError("uploaded file exceeds configured limit")
                    temporary.write(chunk)
            runtime = get_media_runtime()
            video, job = runtime.service.register_file(temporary_path, file.filename)
            request = runtime.service.build_job_request(job.job_id)
            background_tasks.add_task(runtime.dispatcher.dispatch, request)
            return video
        except (MediaAdapterUnavailable, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        finally:
            if "temporary_path" in locals():
                temporary_path.unlink(missing_ok=True)

    @application.get("/api/v1/videos/{video_id}", response_model=Video)
    async def get_video(video_id: str) -> Video:
        try:
            return get_media_runtime().service.get_video(video_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="video not found") from error

    @application.get("/api/v1/jobs/{job_id}", response_model=MediaJob)
    async def get_job(job_id: str) -> MediaJob:
        try:
            return get_media_runtime().service.get_job(job_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @application.post(
        "/api/v1/videos/{video_id}/simulate-stream",
        response_model=list[SegmentBoundary],
    )
    async def simulate_stream(video_id: str) -> list[SegmentBoundary]:
        try:
            return get_media_runtime().service.simulate_stream(video_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="video not found") from error

    return application


app = create_app()
