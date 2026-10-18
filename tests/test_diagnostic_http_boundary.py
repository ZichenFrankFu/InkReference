"""Diagnostics capture failures without retaining input or releasing busy workspaces."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.database import DataError
from inkreference.workspace_lock import workspace_lock


def test_unexpected_http_failure_is_traceable_without_body_or_query_leak(tmp_path, monkeypatch):
    application = create_app(tmp_path / "data", tmp_path / "no-dist")
    secret = "UNIQUE_PRIVATE_BODY_AND_KEY_NOT_FOR_DIAGNOSTICS"
    with TestClient(application, base_url="http://127.0.0.1") as client:
        def broken_stats():
            raise RuntimeError(secret)

        monkeypatch.setattr(application.state.runtime.store, "stats", broken_stats)
        response = client.get("/api/stats", params={"private": secret}, headers={"X-Private": secret})
        assert response.status_code == 500
        assert response.json() == {"detail": "程序内部错误。", "repair_available": True}
        assert response.headers["cache-control"] == "no-store"
        assert secret not in response.text
        diagnostic = client.post("/api/studio/repair/prompt", json={"description": "统计页报错", "topic": "library"})
        assert diagnostic.status_code == 200, diagnostic.text
        assert secret not in diagnostic.text
        events = diagnostic.json()["diagnostics"]["requests_and_errors"]
        failure = next(item for item in events if item["kind"] == "exception")
        assert failure["type"] == "RuntimeError"
        assert failure["path"] == "/api/stats"
        assert any(frame["function"] == "broken_stats" for frame in failure["frames"])
        request = next(item for item in events if item["kind"] == "request" and item["status"] == 500)
        assert request["path"] == "/api/stats" and request["duration_ms"] >= 0
        assert application.state.runtime._studio is None


def test_pending_repair_worker_retains_workspace_until_it_stops(tmp_path):
    directory = tmp_path / "data"
    application = create_app(directory, tmp_path / "no-dist")
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    application.state.active_quick_repair_threads = lambda: [worker]
    try:
        with TestClient(application, base_url="http://127.0.0.1") as client:
            assert client.get("/api/health").status_code == 200
        with pytest.raises(DataError) as caught:
            with workspace_lock(directory):
                pass
        assert caught.value.status_code == 409
    finally:
        release.set()
        worker.join(timeout=2)
    deadline = time.monotonic() + 2
    while True:
        try:
            with workspace_lock(directory):
                return
        except DataError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
