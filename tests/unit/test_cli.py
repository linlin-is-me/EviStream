from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from evistream.agent.types import InvestigationResult, InvestigationStatus
from evistream.application import JobExecution, JobRequest, JobStatus
from evistream.cli import app
from evistream.media.probe import MediaProbeResult
from evistream.models import MockEmbeddingGateway
from evistream.models.profiles import ResolvedEmbeddingProfile
from evistream.retrieval import IndexFailure, IndexSummary

runner = CliRunner()


def test_demo_job_cli() -> None:
    result = runner.invoke(app, ["run-demo-job", "--message", "stage zero"])

    assert result.exit_code == 0
    assert '"status": "SUCCEEDED"' in result.stdout
    assert '"uppercase": "STAGE ZERO"' in result.stdout


def test_mock_model_smoke_cli() -> None:
    result = runner.invoke(app, ["model-smoke", "--profile", "mock"])

    assert result.exit_code == 0
    assert '"actual_model": "mock-stage0"' in result.stdout


def test_mock_asr_cli(tmp_path: Path) -> None:
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"synthetic")

    result = runner.invoke(app, ["asr-smoke", str(media), "--backend", "mock"])

    assert result.exit_code == 0
    assert '"model": "mock-asr"' in result.stdout


def test_unknown_asr_backend_has_structured_error(tmp_path: Path) -> None:
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"synthetic")

    result = runner.invoke(app, ["asr-smoke", str(media), "--backend", "unknown"])

    assert result.exit_code == 1
    assert "INPUT_INVALID" in result.stdout


def test_probe_cli_outputs_normalized_json(tmp_path: Path) -> None:
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"synthetic")
    probe_result = MediaProbeResult(
        path=str(media),
        duration_ms=30000,
        width=640,
        height=360,
        container="mov,mp4",
        video_codec="h264",
        has_audio=True,
        audio_codec="aac",
    )

    with patch("evistream.cli.probe_video", return_value=probe_result):
        result = runner.invoke(app, ["probe-video", str(media)])

    assert result.exit_code == 0
    assert '"duration_ms": 30000' in result.stdout


def test_retrieval_index_returns_nonzero_for_partial_indexing() -> None:
    profile = ResolvedEmbeddingProfile(
        name="mock",
        gateway="mock",
        base_url=None,
        api_key=None,
        model="mock-embedding-v1",
        dimensions=1536,
        batch_size=10,
        timeout_seconds=5,
        max_attempts=1,
    )
    summary = IndexSummary(
        status="partial",
        error_code="EMBEDDING_INDEX_PARTIAL",
        video_id="video",
        total=2,
        indexed=1,
        skipped=0,
        failed=1,
        actual_model="mock-embedding-v1",
        embedding_space="space",
        dimensions=1536,
        prompt_tokens=2,
        failures=[
            IndexFailure(
                batch_index=1,
                document_ids=["doc-2"],
                error_code="MODEL_UNAVAILABLE",
                retryable=True,
            )
        ],
    )
    with (
        patch(
            "evistream.cli.resolve_embedding_gateway",
            return_value=(MockEmbeddingGateway(), profile),
        ),
        patch("evistream.cli.EmbeddingIndexService") as service,
    ):
        service.return_value.index_video = AsyncMock(return_value=summary)
        result = runner.invoke(app, ["retrieval-index", "video", "--profile", "mock"])

    assert result.exit_code == 1
    assert '"status": "partial"' in result.stdout
    assert "EMBEDDING_INDEX_PARTIAL" in result.stdout


def test_investigate_cli_treats_human_review_as_success() -> None:
    request = JobRequest(
        job_id="job",
        job_type="AGENT_INVESTIGATION",
        request_key="key",
        correlation_id="correlation",
        payload={"run_id": "run", "case_id": "case", "model_profile": "mock"},
    )
    investigation = InvestigationResult(
        run_id="run",
        job_id="job",
        case_id="case",
        status=InvestigationStatus.NEEDS_HUMAN_REVIEW,
        stop_reason="REQUIRED_EVIDENCE_MISSING",
        state_version=5,
    )
    service = MagicMock()
    service.prepare.return_value = request
    service.get_result.return_value = investigation
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(
        return_value=JobExecution(
            job_id="job",
            job_type="AGENT_INVESTIGATION",
            status=JobStatus.SUCCEEDED,
            result=investigation.model_dump(mode="json"),
            elapsed_ms=1,
        )
    )
    runtime = SimpleNamespace(service=service, dispatcher=dispatcher)
    database = MagicMock()
    database.session.return_value.__enter__.return_value.get.return_value = SimpleNamespace(
        model_profile="mock"
    )
    with (
        patch("evistream.cli.Database", return_value=database),
        patch("evistream.cli.build_agent_runtime", return_value=runtime),
    ):
        result = runner.invoke(app, ["investigate", "case"])

    assert result.exit_code == 0
    assert '"status": "NEEDS_HUMAN_REVIEW"' in result.stdout
    assert "REQUIRED_EVIDENCE_MISSING" in result.stdout
