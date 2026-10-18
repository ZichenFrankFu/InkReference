"""Fake-window lifecycle checks; no native GUI is launched in these tests."""

from __future__ import annotations

import importlib.util
import json
import logging
from contextlib import closing
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from inkreference import desktop


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self):
        for handler in self.handlers:
            handler()


class FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(closed=Event())
        self.urls = []
        self.html = []

    def load_url(self, url):
        self.urls.append(url)

    def load_html(self, content):
        self.html.append(content)


class FakeWebview:
    def __init__(self, *, close_first=False, renderer_error=False):
        self.window = FakeWindow()
        self.close_first = close_first
        self.renderer_error = renderer_error

    def create_window(self, title, **kwargs):
        self.title = title
        self.creation = kwargs
        return self.window

    def start(self, callback, **kwargs):
        self.start_options = kwargs
        if self.renderer_error:
            raise RuntimeError("WebView2 unavailable")
        if self.close_first:
            self.window.events.closed.emit()
        callback()
        self.window.events.closed.emit()


class FakeServer:
    def __init__(self, *, fail=False, stuck=False):
        self.should_exit = False
        self.fail = fail
        self.stuck = stuck
        self.release = threading.Event()

    def run(self, *, sockets):
        self.sockets = sockets
        self.daemon = threading.current_thread().daemon
        if self.fail:
            raise RuntimeError("database unavailable")
        while not self.release.is_set() and (self.stuck or not self.should_exit):
            time.sleep(0.001)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return json.dumps({"status": "ok"}).encode()


@pytest.fixture
def desktop_fixture(monkeypatch):
    view = FakeWebview()
    monkeypatch.setattr(desktop.importlib, "import_module", lambda _: view)
    monkeypatch.setattr(desktop.urllib.request, "build_opener", lambda *_: SimpleNamespace(open=lambda *_args, **_kwargs: FakeResponse()))
    monkeypatch.setattr(desktop, "STARTUP_TIMEOUT", 0.03)
    monkeypatch.setattr(desktop, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(desktop, "HTTP_TIMEOUT", 0.005)
    monkeypatch.setattr(desktop, "SHUTDOWN_TIMEOUT", 0.1)
    return view


def test_ready_window_loads_url_then_stops_server(desktop_fixture, monkeypatch):
    view = desktop_fixture
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    server = FakeServer()
    listener = object()
    assert desktop.run_desktop(server, listener, "http://127.0.0.1:8000") == 0
    assert server.sockets == [listener]
    assert server.daemon and server.should_exit
    assert view.window.urls == ["http://127.0.0.1:8000"]
    assert 'aria-valuetext="启动中"' in view.creation["html"]
    assert "aria-valuenow" not in view.creation["html"]
    assert view.start_options["gui"] == "edgechromium"


def test_health_timeout_keeps_error_message_and_returns_failure(desktop_fixture, monkeypatch, capsys):
    view = desktop_fixture

    def timeout(*_args, **_kwargs):
        raise TimeoutError("not ready")

    monkeypatch.setattr(desktop.urllib.request, "build_opener", lambda *_: SimpleNamespace(open=timeout))
    server = FakeServer()
    assert desktop.run_desktop(server, object(), "http://127.0.0.1:8000") == 1
    assert not view.window.urls and server.should_exit
    assert "启动超时" in view.window.html[-1]
    assert "open.ps1" in capsys.readouterr().err if desktop.sys.platform == "win32" else "open.sh" in capsys.readouterr().err


def test_server_failure_is_not_reported_as_gui_success(desktop_fixture, capsys):
    server = FakeServer(fail=True)
    assert desktop.run_desktop(server, object(), "http://127.0.0.1:8000") == 1
    assert "database unavailable" in capsys.readouterr().err
    assert not desktop_fixture.window.urls


def test_missing_optional_webview_does_not_start_server(monkeypatch, capsys):
    def missing(_):
        raise ImportError("webview")

    monkeypatch.setattr(desktop.importlib, "import_module", missing)
    server = FakeServer()
    assert desktop.run_desktop(server, object(), "http://127.0.0.1:8000") == 2
    assert not hasattr(server, "sockets")
    assert "Browser" in capsys.readouterr().err if desktop.sys.platform == "win32" else "--browser" in capsys.readouterr().err


def test_renderer_exception_shuts_down_server(desktop_fixture, capsys):
    desktop_fixture.renderer_error = True
    server = FakeServer()
    assert desktop.run_desktop(server, object(), "http://127.0.0.1:8000") == 1
    assert server.should_exit
    assert "WebView2" in capsys.readouterr().err


def test_close_loading_window_cancels_without_navigation(desktop_fixture):
    desktop_fixture.close_first = True
    server = FakeServer()
    assert desktop.run_desktop(server, object(), "http://127.0.0.1:8000") == 0
    assert server.should_exit and not desktop_fixture.window.urls


def test_shutdown_has_a_deadline(desktop_fixture, monkeypatch, capsys):
    monkeypatch.setattr(desktop, "SHUTDOWN_TIMEOUT", 0.005)
    server = FakeServer(stuck=True)
    try:
        assert desktop.run_desktop(server, object(), "http://127.0.0.1:8000") == 1
        assert "关闭期限" in capsys.readouterr().err
    finally:
        server.release.set()


def test_desktop_rejects_non_main_thread(desktop_fixture):
    results = []
    worker = threading.Thread(target=lambda: results.append(desktop.run_desktop(FakeServer(), object(), "http://127.0.0.1:8000")))
    worker.start()
    worker.join(timeout=1)
    assert results == [2]


def test_packaged_entry_defaults_to_desktop_and_preserves_headless(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "packaging" / "desktop_entry.py"
    spec = importlib.util.spec_from_file_location("inkreference_packaged_entry", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    from inkreference import __main__
    calls = []
    monkeypatch.setattr(__main__, "main", lambda argv: calls.append(argv) or 0)
    assert entry.main([]) == 0
    assert calls[-1] == ["--desktop"]
    assert entry.main(["--no-open", "--port", "18767"]) == 0
    assert calls[-1] == ["--no-open", "--port", "18767"]


def test_windowed_entry_provides_streams_for_uvicorn(monkeypatch, tmp_path):
    from uvicorn.logging import DefaultFormatter

    path = Path(__file__).resolve().parents[1] / "packaging" / "desktop_entry.py"
    spec = importlib.util.spec_from_file_location("inkreference_windowed_entry", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    data_dir = tmp_path / "custom app data"
    with monkeypatch.context() as context:
        context.setattr(entry.sys, "stdout", None)
        context.setattr(entry.sys, "stderr", None)
        entry._ensure_streams(["--data-dir", str(data_dir)])
        stream = entry.sys.stderr
        assert DefaultFormatter().format(logging.LogRecord("uvicorn", logging.INFO, __file__, 1, "ready", (), None))
        print("startup diagnostic", file=stream, flush=True)
    stream.close()
    assert "startup diagnostic" in (data_dir / "logs" / "desktop.log").read_text(encoding="utf-8")


def test_frozen_failure_hint_uses_the_executable_and_current_port(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", r"C:\Program Files\InkReference\InkReference.exe")
    message = desktop._error_html("WebView2 unavailable", "http://127.0.0.1:18769")
    assert "open.ps1" not in message
    assert "InkReference.exe" in message and "--no-open --port 18769" in message


def test_macos_uses_native_cocoa_renderer_on_main_thread(desktop_fixture, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    assert desktop.run_desktop(FakeServer(), object(), "http://127.0.0.1:8000") == 0
    assert desktop_fixture.start_options["gui"] == "cocoa"
    assert desktop_fixture.window.urls == ["http://127.0.0.1:8000"]


def test_macos_errors_and_browser_fallback_are_platform_specific(desktop_fixture, monkeypatch, capsys):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    desktop_fixture.renderer_error = True
    assert desktop.run_desktop(FakeServer(), object(), "http://127.0.0.1:8000") == 1
    message = capsys.readouterr().err
    assert "WebKit" in message and "./scripts/open.sh --browser" in message
    assert "PowerShell" not in message and "Windows 需要" not in message


def test_macos_frozen_browser_hint_quotes_bundle_executable(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", "/Applications/Ink Reference.app/Contents/MacOS/InkReference")
    hint = desktop._browser_hint("http://127.0.0.1:18769")
    assert hint.startswith("'/Applications/Ink Reference.app/Contents/MacOS/InkReference' --no-open")
    assert "--port 18769" in hint and "& '" not in hint


def test_default_data_and_log_paths_use_macos_locations_and_legacy_workspace(monkeypatch, tmp_path):
    from inkreference.api import default_data_dir

    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("INKREFERENCE_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert default_data_dir() == tmp_path / "Library" / "Application Support" / "InkReference"
    assert desktop.default_log_dir() == tmp_path / "Library" / "Logs" / "InkReference"
    legacy = tmp_path / ".local" / "share" / "InkReference"
    legacy.mkdir(parents=True)
    (legacy / "inkreference.sqlite3").write_bytes(b"invalid database")
    assert default_data_dir() == tmp_path / "Library" / "Application Support" / "InkReference"
    import sqlite3
    (legacy / "inkreference.sqlite3").unlink()
    with closing(sqlite3.connect(legacy / "inkreference.sqlite3")) as connection:
        with connection:
            connection.execute("CREATE TABLE fixture (id INTEGER)")
    assert default_data_dir() == legacy
    override = tmp_path / "chosen workspace"
    monkeypatch.setenv("INKREFERENCE_DATA_DIR", str(override))
    assert default_data_dir() == override
    assert desktop.default_log_dir() == override / "logs"
