"""Shared job handlers used by inline and future queued dispatchers."""

import asyncio
from typing import TYPE_CHECKING, Any

from evistream.application.types import JobHandlerError, JobRequest

if TYPE_CHECKING:
    from evistream.media.service import MediaApplicationService


class DemoJobHandler:
    async def handle(self, request: JobRequest) -> dict[str, str]:
        message = request.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("payload.message must be a non-empty string")
        normalized = message.strip()
        return {"message": normalized, "uppercase": normalized.upper()}


class MediaPreprocessJobHandler:
    def __init__(self, service: "MediaApplicationService") -> None:
        self._service = service

    async def handle(self, request: JobRequest) -> dict[str, Any]:
        if request.job_type != "MEDIA_PREPROCESS":
            raise JobHandlerError("JOB_INVALID", "unexpected media job type")
        stored = self._service.build_job_request(request.job_id)
        if (
            stored.job_type != request.job_type
            or stored.request_key != request.request_key
            or stored.correlation_id != request.correlation_id
            or stored.payload.get("video_id") != request.payload.get("video_id")
        ):
            raise JobHandlerError("JOB_INVALID", "job request does not match persisted state")
        job = await asyncio.to_thread(self._service.process_job, request.job_id)
        return {
            "job_id": job.job_id,
            "video_id": job.video_id,
            "status": job.status,
            "attempt": job.attempt,
        }
