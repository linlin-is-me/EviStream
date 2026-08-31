from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from evistream.cli import app
from evistream.media.probe import MediaProbeResult

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
