"""Optional native window; HTTP serving stays in its own daemon thread.

Lifecycle tests use a fake webview. They do not validate a native renderer.
"""

from __future__ import annotations

import html
import importlib
import json
import os
import shlex
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

STARTUP_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 4.0
HTTP_TIMEOUT = 0.5
POLL_INTERVAL = 0.15
BROWSER_HINT = r".\scripts\open.ps1 -Browser"

LOADING_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>InkReference</title>
<style>html,body{height:100%;margin:0}body{display:grid;place-items:center;background:#f4f2ec;
color:#243934;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei UI',sans-serif}.card{width:min(440px,74vw);
padding:48px;border-radius:24px;background:#fffdfa;box-shadow:0 16px 64px #233c3210}
.mark{display:grid;place-items:center;width:48px;height:48px;border-radius:14px;background:#275d50;
color:white;font:700 28px Georgia,serif}.name{font-size:13px;letter-spacing:2px;color:#668078;margin:24px 0 10px}
h1{font-size:25px;font-weight:600;line-height:1.5;margin:0 0 12px}p{font-size:14px;line-height:1.8;color:#78867e}
.track{overflow:hidden;height:5px;border-radius:4px;background:#e6ece5;margin-top:30px}
.bar{width:35%;height:100%;background:#759576;border-radius:4px;animation:move 1.5s ease-in-out infinite}
@keyframes move{0%{transform:translateX(-110%)}100%{transform:translateX(390%)}}
@media(prefers-reduced-motion:reduce){.bar{animation:none;margin-left:32%}}</style></head>
<body><main class="card"><div class="mark">I</div><div class="name">INKREFERENCE</div>
<h1>正在打开素材库</h1><p>正在启动本地服务</p>
<div class="track" role="progressbar" aria-label="正在启动" aria-valuetext="启动中">
<div class="bar"></div></div></main></body></html>"""


def _browser_hint(url: str | None = None) -> str:
    if getattr(sys, "frozen", False):
        port = urlsplit(url or "http://127.0.0.1:8000").port or 8000
        executable = (
            "& '" + sys.executable.replace("'", "''") + "'"
            if sys.platform == "win32" else shlex.quote(sys.executable)
        )
        return f"{executable} --no-open --port {port}\n然后在浏览器打开 {url or 'http://127.0.0.1:8000'}"
    return BROWSER_HINT if sys.platform == "win32" else "./scripts/open.sh --browser"


def default_log_dir(configured_data_dir: str | None = None) -> Path:
    """Use writable OS locations, outside the installed application bundle."""
    configured = configured_data_dir or os.environ.get("INKREFERENCE_DATA_DIR")
    if configured:
        return Path(configured).expanduser() / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "InkReference"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "InkReference" / "logs"
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return base / "InkReference" / "logs"


def _renderer_hint() -> str:
    if sys.platform == "win32":
        return "请检查 Microsoft Edge WebView2 Runtime。"
    if sys.platform == "darwin":
        return "请检查 macOS WebKit 和桌面依赖。"
    return "请检查桌面渲染依赖。"


def _error_html(message: str, url: str | None = None) -> str:
    return LOADING_HTML.replace(
        "正在打开素材库", "暂时无法打开桌面窗口"
    ).replace(
        "正在启动本地服务",
        html.escape(message) + "<br>关闭窗口后，可使用浏览器入口：<br><code>"
        + html.escape(_browser_hint(url)).replace("\n", "<br>") + "</code>",
    ).replace('class="track"', 'class="track" style="display:none"')


def _write_error(message: str, url: str | None = None) -> None:
    stream = sys.stderr or sys.__stderr__
    if stream is not None:
        print(f"InkReference 桌面启动失败：{message}\n浏览器入口：{_browser_hint(url)}", file=stream, flush=True)


def run_desktop(server, listener, url: str) -> int:
    """Run pywebview on the calling main thread; return nonzero on failure.

    The caller owns the reserved listener and closes it after this returns.
    Closing a loading window is treated as cancellation, not a startup failure.
    """
    if threading.current_thread() is not threading.main_thread():
        _write_error("桌面窗口必须从主线程启动。", url)
        return 2
    try:
        webview = importlib.import_module("webview")
    except ImportError:
        _write_error("缺少 pywebview；请重新安装桌面依赖，或使用浏览器入口。", url)
        return 2

    stop = threading.Event()
    window_closed = threading.Event()
    gui_ready = threading.Event()
    ready = threading.Event()
    server_finished = threading.Event()
    failures: list[str] = []
    failure_lock = threading.Lock()
    worker = None
    window = None

    def record_failure(message: str) -> None:
        with failure_lock:
            if failures:
                return
            failures.append(message)
        _write_error(message, url)

    def display_failure() -> None:
        if window is not None and failures and not window_closed.is_set():
            try:
                window.load_html(_error_html(failures[0], url))
            except Exception:
                # Keep the original error when the renderer is already closing.
                pass

    def on_closed(*_args) -> None:
        window_closed.set()
        stop.set()
        server.should_exit = True

    def serve() -> None:
        try:
            server.run(sockets=[listener])
        except BaseException as exc:
            if not stop.is_set():
                record_failure(f"本地服务无法启动：{exc}")
        finally:
            server_finished.set()
            if not stop.is_set():
                record_failure("本地服务已意外停止，请查看启动日志。")
                if gui_ready.is_set():
                    display_failure()

    def load_when_ready() -> None:
        gui_ready.set()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not stop.is_set() and time.monotonic() < deadline:
            if failures or server_finished.is_set():
                display_failure()
                return
            try:
                with opener.open(url.rstrip("/") + "/api/health", timeout=HTTP_TIMEOUT) as response:
                    healthy = response.status == 200 and json.loads(response.read()).get("status") == "ok"
                if healthy and not stop.is_set():
                    window.load_url(url)
                    ready.set()
                    return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            except Exception as exc:
                record_failure(f"桌面界面无法载入：{exc}")
                display_failure()
                return
            stop.wait(POLL_INTERVAL)
        if not stop.is_set():
            record_failure("本地服务启动超时。可以检查日志后重试，或切换浏览器入口。")
            display_failure()

    try:
        window = webview.create_window(
            "InkReference", html=LOADING_HTML, width=1440, height=920,
            min_size=(1024, 680), background_color="#f4f2ec", text_select=True,
        )
        window.events.closed += on_closed
        worker = threading.Thread(target=serve, name="inkreference-http", daemon=True)
        worker.start()
        kwargs = {"gui": "edgechromium"} if sys.platform == "win32" else {"gui": "cocoa"} if sys.platform == "darwin" else {}
        webview.start(load_when_ready, **kwargs)
        if not ready.is_set() and not window_closed.is_set() and not failures:
            record_failure("桌面界面在就绪前停止。" + _renderer_hint())
    except Exception as exc:
        record_failure(f"原生窗口不可用：{exc}。" + _renderer_hint())
    finally:
        stop.set()
        server.should_exit = True
        if worker is not None:
            worker.join(timeout=SHUTDOWN_TIMEOUT)
            if worker.is_alive():
                record_failure("本地服务未在关闭期限内退出。请检查启动日志。")
    return 1 if failures else 0
