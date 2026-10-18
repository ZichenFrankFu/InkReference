"""Run the local application with ``python -m inkreference``."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 InkReference 本地素材库")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 端口，默认 8000")
    parser.add_argument("--data-dir", help="数据库和导入原文件保存目录")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--desktop", action="store_true", help="打开原生桌面窗口")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 至 65535 之间。")
    if args.data_dir:
        os.environ["INKREFERENCE_DATA_DIR"] = args.data_dir

    # Reserve the actual listener before starting the server. A preflight
    # connect/bind followed by closing it would leave a port-allocation race.
    listener = None
    try:
        addresses = socket.getaddrinfo(args.host, args.port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
        last_error: OSError | None = None
        for family, socktype, proto, _, address in addresses:
            candidate = socket.socket(family, socktype, proto)
            try:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                candidate.bind(address)
                candidate.listen(128)
                candidate.set_inheritable(True)
                listener = candidate
                break
            except OSError as exc:
                candidate.close()
                last_error = exc
        if listener is None:
            raise last_error or OSError("无法创建监听端口")
    except OSError as exc:
        print(f"无法启动 InkReference：{args.host}:{args.port} 无法监听（{exc}）。请关闭占用端口的应用，或使用 --port 指定其他端口。", file=sys.stderr)
        return 1

    visible_host = "127.0.0.1" if args.host == "0.0.0.0" else "::1" if args.host == "::" else args.host
    url_host = f"[{visible_host}]" if ":" in visible_host else visible_host
    url = f"http://{url_host}:{args.port}"
    stop = threading.Event()

    if not args.no_open and not args.desktop:
        def open_when_ready():
            # Disable environment proxy routing for a local health check.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 30
            while not stop.is_set() and time.monotonic() < deadline:
                try:
                    with opener.open(url + "/api/health", timeout=0.5) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except (urllib.error.URLError, OSError):
                    stop.wait(0.2)

        threading.Thread(target=open_when_ready, name="inkreference-browser", daemon=True).start()

    print(f"InkReference: {url}", flush=True)
    try:
        # Windows' Proactor accept loop can close the listener after a client
        # disconnects during AcceptEx (CPython issue #93821 / WinError 64).
        # This local HTTP app does not need asyncio subprocesses. Select the
        # factory explicitly: recent Uvicorn versions bypass loop policies.
        config = uvicorn.Config(
            "inkreference.api:app", host=args.host, port=args.port,
            log_level="info",
            loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
        )
        server = uvicorn.Server(config)
        if args.desktop:
            from .desktop import run_desktop
            return run_desktop(server, listener, url)
        server.run(sockets=[listener])
        return 0 if server.started else 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        listener.close()


if __name__ == "__main__":
    raise SystemExit(main())
