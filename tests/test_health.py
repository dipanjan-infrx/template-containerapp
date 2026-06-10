"""Baseline tests for the service. The Issue Fix Bot adds more tests alongside
these when it generates a fix."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_home_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "running in" in r.text
