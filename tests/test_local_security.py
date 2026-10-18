"""Real HTTP boundary checks with invented private data and no network calls."""
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as session:
        yield session


@pytest.mark.parametrize("host", ["attacker.example", "127.0.0.1.attacker.example", "localhost.attacker.example", "192.168.1.10", "0.0.0.0", "user@localhost", "127.0.0.1:0"])
def test_rebinding_and_nonlocal_hosts_cannot_read_or_write(client, host):
    for method, path in [("GET", "/api/studio/models"), ("GET", "/api/studio/projects/export"), ("POST", "/api/studio/models")]:
        response = client.request(method, path, headers={"Host": host})
        assert response.status_code == 400
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Origin": "http://localhost:5173"}, {"Sec-Fetch-Site": "cross-site"},
])
def test_foreign_browser_cannot_read_download_or_mutate(client, headers):
    for method, path in [("GET", "/api/studio/documents"), ("GET", "/api/studio/projects/export"), ("POST", "/api/studio/models/test-draft"), ("OPTIONS", "/api/studio/models")]:
        assert client.request(method, path, headers=headers).status_code == 403


def test_same_origin_and_opt_in_development_origin(client, monkeypatch):
    assert client.get("/api/stats", headers={"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"}).status_code == 200
    monkeypatch.setenv("INKREFERENCE_DEV_ORIGIN", "http://localhost:5173")
    assert client.get("/api/stats", headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert client.get("/api/stats", headers={"Origin": "http://localhost:5174"}).status_code == 403
    monkeypatch.setenv("INKREFERENCE_DEV_ORIGIN", "https://attacker.example")
    assert client.get("/api/stats", headers={"Origin": "https://attacker.example"}).status_code == 403


def test_browser_resources_are_isolated_and_private_responses_not_cached(client):
    response = client.get("/api/studio/models")
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "access-control-allow-origin" not in response.headers
    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404


def test_validation_errors_do_not_echo_secret_or_document_input(client):
    sentinel = "private-never-echo-key-and-source-941"
    response = client.post("/api/studio/jobs", json={"document_id": {"api_key": sentinel}, "profile_id": "x", "version_id": "v"})
    assert response.status_code == 422
    assert sentinel not in response.text
    assert all("input" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", "external.example"])
def test_cli_refuses_nonloopback_binding_before_creating_data(tmp_path, host):
    directory = tmp_path / "must-not-create"
    result = subprocess.run([sys.executable, "-m", "inkreference", "--no-open", "--host", host, "--data-dir", str(directory)], capture_output=True, timeout=10, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 2
    assert not directory.exists()
