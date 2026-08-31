"""Stage 0 command-line verification interface."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from dotenv import load_dotenv
from pydantic import BaseModel

from evistream.application import (
    ApplicationService,
    DemoJobHandler,
    HandlerRegistry,
    InlineExecutor,
)
from evistream.config import Settings, get_settings
from evistream.media.asr import ASRAdapter, ASRRequest, FasterWhisperASR, MockASR
from evistream.media.extractors import MockOCR, MockVisualDescription
from evistream.media.probe import MediaProbeError, probe_video
from evistream.media.service import MediaApplicationService
from evistream.models import (
    ModelError,
    ModelMessage,
    ModelRequest,
    ModelRole,
    build_model_gateway,
)
from evistream.policies.compiler import PolicyCompiler
from evistream.policies.schema import PolicyError, load_policy
from evistream.policies.seeds import apply_demo_seeds, validate_demo_seeds
from evistream.policies.versioning import PolicyVersionError, PolicyVersionService
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database

app = typer.Typer(no_args_is_help=True, help="EviStream Stage 0 verification commands.")


class SmokeOutput(BaseModel):
    ok: bool
    summary: str


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


@app.command("media-ingest")
def media_ingest(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    process: Annotated[bool, typer.Option(help="Run preprocessing immediately.")] = False,
) -> None:
    service = _media_service(_load_settings())
    video, job = service.register_file(path)
    if process:
        job = service.process_job(job.job_id)
        video = service.get_video(video.video_id)
    typer.echo(
        json.dumps(
            {"video": video.model_dump(mode="json"), "job": job.model_dump(mode="json")},
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("media-process")
def media_process(job_id: Annotated[str, typer.Argument()]) -> None:
    service = _media_service(_load_settings())
    job = service.process_job(job_id)
    typer.echo(job.model_dump_json(indent=2))


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


def _load_settings() -> Settings:
    load_dotenv()
    get_settings.cache_clear()
    return get_settings()


def _media_service(settings: Settings) -> MediaApplicationService:
    return MediaApplicationService(
        Database(settings.database_url),
        LocalArtifactStore(settings.artifact_root),
        settings,
        MockASR(),
        MockOCR(),
        MockVisualDescription(),
    )


def _fail(code: object, message: str) -> NoReturn:
    typer.echo(json.dumps({"error_code": str(code), "message": message}, ensure_ascii=False))
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
