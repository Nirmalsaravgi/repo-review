from fastapi.testclient import TestClient

from api import app


def test_health_endpoint():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ok", "degraded"}
    assert "github_configured" in body
    assert "database" in body
