"""Real source/frozen HTTP smoke, with portable process-tree cleanup."""
from __future__ import annotations

import argparse
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--require-vault", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        command = [sys.executable, "scripts/manage.py", "open", "--browser", "--no-open", "--port", str(args.port)]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{args.port}"

    def request(path: str, method: str = "GET", body=None):
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if raw is not None else {}
        with opener.open(urllib.request.Request(base + path, data=raw, headers=headers, method=method), timeout=5) as response:
            return json.load(response)

    with tempfile.TemporaryDirectory(prefix="InkReference 中文 smoke ") as temporary:
        directory = Path(temporary)
        log_path = directory / "server.log"
        environment = {**os.environ, "INKREFERENCE_DATA_DIR": str(directory / "application data"), "PYTHONIOENCODING": "utf-8"}
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1], env=environment, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                                       start_new_session=os.name != "nt")
            try:
                deadline = time.monotonic() + 60
                while True:
                    try:
                        if request("/api/health")["status"] == "ok":
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("The application did not become ready.")
                    time.sleep(0.15)
                with opener.open(base + "/", timeout=5) as page:
                    if page.status != 200 or "InkReference" not in page.read().decode("utf-8"):
                        raise RuntimeError("The application did not serve its frontend.")
                if len(request("/api/meta")["tables"]) != 3:
                    raise RuntimeError("The application did not initialize its library.")
                work = request("/api/records/works", "POST", {"values": {
                    "作品ID": "SMOKE-W", "作品": "合成启动作品", "类别": "合成类别", "用户是否完整体验过": "否",
                }})
                filtered = request("/api/records/works?category=" + urllib.parse.quote("合成类别") + "&seen=" + urllib.parse.quote("否"))
                assert filtered["total"] == 1 and filtered["items"][0]["id"] == work["id"]
                # Exercise lazily imported interchange packages in the frozen
                # application, not just the Python build environment.
                for format in ("db", "csv", "xlsx"):
                    with opener.open(base + f"/api/library/export?format={format}", timeout=15) as response:
                        raw = response.read()
                    if format == "db":
                        assert raw.startswith(b"SQLite format 3\x00")
                    else:
                        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                            expected = "works.csv" if format == "csv" else "xl/workbook.xml"
                            assert expected in archive.namelist()
                boundary = uuid.uuid4().hex
                source = "第一章 合成测试\n正文尾标。\n作者说：求月票！".encode("utf-8")
                multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="smoke.txt"\r\n'
                             'Content-Type: text/plain\r\n\r\n').encode() + source + f"\r\n--{boundary}--\r\n".encode()
                with opener.open(urllib.request.Request(base + "/api/studio/ingestions", data=multipart,
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST"), timeout=15) as response:
                    upload = json.load(response)
                deadline = time.monotonic() + 20
                while upload["status"] in {"queued", "running"} and time.monotonic() < deadline:
                    time.sleep(.1)
                    upload = request(f'/api/studio/ingestions/{upload["id"]}')
                assert upload["status"] == "completed", upload
                version = request(f'/api/studio/documents/{upload["document_id"]}/versions/{upload["version_id"]}')
                assert version["text"] == source.decode("utf-8")
                findings = request(f'/api/studio/documents/{upload["document_id"]}/versions/{upload["version_id"]}/findings')
                assert any(item["kind"] == "author_note" for item in findings["items"])
                link = request(f'/api/studio/documents/{upload["document_id"]}/catalog', "PUT", {
                    "expected_work_id": None, "expected_version_id": upload["version_id"],
                    "work_id": work["id"], "complete": True,
                })
                assert link["work_id"] == work["id"] and link["completion"]["version_id"] == upload["version_id"]
                catalog = request(f'/api/studio/catalog/works/{work["id"]}')
                assert catalog["documents"][0]["id"] == upload["document_id"]
                preview = request(f'/api/studio/documents/{upload["document_id"]}/chapters/resegment/preview', "POST", {
                    "base_version_id": upload["version_id"], "heading_formats": ["第N章"], "repair_duplicates": True,
                })
                assert preview["preview_token"] and preview["old"]["chapter_count"] == 1
                repair = request("/api/studio/repair/capabilities")
                assert repair["protocol"] == "inkreference.repair.v1" and repair["can_rebuild_cache"]
                operation = request("/api/studio/repair/cache-rebuild", "POST", {
                    "document_id": upload["document_id"], "version_id": upload["version_id"],
                })
                deadline = time.monotonic() + 20
                while operation["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(.1)
                    operation = request(f'/api/studio/repair/operations/{operation["operation_id"]}')
                assert operation["status"] == "succeeded", operation
                unchanged = request(f'/api/studio/documents/{upload["document_id"]}/versions/{upload["version_id"]}')
                assert unchanged["text"] == version["text"] and unchanged["id"] == version["id"]
                prompt = request("/api/studio/repair/prompt", "POST", {"description": "合成启动检查", "topic": "chapters"})
                assert prompt["session_id"] and "inkreference.repair.v1" in prompt["prompt"]
                assert "正文尾标" not in prompt["prompt"]
                security = request("/api/studio/models/security")
                if args.require_vault:
                    if not security["vault_available"]:
                        raise RuntimeError("The native credential store was not bundled.")
                    profile_id = "synthetic-frozen-smoke"
                    try:
                        profile = request("/api/studio/models", "POST", {
                            "id": profile_id, "name": "Synthetic credential smoke", "provider": "openai_compatible",
                            "connection_mode": "local", "credential_mode": "vault", "base_url": "http://127.0.0.1:1234/v1",
                            "model": "synthetic-no-network", "api_key": uuid.uuid4().hex,
                        })
                        if not profile["has_vault_key"] or not profile["key_available"]:
                            raise RuntimeError("The native credential was not saved.")
                        cleared = request(f"/api/studio/models/{profile_id}/credentials", "DELETE")
                        if cleared["has_vault_key"] or cleared["key_available"]:
                            raise RuntimeError("The native credential was not cleared.")
                    finally:
                        request(f"/api/studio/models/{profile_id}", "DELETE")
                print(json.dumps({"health": "ok", "frontend": "ok", "database": "ok", "library_formats": ["db", "csv", "xlsx"],
                                  "upload_pipeline": "ok", "catalog_link": "ok", "chapter_preview": "ok", "quick_repair": "ok",
                                  "native_vault": security["vault_backend"], "credential_roundtrip": args.require_vault}))
            except Exception:
                log.flush()
                print(log_path.read_text(encoding="utf-8", errors="replace")[-12000:], file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name != "nt":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
