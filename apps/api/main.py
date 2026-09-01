"""EviStream HTTP API composition root."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from evistream import __version__
from evistream.agent.errors import AgentRuntimeError
from evistream.agent.service import AgentInvestigationService
from evistream.config import Settings, get_settings
from evistream.domain import Verdict
from evistream.governance.errors import GovernanceError
from evistream.governance.review import HumanGovernanceService
from evistream.governance.runtime import build_governance_runtime
from evistream.governance.service import GovernanceApplicationService
from evistream.governance.timeline import CaseTimelineService
from evistream.jobs import ApplicationDispatcher, JobService, PersistedJobRepository
from evistream.jobs.service import JobServiceError
from evistream.media.runtime import MediaAdapterUnavailable, build_media_runtime
from evistream.media.types import SegmentBoundary
from evistream.models import (
    EmbeddingRequest,
    ModelMessage,
    ModelRequest,
    ModelRole,
    build_model_gateway,
    resolve_embedding_gateway,
)
from evistream.models.profiles import load_model_profile, resolve_model_profile
from evistream.models.types import ModelError
from evistream.observability import configure_json_logging
from evistream.policies.schema import PolicyError, load_policy_source
from evistream.policies.versioning import (
    PolicyVersionError,
    PolicyVersionService,
    case_from_record,
    requirement_from_record,
)
from evistream.replay.planner import ReplayPlanner
from evistream.replay.service import ReplayApplicationService
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database
from evistream.storage.models import (
    AgentRunRecord,
    AppealRecord,
    ArtifactRecord,
    CaseRecord,
    DecisionRecord,
    ProcessingJobRecord,
    RequirementRecord,
    RequirementResultRecord,
    SearchDocumentRecord,
    SegmentRecord,
    VideoRecord,
    VideoTriageCheckRecord,
)

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    mode: str


class PolicyWrite(BaseModel):
    source_yaml: str = Field(min_length=1)
    lifecycle: Literal["draft", "published"] = "published"


class InvestigationWrite(BaseModel):
    model_profile: str | None = None


class ReviewWrite(BaseModel):
    reviewer: str = Field(min_length=1, max_length=255)
    verdict: Verdict
    note: str = Field(default="human review", min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list)


class AppealWrite(BaseModel):
    submitter: str = Field(min_length=1, max_length=255)
    statement: str = Field(min_length=1, max_length=4000)


class AppealResolveWrite(ReviewWrite):
    note: str = Field(default="appeal resolution", min_length=1, max_length=4000)


class ReplayPreviewWrite(BaseModel):
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    model_change_policy: Literal["keep", "invalidate-visual"] = "keep"


class ReplayWrite(ReplayPreviewWrite):
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_profile: str | None = None


class ProfileHealthProbe(BaseModel):
    ok: bool


class ApiError(HTTPException):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    database = Database(runtime_settings.database_url)
    dispatcher = ApplicationDispatcher(runtime_settings)
    jobs = JobService(database)
    artifacts = LocalArtifactStore(runtime_settings.artifact_root)
    application = FastAPI(title="EviStream API", version=__version__)
    logger = configure_json_logging("api")

    if runtime_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=runtime_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    @application.middleware("http")
    async def correlation_middleware(request: Request, call_next: Any) -> Any:
        correlation_id = request.headers.get("X-Correlation-ID") or f"corr_{uuid4().hex}"
        token = correlation_id_var.set(correlation_id)
        started = perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            logger.info(
                "request_completed",
                extra={
                    "event": "http.request.completed",
                    "correlation_id": correlation_id,
                    "status": response.status_code,
                    "latency_ms": round((perf_counter() - started) * 1000),
                },
            )
            return response
        finally:
            correlation_id_var.reset(token)

    @application.exception_handler(ApiError)
    async def api_error(_: Request, error: ApiError) -> JSONResponse:
        return _error_response(error.code, str(error.detail), error.status_code)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return _error_response("INPUT_INVALID", "request validation failed", 422, error.errors())

    @application.get("/api/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="evistream-api",
            version=__version__,
            mode=runtime_settings.environment,
        )

    @application.get("/api/v1/ready")
    async def ready() -> dict[str, str]:
        try:
            expected_revision = ScriptDirectory.from_config(
                Config("alembic.ini")
            ).get_current_head()
            with database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                current_revision = MigrationContext.configure(
                    connection
                ).get_current_revision()
            if current_revision != expected_revision:
                raise RuntimeError(
                    f"database revision {current_revision!r} does not match {expected_revision!r}"
                )
            if runtime_settings.task_dispatcher == "rq":
                from redis import Redis

                if not Redis.from_url(runtime_settings.redis_url).ping():
                    raise RuntimeError("Redis ping failed")
            return {"status": "ready", "database": "ok", "redis": "ok"}
        except Exception as error:
            raise ApiError("MIGRATION_REQUIRED", str(error), 503) from error

    @application.post("/api/v1/videos", status_code=202)
    async def upload_video(
        file: UploadFile = File(...), model_profile: str = Form(default="mock")
    ) -> dict[str, Any]:
        _validate_profile(runtime_settings, model_profile)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                suffix=Path(file.filename or "upload.bin").suffix, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                total_bytes = 0
                while chunk := await file.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > runtime_settings.upload_max_bytes:
                        raise ApiError(
                            "INPUT_INVALID", "uploaded file exceeds configured limit", 422
                        )
                    temporary.write(chunk)
            runtime = build_media_runtime(runtime_settings, model_profile)
            video, job = runtime.service.register_file(
                temporary_path, file.filename, model_profile=model_profile
            )
            submission = await dispatcher.submit(runtime.service.build_job_request(job.job_id))
            return {"video": video.model_dump(mode="json"), **submission.model_dump(mode="json")}
        except ApiError:
            raise
        except (MediaAdapterUnavailable, ModelError) as error:
            raise ApiError("MODEL_PROFILE_UNAVAILABLE", str(error), 503) from error
        except (ValueError, RuntimeError) as error:
            raise ApiError(getattr(error, "code", "INPUT_INVALID"), str(error), 422) from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @application.get("/api/v1/videos")
    async def list_videos(
        status: str | None = None,
        model_profile: str | None = None,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        with database.session() as session:
            statement = select(VideoRecord)
            if status:
                statement = statement.where(VideoRecord.status == status)
            if model_profile:
                statement = statement.where(VideoRecord.model_profile == model_profile)
            rows = list(
                session.scalars(
                    statement.order_by(VideoRecord.created_at.desc(), VideoRecord.id)
                    .offset(cursor)
                    .limit(limit + 1)
                ).all()
            )
            items = [_video_payload(item) for item in rows[:limit]]
        return {"items": items, "next_cursor": cursor + limit if len(rows) > limit else None}

    @application.get("/api/v1/videos/{video_id}")
    async def get_video(video_id: str) -> dict[str, Any]:
        with database.session() as session:
            video = session.get(VideoRecord, video_id)
            if video is None:
                raise ApiError("VIDEO_NOT_FOUND", "video not found", 404)
            job = session.scalar(
                select(ProcessingJobRecord).where(
                    ProcessingJobRecord.subject_id == video_id,
                    ProcessingJobRecord.type == "MEDIA_PREPROCESS",
                )
            )
            segments = session.scalars(
                select(SegmentRecord)
                .where(SegmentRecord.video_id == video_id)
                .order_by(SegmentRecord.sequence)
            ).all()
            artifact_rows = session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.video_id == video_id)
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
            ).all()
            checks = session.scalars(
                select(VideoTriageCheckRecord)
                .where(VideoTriageCheckRecord.video_id == video_id)
                .order_by(VideoTriageCheckRecord.policy_id)
            ).all()
            cases = session.scalars(
                select(CaseRecord)
                .where(CaseRecord.video_id == video_id)
                .order_by(CaseRecord.created_at, CaseRecord.id)
            ).all()
            count = int(
                session.scalar(
                    select(func.count())
                    .select_from(SearchDocumentRecord)
                    .where(SearchDocumentRecord.video_id == video_id)
                )
                or 0
            )
            return {
                "video": _video_payload(video),
                "job": jobs.get(job.id).model_dump(mode="json") if job else None,
                "segments": [
                    {"segment_id": row.id, "start_ms": row.start_ms, "end_ms": row.end_ms}
                    for row in segments
                ],
                "artifacts": [_artifact_payload(row) for row in artifact_rows],
                "search_document_count": count,
                "triage_checks": [_triage_payload(row) for row in checks],
                "cases": [case_from_record(row).model_dump(mode="json") for row in cases],
            }

    @application.get("/api/v1/videos/{video_id}/content")
    async def video_content(video_id: str) -> FileResponse:
        with database.session() as session:
            video = session.get(VideoRecord, video_id)
            if video is None:
                raise ApiError("VIDEO_NOT_FOUND", "video not found", 404)
            path = artifacts.resolve(video.artifact_uri)
        return FileResponse(path)

    @application.get("/api/v1/artifacts/{artifact_id}/content")
    async def artifact_content(artifact_id: str) -> FileResponse:
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            if artifact is None:
                raise ApiError("ARTIFACT_NOT_FOUND", "artifact not found", 404)
            path = artifacts.resolve(artifact.uri)
        return FileResponse(path)

    @application.post("/api/v1/videos/{video_id}/simulate-stream")
    async def simulate_stream(video_id: str) -> list[SegmentBoundary]:
        try:
            return build_media_runtime(runtime_settings).service.simulate_stream(video_id)
        except LookupError as error:
            raise ApiError("VIDEO_NOT_FOUND", "video not found", 404) from error

    @application.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        try:
            return jobs.get(job_id).model_dump(mode="json")
        except JobServiceError as error:
            raise ApiError(error.code, str(error), 404) from error

    @application.post("/api/v1/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str) -> dict[str, Any]:
        try:
            jobs.retry(job_id)
            submission = await dispatcher.submit(PersistedJobRepository(database).request(job_id))
            return submission.model_dump(mode="json")
        except JobServiceError as error:
            status_code = 404 if error.code == "JOB_NOT_FOUND" else 409
            raise ApiError(error.code, str(error), status_code) from error

    @application.get("/api/v1/cases")
    async def list_cases(
        status: str | None = None,
        video_id: str | None = None,
        policy_id: str | None = None,
        model_profile: str | None = None,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        with database.session() as session:
            statement = select(CaseRecord)
            for column, value in [
                (CaseRecord.status, status),
                (CaseRecord.video_id, video_id),
                (CaseRecord.policy_id, policy_id),
                (CaseRecord.model_profile, model_profile),
            ]:
                if value:
                    statement = statement.where(column == value)
            rows = list(
                session.scalars(
                    statement.order_by(CaseRecord.created_at.desc(), CaseRecord.id)
                    .offset(cursor)
                    .limit(limit + 1)
                ).all()
            )
            items = [case_from_record(row).model_dump(mode="json") for row in rows[:limit]]
        return {"items": items, "next_cursor": cursor + limit if len(rows) > limit else None}

    @application.get("/api/v1/cases/{case_id}")
    async def get_case(case_id: str) -> dict[str, Any]:
        with database.session() as session:
            case = session.get(CaseRecord, case_id)
            if case is None:
                raise ApiError("CASE_NOT_FOUND", "case not found", 404)
            requirements = session.scalars(
                select(RequirementRecord)
                .where(RequirementRecord.case_id == case_id)
                .order_by(RequirementRecord.requirement_key)
            ).all()
            decision = (
                session.get(DecisionRecord, case.current_decision_id)
                if case.current_decision_id
                else None
            )
            appeals = session.scalars(
                select(AppealRecord)
                .where(AppealRecord.case_id == case_id)
                .order_by(AppealRecord.created_at)
            ).all()
            run = session.scalar(
                select(AgentRunRecord)
                .where(
                    AgentRunRecord.case_id == case_id, AgentRunRecord.run_kind == "INVESTIGATION"
                )
                .order_by(AgentRunRecord.created_at.desc())
            )
            requirement_payloads = []
            for requirement in requirements:
                payload = requirement_from_record(requirement).model_dump(mode="json")
                result = (
                    session.get(RequirementResultRecord, requirement.current_result_id)
                    if requirement.current_result_id
                    else None
                )
                payload["current_result"] = _record_payload(result) if result else None
                requirement_payloads.append(payload)
            return {
                "case": case_from_record(case).model_dump(mode="json"),
                "requirements": requirement_payloads,
                "current_decision": _record_payload(decision) if decision else None,
                "appeals": [_record_payload(row) for row in appeals],
                "investigation": _record_payload(run) if run else None,
                "video_content_url": f"/api/v1/videos/{case.video_id}/content",
            }

    @application.post("/api/v1/cases/{case_id}/investigate", status_code=202)
    async def investigate(case_id: str, body: InvestigationWrite) -> dict[str, Any]:
        try:
            with database.session() as session:
                case = session.get(CaseRecord, case_id)
                if case is None:
                    raise ApiError("CASE_NOT_FOUND", "case not found", 404)
                profile = body.model_profile or case.model_profile
            runtime = build_governance_runtime(runtime_settings, profile)
            if runtime.replay.agent_service is None:
                raise RuntimeError("Agent runtime is unavailable")
            request = runtime.replay.agent_service.prepare(case_id, body.model_profile)
            submission = await dispatcher.submit(request)
            return {
                "case_id": case_id,
                "run_id": request.payload["run_id"],
                **submission.model_dump(mode="json"),
            }
        except ApiError:
            raise
        except (AgentRuntimeError, GovernanceError) as error:
            raise ApiError(getattr(error, "code", "AGENT_MODEL_FAILED"), str(error), 409) from error
        except (ModelError, MediaAdapterUnavailable) as error:
            raise ApiError(
                getattr(error, "code", "MODEL_PROFILE_UNAVAILABLE"), str(error), 503
            ) from error

    @application.get("/api/v1/cases/{case_id}/timeline")
    async def case_timeline(case_id: str) -> list[dict[str, Any]]:
        try:
            return CaseTimelineService(database).timeline(case_id)
        except GovernanceError as error:
            raise ApiError(error.code, str(error), 404) from error

    @application.get("/api/v1/cases/{case_id}/trace")
    async def case_trace(case_id: str) -> dict[str, Any]:
        with database.session() as session:
            run = session.scalar(
                select(AgentRunRecord)
                .where(
                    AgentRunRecord.case_id == case_id, AgentRunRecord.run_kind == "INVESTIGATION"
                )
                .order_by(AgentRunRecord.created_at.desc())
            )
        if run is None:
            return {"result": None, "steps": [], "model_calls": [], "tool_runs": []}
        return AgentInvestigationService(database, runtime_settings).trace(run.id)

    @application.post("/api/v1/cases/{case_id}/reviews", status_code=201)
    async def review_case(
        case_id: str,
        body: ReviewWrite,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            result = HumanGovernanceService(database).submit_review(
                case_id,
                reviewer=body.reviewer,
                verdict=body.verdict,
                note=body.note,
                evidence_ids=body.evidence_ids,
                request_key=idempotency_key,
            )
            return result.model_dump(mode="json")
        except GovernanceError as error:
            raise ApiError(error.code, str(error), _governance_status(error.code)) from error

    @application.post("/api/v1/cases/{case_id}/appeals", status_code=201)
    async def submit_appeal(
        case_id: str,
        body: AppealWrite,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            result = HumanGovernanceService(database).submit_appeal(
                case_id,
                submitter=body.submitter,
                statement=body.statement,
                request_key=idempotency_key,
            )
            return result.model_dump(mode="json")
        except GovernanceError as error:
            raise ApiError(error.code, str(error), _governance_status(error.code)) from error

    @application.post("/api/v1/cases/{case_id}/appeals/{appeal_id}/resolve", status_code=201)
    async def resolve_appeal(
        case_id: str,
        appeal_id: str,
        body: AppealResolveWrite,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        with database.session() as session:
            appeal = session.get(AppealRecord, appeal_id)
            if appeal is None or appeal.case_id != case_id:
                raise ApiError("APPEAL_NOT_FOUND", "appeal not found", 404)
        try:
            result = HumanGovernanceService(database).resolve_appeal(
                appeal_id,
                reviewer=body.reviewer,
                verdict=body.verdict,
                note=body.note,
                evidence_ids=body.evidence_ids,
                request_key=idempotency_key,
            )
            return result.model_dump(mode="json")
        except GovernanceError as error:
            raise ApiError(error.code, str(error), _governance_status(error.code)) from error

    @application.post("/api/v1/policies", status_code=201)
    async def write_policy(body: PolicyWrite) -> dict[str, Any]:
        try:
            source = load_policy_source(body.source_yaml)
            service = PolicyVersionService(database)
            result = (
                service.save_draft(source) if body.lifecycle == "draft" else service.publish(source)
            )
            return result.model_dump(mode="json")
        except PolicyError as error:
            raise ApiError(error.code, str(error), 422) from error
        except PolicyVersionError as error:
            raise ApiError(error.code, str(error), 409) from error

    @application.get("/api/v1/policies/{policy_id}/versions")
    async def policy_versions(policy_id: str) -> dict[str, Any]:
        versions = PolicyVersionService(database).list_versions(policy_id)
        return {"items": [row.model_dump(mode="json") for row in versions], "next_cursor": None}

    @application.post("/api/v1/policies/{policy_id}/replay/preview")
    async def replay_preview(policy_id: str, body: ReplayPreviewWrite) -> dict[str, Any]:
        try:
            result = ReplayPlanner(database).preview(
                policy_id,
                body.from_version,
                body.to_version,
                model_change_policy=body.model_change_policy,
            )
            return result.model_dump(mode="json")
        except GovernanceError as error:
            raise ApiError(error.code, str(error), _governance_status(error.code)) from error

    @application.post("/api/v1/policies/{policy_id}/replay", status_code=202)
    async def run_replay(policy_id: str, body: ReplayWrite) -> dict[str, Any]:
        try:
            runtime = build_governance_runtime(runtime_settings, body.model_profile)
            request = runtime.replay.prepare(
                policy_id,
                body.from_version,
                body.to_version,
                body.preview_sha256,
                model_profile=body.model_profile,
                model_change_policy=body.model_change_policy,
            )
            submission = await dispatcher.submit(request)
            return {
                "replay_job_id": request.payload["replay_job_id"],
                **submission.model_dump(mode="json"),
            }
        except (GovernanceError, AgentRuntimeError, ModelError) as error:
            raise ApiError(
                getattr(error, "code", "REPLAY_NOT_RESUMABLE"), str(error), 409
            ) from error

    @application.get("/api/v1/replay-jobs/{job_id}")
    async def replay_status(job_id: str) -> Any:
        service = ReplayApplicationService(
            database, ReplayPlanner(database), GovernanceApplicationService(database)
        )
        try:
            result = service.status(job_id)
            return result.model_dump(mode="json") if isinstance(result, BaseModel) else result
        except GovernanceError as error:
            raise ApiError(error.code, str(error), 404) from error

    @application.get("/api/v1/replay-jobs/{job_id}/diff")
    async def replay_diff(job_id: str) -> list[dict[str, Any]]:
        service = ReplayApplicationService(
            database, ReplayPlanner(database), GovernanceApplicationService(database)
        )
        try:
            return service.diff(job_id)
        except GovernanceError as error:
            raise ApiError(error.code, str(error), 404) from error

    @application.get("/api/v1/model-profiles")
    async def model_profiles() -> dict[str, Any]:
        items = [
            _profile_payload(runtime_settings, path.stem)
            for path in sorted(runtime_settings.model_config_dir.glob("*.yaml"))
        ]
        return {"items": [row for row in items if row is not None], "next_cursor": None}

    @application.get("/api/v1/model-profiles/{profile}/health")
    async def model_profile_health(profile: str) -> dict[str, Any]:
        try:
            document = load_model_profile(runtime_settings.model_config_dir, profile)
            environment = runtime_settings.model_environment()
            chat_results: list[dict[str, Any]] = []
            seen_models: set[str] = set()
            for role in (
                ModelRole.AGENT,
                ModelRole.TRIAGE,
                ModelRole.VERIFIER,
                ModelRole.JUDGE,
            ):
                resolved = resolve_model_profile(document, role, environment)
                if resolved.model in seen_models:
                    continue
                seen_models.add(resolved.model)
                response = await build_model_gateway(
                    runtime_settings.model_config_dir,
                    profile,
                    role,
                    environment=environment,
                ).generate(
                    ModelRequest(
                        role=role,
                        messages=[
                            ModelMessage(
                                role="user",
                                content='Return JSON exactly as {"ok": true}.',
                            )
                        ],
                        response_schema=ProfileHealthProbe,
                        timeout_seconds=min(resolved.defaults.timeout_seconds, 15),
                        trace_id=f"profile-health-{profile}-{role}",
                    )
                )
                ProfileHealthProbe.model_validate(response.data)
                chat_results.append(
                    {
                        "requested_model": resolved.model,
                        "actual_model": response.actual_model,
                        "latency_ms": response.latency_ms,
                    }
                )
            embedding_gateway, embedding = resolve_embedding_gateway(
                runtime_settings.model_config_dir,
                profile,
                environment=environment,
            )
            embedding_response = await embedding_gateway.embed(
                EmbeddingRequest(
                    texts=("EviStream profile health",),
                    dimensions=embedding.dimensions,
                    timeout_seconds=15,
                    trace_id=f"profile-health-{profile}-embedding",
                )
            )
            return {
                "status": "ok",
                "profile": profile,
                "gateway": document.gateway,
                "chat": chat_results,
                "embedding": {
                    "requested_model": embedding.model,
                    "actual_model": embedding_response.actual_model,
                    "dimensions": len(embedding_response.vectors[0].values),
                    "latency_ms": embedding_response.latency_ms,
                },
                "capabilities": document.capabilities.model_dump(mode="json"),
            }
        except ModelError as error:
            raise ApiError("MODEL_PROFILE_UNAVAILABLE", str(error), 503) from error

    return application


def _error_response(code: str, message: str, status: int, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error_code": code,
            "message": message,
            "correlation_id": correlation_id_var.get(),
            "details": details or {},
        },
    )


def _validate_profile(settings: Settings, name: str) -> None:
    try:
        profile = load_model_profile(settings.model_config_dir, name)
        resolve_model_profile(profile, ModelRole.TRIAGE, settings.model_environment())
        resolve_embedding_gateway(
            settings.model_config_dir, name, environment=settings.model_environment()
        )
    except ModelError as error:
        raise ApiError("MODEL_PROFILE_UNAVAILABLE", str(error), 503) from error


def _video_payload(record: VideoRecord) -> dict[str, Any]:
    return {
        "video_id": record.id,
        "original_name": record.original_name,
        "duration_ms": record.duration_ms,
        "width": record.width,
        "height": record.height,
        "container": record.container,
        "video_codec": record.video_codec,
        "has_audio": record.has_audio,
        "audio_codec": record.audio_codec,
        "status": record.status,
        "model_profile": record.model_profile,
        "triage_status": record.triage_status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _artifact_payload(record: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": record.id,
        "segment_id": record.segment_id,
        "type": record.type,
        "metadata": record.artifact_metadata,
        "content_url": f"/api/v1/artifacts/{record.id}/content",
    }


def _triage_payload(record: VideoTriageCheckRecord) -> dict[str, Any]:
    return {
        "triage_check_id": record.id,
        "policy_id": record.policy_id,
        "policy_version": record.policy_version,
        "status": record.status,
        "action": record.action,
        "confidence": float(record.confidence) if record.confidence is not None else None,
        "reason_code": record.reason_code,
        "matched_terms": record.matched_terms,
        "matched_requirement_keys": record.matched_requirement_keys,
        "summary": record.summary,
        "case_id": record.case_id,
        "error_code": record.error_code,
    }


def _record_payload(record: Any) -> dict[str, Any]:
    hidden = {"state_snapshot", "request_summary", "response_payload"}
    return {
        column.name: getattr(record, column.name)
        for column in record.__table__.columns
        if column.name not in hidden
    }


def _profile_payload(settings: Settings, name: str) -> dict[str, Any] | None:
    try:
        profile = load_model_profile(settings.model_config_dir, name)
        configured = True
        models: dict[str, str | None] = {}
        for role in ModelRole:
            try:
                models[str(role)] = resolve_model_profile(
                    profile, role, settings.model_environment()
                ).model
            except ModelError:
                configured = False
                models[str(role)] = None
        try:
            _, embedding = resolve_embedding_gateway(
                settings.model_config_dir, name, environment=settings.model_environment()
            )
            models["embedding"] = embedding.model
        except ModelError:
            configured = False
            models["embedding"] = None
        return {
            "name": profile.name,
            "gateway": profile.gateway,
            "configured": configured,
            "models": models,
            "capabilities": profile.capabilities.model_dump(mode="json"),
        }
    except ModelError:
        return None


def _governance_status(code: str) -> int:
    return (
        404
        if code.endswith("NOT_FOUND") or code in {"CASE_NOT_FOUND", "DECISION_NOT_FOUND"}
        else 409
    )


app = create_app()
