"""Stage 0 command-line verification interface."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, NoReturn
from uuid import uuid4

import typer
from dotenv import load_dotenv
from pydantic import BaseModel

from evistream.agent.errors import AgentRuntimeError
from evistream.agent.runtime import build_agent_runtime
from evistream.agent.service import AgentInvestigationService
from evistream.application import (
    ApplicationService,
    DemoJobHandler,
    HandlerRegistry,
    InlineExecutor,
)
from evistream.config import Settings, get_settings
from evistream.domain import Verdict
from evistream.governance.errors import GovernanceError
from evistream.governance.review import HumanGovernanceService
from evistream.governance.runtime import build_governance_runtime
from evistream.governance.service import GovernanceApplicationService
from evistream.governance.timeline import CaseTimelineService
from evistream.jobs.runtime import requeue_due
from evistream.media.asr import ASRAdapter, ASRRequest, FasterWhisperASR, MockASR
from evistream.media.probe import MediaProbeError, probe_video
from evistream.media.runtime import MediaAdapterUnavailable, build_media_runtime
from evistream.models import (
    EmbeddingRequest,
    ModelError,
    ModelMessage,
    ModelRequest,
    ModelRole,
    build_model_gateway,
    resolve_embedding_gateway,
)
from evistream.policies.compiler import PolicyCompiler
from evistream.policies.schema import PolicyError, load_policy
from evistream.policies.seeds import apply_demo_seeds, validate_demo_seeds
from evistream.policies.versioning import PolicyVersionError, PolicyVersionService
from evistream.replay.planner import ReplayPlanner
from evistream.replay.service import ReplayApplicationService
from evistream.retrieval import EmbeddingIndexService, HybridRetrievalService
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database
from evistream.storage.models import CaseRecord
from evistream.tools import ToolExecutor, ToolRequest, build_default_registry

app = typer.Typer(no_args_is_help=True, help="EviStream verification and development commands.")


class SmokeOutput(BaseModel):
    ok: bool
    summary: str


@app.command("jobs-requeue")
def jobs_requeue(
    due_only: Annotated[bool, typer.Option(help="Only enqueue due jobs.")] = True,
) -> None:
    settings = _load_settings()
    try:
        count = requeue_due(settings, due_only=due_only)
    except Exception as error:
        _fail("QUEUE_UNAVAILABLE", str(error))
    typer.echo(json.dumps({"enqueued": count}, indent=2))


@app.command("worker")
def worker() -> None:
    settings = _load_settings()
    requeue_due(settings)
    from redis import Redis
    from rq import Queue, Worker

    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.rq_queue, connection=connection)
    Worker([queue], connection=connection, name="evistream-worker").work(
        with_scheduler=True
    )


@app.command("run-demo-job")
def run_demo_job(
    message: Annotated[str, typer.Option(help="Message handled by the demo job.")] = "stage0",
) -> None:
    registry = HandlerRegistry()
    registry.register("DEMO", DemoJobHandler())
    service = ApplicationService(InlineExecutor(registry))
    execution = asyncio.run(service.run_demo_job(message))
    typer.echo(execution.model_dump_json(indent=2))
    if execution.error_code:
        raise typer.Exit(code=1)


@app.command("probe-video")
def probe_video_command(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    settings = _load_settings()
    try:
        result = probe_video(
            path,
            ffprobe_binary=settings.ffprobe_binary,
            timeout_seconds=settings.process_timeout_seconds,
        )
    except MediaProbeError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))


@app.command("model-smoke")
def model_smoke(
    profile: Annotated[str | None, typer.Option(help="Model profile from configs/models.")] = None,
) -> None:
    settings = _load_settings()
    selected_profile = profile or settings.model_profile
    try:
        gateway = build_model_gateway(
            settings.model_config_dir,
            selected_profile,
            ModelRole.AGENT,
        )
        request = ModelRequest(
            role=ModelRole.AGENT,
            messages=[
                ModelMessage(
                    role="system",
                    content="Return only a JSON object that satisfies the requested fields.",
                ),
                ModelMessage(
                    role="user",
                    content='Return {"ok": true, "summary": "stage0"}.',
                ),
            ],
            response_schema=SmokeOutput,
            timeout_seconds=30,
            trace_id="stage0-model-smoke",
        )
        response = asyncio.run(gateway.generate(request))
    except ModelError as error:
        _fail(error.code, str(error))
    typer.echo(response.model_dump_json(indent=2))


@app.command("asr-smoke")
def asr_smoke(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    backend: Annotated[
        str | None,
        typer.Option(help="ASR backend: mock or faster-whisper."),
    ] = None,
) -> None:
    settings = _load_settings()
    selected_backend = backend or settings.asr_backend
    if selected_backend == "mock":
        adapter: ASRAdapter = MockASR()
    elif selected_backend == "faster-whisper":
        adapter = FasterWhisperASR(
            settings.asr_model,
            device=settings.asr_device,
            compute_type=settings.asr_compute_type,
        )
    else:
        _fail("INPUT_INVALID", f"unknown ASR backend: {selected_backend}")
    result = adapter.transcribe(ASRRequest(media_path=path, language="en"))
    typer.echo(result.model_dump_json(indent=2))


@app.command("embedding-smoke")
def embedding_smoke(
    profile: Annotated[str | None, typer.Option(help="Model profile from configs/models.")] = None,
) -> None:
    settings = _load_settings()
    selected_profile = profile or settings.model_profile
    try:
        gateway, resolved = resolve_embedding_gateway(
            settings.model_config_dir, selected_profile
        )
        response = asyncio.run(
            gateway.embed(
                EmbeddingRequest(
                    texts=("EviStream evidence retrieval",),
                    dimensions=resolved.dimensions,
                    timeout_seconds=resolved.timeout_seconds,
                    trace_id="stage3-embedding-smoke",
                )
            )
        )
    except ModelError as error:
        _fail(error.code, str(error))
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "profile": selected_profile,
                "actual_model": response.actual_model,
                "dimensions": len(response.vectors[0].values),
                "usage": response.usage.model_dump(mode="json"),
                "latency_ms": response.latency_ms,
                "provider_request_id": response.provider_request_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("media-ingest")
def media_ingest(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    process: Annotated[bool, typer.Option(help="Run preprocessing immediately.")] = False,
) -> None:
    try:
        runtime = build_media_runtime(_load_settings())
    except MediaAdapterUnavailable as error:
        _fail(error.code, str(error))
    video, job = runtime.service.register_file(path)
    if process:
        execution = asyncio.run(
            runtime.dispatcher.dispatch(runtime.service.build_job_request(job.job_id))
        )
        job = runtime.service.get_job(job.job_id)
        video = runtime.service.get_video(video.video_id)
        if execution.error_code:
            _fail(execution.error_code, execution.error_message or "media job failed")
    typer.echo(
        json.dumps(
            {"video": video.model_dump(mode="json"), "job": job.model_dump(mode="json")},
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("media-process")
def media_process(job_id: Annotated[str, typer.Argument()]) -> None:
    try:
        runtime = build_media_runtime(_load_settings())
        execution = asyncio.run(
            runtime.dispatcher.dispatch(runtime.service.build_job_request(job_id))
        )
    except MediaAdapterUnavailable as error:
        _fail(error.code, str(error))
    except LookupError:
        _fail("JOB_NOT_FOUND", f"job not found: {job_id}")
    if execution.error_code:
        _fail(execution.error_code, execution.error_message or "media job failed")
    job = runtime.service.get_job(job_id)
    typer.echo(job.model_dump_json(indent=2))


@app.command("retrieval-index")
def retrieval_index(
    video_id: Annotated[str, typer.Argument()],
    profile: Annotated[str | None, typer.Option(help="Embedding model profile.")] = None,
    force: Annotated[bool, typer.Option(help="Refresh vectors even when current.")] = False,
) -> None:
    settings = _load_settings()
    selected_profile = profile or settings.model_profile
    try:
        gateway, resolved = resolve_embedding_gateway(
            settings.model_config_dir, selected_profile
        )
        summary = asyncio.run(
            EmbeddingIndexService(
                Database(settings.database_url), gateway, resolved
            ).index_video(video_id, force=force)
        )
    except ModelError as error:
        _fail(error.code, str(error))
    except LookupError:
        _fail("VIDEO_NOT_FOUND", f"video not found: {video_id}")
    typer.echo(summary.model_dump_json(indent=2))
    if summary.status != "success":
        raise typer.Exit(code=1)


@app.command("tool-run")
def tool_run(
    tool_name: Annotated[str, typer.Argument()],
    case_id: Annotated[str, typer.Option()],
    requirement_id: Annotated[str, typer.Option()],
    query: Annotated[str, typer.Option()] = "",
    start_ms: Annotated[int | None, typer.Option()] = None,
    end_ms: Annotated[int | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=50)] = 5,
) -> None:
    settings = _load_settings()
    database = Database(settings.database_url)
    with database.session() as session:
        case = session.get(CaseRecord, case_id)
        profile_name = case.model_profile if case is not None else settings.model_profile
    try:
        gateway, resolved = resolve_embedding_gateway(
            settings.model_config_dir, profile_name
        )
        retrieval = HybridRetrievalService(
            database,
            gateway,
            resolved,
            rrf_k=settings.retrieval_rrf_k,
            candidate_limit=settings.retrieval_candidate_limit,
        )
        registry = build_default_registry(
            database,
            LocalArtifactStore(settings.artifact_root),
            settings,
            retrieval,
        )
        request = ToolRequest(
            correlation_id=f"corr_{uuid4().hex}",
            run_id=f"manual_{uuid4().hex}",
            case_id=case_id,
            requirement_id=requirement_id,
            query=query,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit,
        )
        result = asyncio.run(ToolExecutor(database, registry).execute(tool_name, request))
    except ModelError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))
    if result.status == "failed":
        raise typer.Exit(code=1)


@app.command("investigate")
def investigate(
    case_id: Annotated[str, typer.Argument()],
    profile: Annotated[str | None, typer.Option(help="Model profile for a new run.")] = None,
) -> None:
    settings = _load_settings()
    database = Database(settings.database_url)
    with database.session() as session:
        case = session.get(CaseRecord, case_id)
        if case is None:
            _fail("CASE_NOT_FOUND", f"case not found: {case_id}")
        selected_profile = profile or case.model_profile
    try:
        runtime = build_agent_runtime(settings, selected_profile)
        request = runtime.service.prepare(case_id, profile)
        execution = asyncio.run(runtime.dispatcher.dispatch(request))
    except AgentRuntimeError as error:
        _fail(error.code, str(error))
    except (MediaAdapterUnavailable, ModelError) as error:
        _fail(getattr(error, "code", "MODEL_UNAVAILABLE"), str(error))
    if execution.error_code:
        _fail(execution.error_code, execution.error_message or "investigation failed")
    run_id = str(request.payload["run_id"])
    result = runtime.service.get_result(run_id)
    typer.echo(result.model_dump_json(indent=2))


@app.command("investigation-status")
def investigation_status(run_id: Annotated[str, typer.Argument()]) -> None:
    try:
        result = AgentInvestigationService(
            Database(_load_settings().database_url), _load_settings()
        ).get_result(run_id)
    except AgentRuntimeError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))


@app.command("investigation-trace")
def investigation_trace(run_id: Annotated[str, typer.Argument()]) -> None:
    settings = _load_settings()
    try:
        trace = AgentInvestigationService(Database(settings.database_url), settings).trace(run_id)
    except AgentRuntimeError as error:
        _fail(error.code, str(error))
    typer.echo(json.dumps(trace, ensure_ascii=False, indent=2))


@app.command("policy-validate")
def policy_validate(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    try:
        loaded = load_policy(path)
        compiled = PolicyCompiler().compile(loaded)
    except PolicyError as error:
        _fail(error.code, str(error))
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "policy_id": compiled.policy_id,
                "version": compiled.version,
                "source_sha256": loaded.source_sha256,
                "semantic_sha256": compiled.semantic_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("policy-compile")
def policy_compile(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    try:
        compiled = PolicyCompiler().compile(load_policy(path))
    except PolicyError as error:
        _fail(error.code, str(error))
    typer.echo(compiled.model_dump_json(indent=2))


@app.command("policy-publish")
def policy_publish(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    lifecycle: Annotated[
        str, typer.Option(help="Policy lifecycle: draft or published.")
    ] = "published",
) -> None:
    settings = _load_settings()
    service = PolicyVersionService(Database(settings.database_url))
    try:
        source = load_policy(path)
        if lifecycle.lower() == "draft":
            policy = service.save_draft(source)
        elif lifecycle.lower() == "published":
            policy = service.publish(source)
        else:
            _fail("INPUT_INVALID", f"unknown policy lifecycle: {lifecycle}")
    except PolicyError as error:
        _fail(error.code, str(error))
    except PolicyVersionError as error:
        _fail(error.code, str(error))
    typer.echo(policy.model_dump_json(indent=2))


@app.command("seed-demo")
def seed_demo(
    check: Annotated[bool, typer.Option(help="Validate metadata without database writes.")] = False,
    apply: Annotated[bool, typer.Option(help="Publish policies and create mapped cases.")] = False,
    video_map: Annotated[
        Path | None,
        typer.Option(exists=True, dir_okay=False, help="fixture_ref to video_id YAML map."),
    ] = None,
    model_profile: Annotated[str, typer.Option()] = "mock",
) -> None:
    if check == apply:
        _fail("INPUT_INVALID", "select exactly one of --check or --apply")
    policy_dir = Path("configs/policies")
    manifest = Path("configs/demo/stage2-cases.yaml")
    try:
        if check:
            summary = validate_demo_seeds(policy_dir, manifest)
        else:
            if video_map is None:
                _fail("INPUT_INVALID", "--video-map is required with --apply")
            summary = apply_demo_seeds(
                Database(_load_settings().database_url),
                policy_dir,
                manifest,
                video_map,
                model_profile=model_profile,
            )
    except PolicyError as error:
        _fail(error.code, str(error))
    except PolicyVersionError as error:
        _fail(error.code, str(error))
    typer.echo(summary.model_dump_json(indent=2))


@app.command("case-evaluate")
def case_evaluate(case_id: Annotated[str, typer.Argument()]) -> None:
    try:
        result = GovernanceApplicationService(
            Database(_load_settings().database_url)
        ).finalize_case(case_id)
    except GovernanceError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))


@app.command("case-timeline")
def case_timeline(case_id: Annotated[str, typer.Argument()]) -> None:
    try:
        result = CaseTimelineService(
            Database(_load_settings().database_url)
        ).timeline(case_id)
    except GovernanceError as error:
        _fail(error.code, str(error))
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("case-review")
def case_review(
    case_id: Annotated[str, typer.Argument()],
    reviewer: Annotated[str, typer.Option()],
    verdict: Annotated[Verdict, typer.Option()],
    note: Annotated[str, typer.Option()] = "human review",
) -> None:
    try:
        result = HumanGovernanceService(
            Database(_load_settings().database_url)
        ).submit_review(case_id, reviewer=reviewer, verdict=verdict, note=note)
    except GovernanceError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))


@app.command("appeal-submit")
def appeal_submit(
    case_id: Annotated[str, typer.Argument()],
    submitter: Annotated[str, typer.Option()],
    statement: Annotated[str, typer.Option()],
) -> None:
    try:
        result = HumanGovernanceService(
            Database(_load_settings().database_url)
        ).submit_appeal(case_id, submitter=submitter, statement=statement)
    except GovernanceError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))


@app.command("appeal-resolve")
def appeal_resolve(
    appeal_id: Annotated[str, typer.Argument()],
    reviewer: Annotated[str, typer.Option()],
    verdict: Annotated[Verdict, typer.Option()],
    note: Annotated[str, typer.Option()] = "appeal resolution",
) -> None:
    try:
        result = HumanGovernanceService(
            Database(_load_settings().database_url)
        ).resolve_appeal(appeal_id, reviewer=reviewer, verdict=verdict, note=note)
    except GovernanceError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))


@app.command("replay-preview")
def replay_preview(
    policy_id: Annotated[str, typer.Argument()],
    from_version: Annotated[int, typer.Option("--from-version", min=1)],
    to_version: Annotated[int, typer.Option("--to-version", min=1)],
    model_change_policy: Annotated[
        str, typer.Option(help="keep or invalidate-visual")
    ] = "keep",
) -> None:
    try:
        result = ReplayPlanner(Database(_load_settings().database_url)).preview(
            policy_id,
            from_version,
            to_version,
            model_change_policy=model_change_policy,
        )
    except GovernanceError as error:
        _fail(error.code, str(error))
    typer.echo(result.model_dump_json(indent=2))


@app.command("replay-run")
def replay_run(
    policy_id: Annotated[str, typer.Argument()],
    from_version: Annotated[int, typer.Option("--from-version", min=1)],
    to_version: Annotated[int, typer.Option("--to-version", min=1)],
    preview_hash: Annotated[str, typer.Option("--preview-hash")],
    profile: Annotated[str | None, typer.Option()] = None,
    model_change_policy: Annotated[
        str, typer.Option(help="keep or invalidate-visual")
    ] = "keep",
) -> None:
    settings = _load_settings()
    try:
        preview = ReplayPlanner(Database(settings.database_url)).preview(
            policy_id,
            from_version,
            to_version,
            model_change_policy=model_change_policy,
        )
        needs_agent = any(item.investigate_requirement_keys for item in preview.cases)
        runtime = build_governance_runtime(
            settings, profile or settings.model_profile if needs_agent else None
        )
        request = runtime.replay.prepare(
            policy_id,
            from_version,
            to_version,
            preview_hash,
            model_profile=profile,
            model_change_policy=model_change_policy,
        )
        execution = asyncio.run(runtime.dispatcher.dispatch(request))
    except (GovernanceError, AgentRuntimeError, ModelError) as error:
        _fail(getattr(error, "code", "REPLAY_NOT_RESUMABLE"), str(error))
    if execution.error_code:
        _fail(execution.error_code, execution.error_message or "replay failed")
    typer.echo(json.dumps(execution.result, ensure_ascii=False, indent=2))


@app.command("replay-status")
def replay_status(job_id: Annotated[str, typer.Argument()]) -> None:
    settings = _load_settings()
    database = Database(settings.database_url)
    service = ReplayApplicationService(
        database,
        ReplayPlanner(database),
        GovernanceApplicationService(database),
    )
    try:
        result = service.status(job_id)
    except GovernanceError as error:
        _fail(error.code, str(error))
    if isinstance(result, BaseModel):
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("replay-diff")
def replay_diff(job_id: Annotated[str, typer.Argument()]) -> None:
    settings = _load_settings()
    database = Database(settings.database_url)
    service = ReplayApplicationService(
        database,
        ReplayPlanner(database),
        GovernanceApplicationService(database),
    )
    try:
        result = service.diff(job_id)
    except GovernanceError as error:
        _fail(error.code, str(error))
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


def _load_settings() -> Settings:
    load_dotenv()
    get_settings.cache_clear()
    return get_settings()


def _fail(code: object, message: str) -> NoReturn:
    typer.echo(json.dumps({"error_code": str(code), "message": message}, ensure_ascii=False))
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
