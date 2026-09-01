from fastapi.testclient import TestClient

from apps.api.main import create_app
from evistream.config import Settings


def test_health_endpoint_reports_runtime_mode() -> None:
    app = create_app(Settings(environment="test", _env_file=None))

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "evistream-api",
        "version": "0.1.0.dev0",
        "mode": "test",
    }


def test_upload_rejects_body_while_streaming_before_runtime_construction() -> None:
    app = create_app(Settings(environment="test", upload_max_bytes=4, _env_file=None))

    response = TestClient(app).post(
        "/api/v1/videos",
        files={"file": ("large.mp4", b"12345", "video/mp4")},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INPUT_INVALID"
    assert response.json()["message"] == "uploaded file exceeds configured limit"
    assert response.headers["x-correlation-id"].startswith("corr_")


def test_stage6_openapi_exposes_public_workflow_routes() -> None:
    app = create_app(Settings(environment="test", _env_file=None))
    schema = app.openapi()

    expected_paths = {
        "/api/v1/videos",
        "/api/v1/jobs/{job_id}",
        "/api/v1/cases/{case_id}/investigate",
        "/api/v1/cases/{case_id}/reviews",
        "/api/v1/cases/{case_id}/appeals",
        "/api/v1/policies/{policy_id}/replay/preview",
        "/api/v1/model-profiles/{profile}/health",
    }
    assert expected_paths <= set(schema["paths"])
