"""Stage 0 command-line verification interface."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

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
from evistream.media.probe import MediaProbeError, probe_video
from evistream.models import (
    ModelError,
    ModelMessage,
    ModelRequest,
    ModelRole,
    build_model_gateway,
)

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


def _load_settings() -> Settings:
    load_dotenv()
    get_settings.cache_clear()
    return get_settings()


def _fail(code: object, message: str) -> None:
    typer.echo(json.dumps({"error_code": str(code), "message": message}, ensure_ascii=False))
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
