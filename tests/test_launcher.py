"""Exercise the real listener, including clients that leave during accept."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


def test_cli_health_survives_clients_disconnecting_before_http(tmp_path):
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    log_path = tmp_path / "server.log"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    health_url = f"http://127.0.0.1:{port}/api/health"

    def health():
        with opener.open(health_url, timeout=1) as response:
            assert response.status == 200
            assert json.load(response)["status"] == "ok"

    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "inkreference", "--no-open",
             "--port", str(port), "--data-dir", str(tmp_path / "app-data")],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    health()
                    break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("CLI failed to serve health:\n" + log_path.read_text(encoding="utf-8", errors="replace"))
                    time.sleep(0.05)

            # An immediate TCP reset while accept is pending previously could
            # end the Windows Proactor accept loop and close its listener.
            linger = struct.pack("hh" if os.name == "nt" else "ii", 1, 0)
            for number in range(40):
                with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
                if number % 10 == 0:
                    health()
            health()
            assert process.poll() is None
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
