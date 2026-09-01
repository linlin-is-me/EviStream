"""Shared job handlers used by inline and future queued dispatchers."""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from evistream.application.types import JobHandlerError, JobRequest

if TYPE_CHECKING:
    from evistream.agent.engine import InvestigationEngine
    from evistream.agent.service import AgentInvestigationService
    from evistream.agent.types import InvestigationState
    from evistream.media.service import MediaApplicationService
    from evistream.replay.service import ReplayApplicationService
    from evistream.retrieval.indexing import EmbeddingIndexService
    from evistream.triage.service import VideoTriageService


class DemoJobHandler:
    async def handle(self, request: JobRequest) -> dict[str, str]:
        message = request.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("payload.message must be a non-empty string")
        normalized = message.strip()
        return {"message": normalized, "uppercase": normalized.upper()}


class MediaPreprocessJobHandler:
    def __init__(
        self,
        service: "MediaApplicationService",
        indexer: "EmbeddingIndexService | None" = None,
        triage: "VideoTriageService | None" = None,
    ) -> None:
        self._service = service
        self._indexer = indexer
        self._triage = triage

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
        video_id = str(request.payload.get("video_id", ""))
        profile = str(request.payload.get("model_profile", "mock"))
        try:
            job = await asyncio.to_thread(
                self._service.process_job, request.job_id, finalize=False
            )
            if self._indexer is not None:
                summary = await self._indexer.index_video(video_id)
                if summary.status != "success":
                    retryable = any(item.retryable for item in summary.failures)
                    raise JobHandlerError(
                        summary.error_code or "EMBEDDING_INDEX_FAILED",
                        "video embedding index did not complete",
                        retryable=retryable,
                    )
            if self._triage is not None:
                await self._triage.triage(request.job_id, video_id, profile)
            job = await asyncio.to_thread(self._service.complete_job, request.job_id)
        except JobHandlerError as error:
            await asyncio.to_thread(
                self._service.fail_job,
                request.job_id,
                error.code,
                str(error),
                retryable=error.retryable,
            )
            raise
        except Exception as error:
            from evistream.models.types import ModelError
            from evistream.triage.service import TriageError

            code = getattr(error, "code", "MEDIA_DECODE_FAILED")
            retryable = isinstance(error, (ModelError, TriageError)) and error.retryable
            await asyncio.to_thread(
                self._service.fail_job,
                request.job_id,
                str(code),
                str(error),
                retryable=retryable,
            )
            raise JobHandlerError(str(code), str(error), retryable=retryable) from error
        return {
            "job_id": job.job_id,
            "video_id": job.video_id,
            "status": job.status,
            "attempt": job.attempt,
        }


class AgentInvestigationJobHandler:
    def __init__(
        self,
        service: "AgentInvestigationService",
        engine_factory: Callable[["InvestigationState"], "InvestigationEngine"],
    ) -> None:
        self._service = service
        self._engine_factory = engine_factory

    async def handle(self, request: JobRequest) -> dict[str, Any]:
        from evistream.agent.errors import AgentRuntimeError
        from evistream.agent.types import InvestigationResult
        from evistream.models.types import ModelError

        if request.job_type != "AGENT_INVESTIGATION":
            raise JobHandlerError("JOB_INVALID", "unexpected Agent job type")
        run_id = request.payload.get("run_id")
        if not isinstance(run_id, str):
            raise JobHandlerError("AGENT_CHECKPOINT_INVALID", "request has no run ID")
        claimed = False
        try:
            state = self._service.claim(request)
            if isinstance(state, InvestigationResult):
                return state.model_dump(mode="json")
            claimed = True
            result = await self._engine_factory(state).run(state, request.correlation_id)
            return result.model_dump(mode="json")
        except ModelError as error:
            if claimed and error.retryable:
                self._service.defer_retry(run_id, str(error.code))
            raise JobHandlerError(
                str(error.code), str(error), retryable=error.retryable
            ) from error
        except AgentRuntimeError as error:
            if claimed and error.code != "AGENT_STATE_CONFLICT":
                self._service.fail(run_id, error.code)
            raise JobHandlerError(error.code, str(error)) from error


class PolicyReplayJobHandler:
    def __init__(self, service: "ReplayApplicationService") -> None:
        self._service = service

    async def handle(self, request: JobRequest) -> dict[str, Any]:
        from evistream.governance.errors import GovernanceError

        if request.job_type != "POLICY_REPLAY":
            raise JobHandlerError("JOB_INVALID", "unexpected replay job type")
        replay_job_id = request.payload.get("replay_job_id")
        if not isinstance(replay_job_id, str):
            raise JobHandlerError("REPLAY_NOT_RESUMABLE", "request has no replay job ID")
        claimed = False
        try:
            self._service.claim(request)
            claimed = True
            return (await self._service.execute(replay_job_id)).model_dump(mode="json")
        except GovernanceError as error:
            if claimed:
                self._service.fail(replay_job_id, error.code)
            raise JobHandlerError(error.code, str(error)) from error
        except Exception:
            if claimed:
                self._service.fail(replay_job_id, "REPLAY_NOT_RESUMABLE")
            raise
