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
