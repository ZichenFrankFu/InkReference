"""Kernel lock tests use real competing Python processes, including termination."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from inkreference.database import DataError
from inkreference.workspace_lock import LOCK_FILENAME, workspace_lock


PROBE = """
import sys
from inkreference.database import DataError
from inkreference.workspace_lock import workspace_lock
try:
    with workspace_lock(sys.argv[1]):
        print('acquired', flush=True)
except DataError as error:
    print(str(error.status_code) + ':' + str(error.detail), flush=True)
    raise SystemExit(9)
"""
HOLDER = """
import sys
from pathlib import Path
from inkreference.workspace_lock import workspace_lock
with workspace_lock(sys.argv[1]):
    Path(sys.argv[2]).write_text('locked', encoding='utf-8')
    sys.stdin.buffer.read(1)
"""


def probe(directory):
    return subprocess.run([sys.executable, "-c", PROBE, str(directory)], capture_output=True, text=True, timeout=10, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_competing_process_is_rejected_then_normal_exit_releases(tmp_path):
    with workspace_lock(tmp_path):
        rejected = probe(tmp_path)
        assert rejected.returncode == 9
        assert "409:" in rejected.stdout
        assert "InkReference" in rejected.stdout
        # A second instance in this process is also independent, not reentrant.
        with pytest.raises(DataError) as duplicate:
            with workspace_lock(tmp_path):
                pytest.fail("same directory acquired twice")
        assert duplicate.value.status_code == 409
        # Another project can be opened without blocking this one.
        assert probe(tmp_path / "other-project").returncode == 0
    assert (tmp_path / LOCK_FILENAME).read_bytes() == b"1"
    assert probe(tmp_path).returncode == 0


def test_kernel_releases_lock_after_process_is_killed(tmp_path):
    ready = tmp_path / "ready.txt"
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(tmp_path), str(ready)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                stdout, stderr = process.communicate(timeout=2)
                pytest.fail(f"lock holder failed: {stdout!r} {stderr!r}")
            time.sleep(0.02)
        assert probe(tmp_path).returncode == 9
        process.kill()
        process.communicate(timeout=5)
        assert (tmp_path / LOCK_FILENAME).exists()
        assert probe(tmp_path).returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_application_exception_releases_handle_without_deleting_lock_file(tmp_path):
    with pytest.raises(RuntimeError):
        with workspace_lock(tmp_path):
            raise RuntimeError("synthetic application shutdown")
    assert (tmp_path / LOCK_FILENAME).is_file()
    assert probe(tmp_path).returncode == 0


def test_exiting_context_keeps_kernel_lock_until_inflight_worker_finishes(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def inflight_request():
        entered.set()
        release.wait(10)
    worker = threading.Thread(target=inflight_request, daemon=True)
    worker.start()
    assert entered.wait(2)
    try:
        with workspace_lock(tmp_path, pending_threads=lambda: [worker]):
            pass
        # The application context already returned; its daemon lock guard
        # prevents a different process from recovering this still-running job.
        assert worker.is_alive()
        assert probe(tmp_path).returncode == 9
    finally:
        release.set()
        worker.join(timeout=2)
    deadline = time.monotonic() + 5
    while True:
        accepted = probe(tmp_path)
        if accepted.returncode == 0:
            break
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert (tmp_path / LOCK_FILENAME).read_bytes() == b"1"


def test_application_retains_workspace_while_upload_worker_is_finishing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from inkreference.api import create_app

    directory = tmp_path / 'workspace'
    application = create_app(data_dir=directory)
    entered, release = threading.Event(), threading.Event()
    worker_store = None
    try:
        with TestClient(application, base_url='http://127.0.0.1') as client:
            studio = application.state.runtime.studio
            worker_store = studio.ingestions
            original_upload = studio.documents.upload
            original_shutdown = worker_store.shutdown

            def delayed_upload(*args, **kwargs):
                entered.set()
                assert release.wait(10)
                return original_upload(*args, **kwargs)

            monkeypatch.setattr(studio.documents, 'upload', delayed_upload)
            monkeypatch.setattr(worker_store, 'shutdown', lambda: original_shutdown(timeout=.01))
            response = client.post('/api/studio/ingestions', files={'file': ('book.txt', '第一章 初遇\n正文。'.encode(), 'text/plain')})
            assert response.status_code == 201, response.text
            assert entered.wait(2)
        assert worker_store.active_threads()
        assert probe(directory).returncode == 9
    finally:
        release.set()
        if worker_store is not None:
            for worker in worker_store.active_threads():
                worker.join(timeout=3)
    deadline = time.monotonic() + 5
    while probe(directory).returncode != 0:
        assert time.monotonic() < deadline
        time.sleep(.02)
