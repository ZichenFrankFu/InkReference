"""Local structured diagnostics with real ASGI, threads, services, and wire fixtures."""
from concurrent.futures import ThreadPoolExecutor
import json
import importlib.util
import os
import re
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.bootstrap_logging import BootstrapLogStream
from inkreference.conversions import ConversionStore
from inkreference.database import DataError
from inkreference.diagnostics import (DiagnosticLog, background_target, diagnostic_context,
    diagnostic_log, logged_operation, recent_error_events, record_event, record_exception, redact)
from inkreference.documents import DocumentStore
from inkreference.embedding_downloads import EmbeddingDownloads
from inkreference.ingestions import IngestionStore
from inkreference.models import ModelService
from inkreference.research_sources import ResearchSources
from inkreference.semantic_search import SemanticIndex
from inkreference.workflows import WorkflowRunner
from pathlib import Path


PRIVATE = "合成的私人正文绝不应进入诊断文件"
SECRET = "sk-synthetic-private-credential-123456"


def logged(tmp_path):
    return [json.loads(line) for path in sorted((tmp_path / "logs").glob("*.jsonl")) for line in path.read_text().splitlines()]


def test_log_is_lazy_and_successful_health_does_not_create_request_noise(tmp_path):
    sink = diagnostic_log(tmp_path)
    assert sink.path == tmp_path / "logs" / "events.jsonl"
    assert not sink.directory.exists()
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
    assert "X-Diagnostic-ID" not in response.headers
    assert not sink.directory.exists()


def test_error_keeps_locations_and_ids_without_args_locals_or_user_text(tmp_path):
    try:
        raise RuntimeError(f"{PRIVATE}; Authorization: Bearer {SECRET}; api_key={SECRET}")
    except RuntimeError as error:
        with diagnostic_context(data_dir=tmp_path, request_id="a" * 32, task_id="task-1", stage="parse"):
            identity = record_exception("ingestion", "parse", error)
            assert record_exception("ingestion", "parse", error) == identity
    events = logged(tmp_path)
    assert len(events) == 1
    event = events[0]
    assert event["request_id"] == "a" * 32 and event["task_id"] == "task-1"
    assert event["stage"] == "parse" and event["error_type"] == "RuntimeError"
    assert event["source"]["file"] == "tests/test_logging_v13.py"
    assert event["source"]["line"] > 0
    assert event["source"]["function"] == "test_error_keeps_locations_and_ids_without_args_locals_or_user_text"
    assert re.fullmatch(r"err-[0-9a-f]{16}", identity)
    assert PRIVATE not in json.dumps(events, ensure_ascii=False) and SECRET not in json.dumps(events)


def test_http_handled_validation_preserves_detail_and_correlates_failures(tmp_path):
    app = create_app(tmp_path)
    @app.get("/api/diagnostic-test/{identifier}")
    def reject(identifier: str):
        raise DataError(409, "版本已更新，请刷新后重试")
    app.router.routes.insert(0, app.router.routes.pop())
    request_id = "b" * 32
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get(f"/api/diagnostic-test/{PRIVATE}", headers={"X-Request-ID": request_id})
    assert response.status_code == 409
    assert response.json() == {"detail": "版本已更新，请刷新后重试"}
    assert response.headers["X-Request-ID"] == request_id
    identity = response.headers["X-Diagnostic-ID"]
    errors = [event for event in logged(tmp_path) if event.get("diagnostic_id") == identity]
    assert any(event.get("source", {}).get("function") == "reject" for event in errors)
    assert any(event.get("route") == "/api/diagnostic-test/{identifier}" for event in errors)
    assert all(event["request_id"] == request_id for event in errors)
    assert PRIVATE not in json.dumps(errors, ensure_ascii=False)


def test_actual_500_has_repair_id_and_does_not_log_request_or_exception_payload(tmp_path):
    app = create_app(tmp_path)
    @app.post("/api/diagnostic-crash")
    def crash():
        raise ValueError(SECRET + PRIVATE)
    app.router.routes.insert(0, app.router.routes.pop())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/diagnostic-crash?api_key=" + SECRET, json={"novel": PRIVATE, "prompt": SECRET}, headers={"Authorization": "Bearer " + SECRET})
    assert response.status_code == 500
    assert response.json() == {"detail": "程序内部错误。", "repair_available": True}
    identity = response.headers["X-Diagnostic-ID"]
    assert re.fullmatch(r"err-[0-9a-f]{16}", identity)
    errors = recent_error_events(tmp_path)
    assert any(event.get("error_type") == "ValueError" and event.get("source", {}).get("function") == "crash" for event in errors)
    assert any(event.get("diagnostic_id") == identity for event in errors)
    serialized = json.dumps(errors, ensure_ascii=False)
    assert PRIVATE not in serialized and SECRET not in serialized
    assert "api_key" not in serialized and "Authorization" not in serialized


def test_pydantic_validation_drops_rejected_input_and_header_injection(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/records/works", params={"limit": SECRET}, headers={"X-Request-ID": "invalid-" + SECRET})
    assert response.status_code == 422
    assert SECRET not in response.text
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
    assert SECRET not in json.dumps(logged(tmp_path))


def test_concurrent_requests_keep_their_own_correlation(tmp_path):
    app = create_app(tmp_path)
    @app.get("/api/concurrent/{item}")
    def fail(item: int):
        raise DataError(409, "请刷新后重试")
    app.router.routes.insert(0, app.router.routes.pop())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        def call(index):
            identity = f"{index:032x}"
            response = client.get(f"/api/concurrent/{index}", headers={"X-Request-ID": identity})
            assert response.status_code == 409
            return identity, response.headers["X-Request-ID"], response.headers["X-Diagnostic-ID"]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(call, range(1, 25)))
    assert all(sent == received for sent, received, _ in results)
    assert len({result[2] for result in results}) == 24
    events = logged(tmp_path)
    for sent, _, diagnostic_id in results:
        matching = [event for event in events if event.get("diagnostic_id") == diagnostic_id]
        assert matching and all(event["request_id"] == sent for event in matching)


def test_background_thread_keeps_request_and_task_context_after_parent_resets(tmp_path):
    ready, release = threading.Event(), threading.Event()
    class Service:
        data_dir = tmp_path
        @logged_operation("test", "worker", task_arg="identity")
        def worker(self, identity):
            ready.set()
            assert release.wait(3)
            try:
                raise RuntimeError(PRIVATE)
            except RuntimeError as error:
                record_exception("test", "worker", error)
    with diagnostic_context(data_dir=tmp_path, request_id="c" * 32):
        worker = threading.Thread(target=background_target(Service().worker), args=("worker-1",))
        worker.start()
        assert ready.wait(3)
    release.set(); worker.join(3)
    error = next(event for event in logged(tmp_path) if event.get("error_type"))
    assert error["request_id"] == "c" * 32 and error["task_id"] == "worker-1"
    before = logged(tmp_path)
    record_event("test", "outside")
    assert logged(tmp_path) == before


def test_concurrent_rotation_is_bounded_valid_json_and_keeps_latest(tmp_path):
    sink = DiagnosticLog(tmp_path, max_bytes=4096, backups=2)
    def write(index):
        sink.write({"level": "ERROR", "diagnostic_id": f"err-{index:016x}", "padding": "x" * 180})
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(200)))
    sink.write({"level": "ERROR", "diagnostic_id": "err-ffffffffffffffff"})
    files = list(sink.directory.glob("*.jsonl"))
    assert len(files) == 3 and all(path.stat().st_size <= 4096 for path in files)
    assert sink.recent_errors(1)[0]["diagnostic_id"] == "err-ffffffffffffffff"
    for path in files:
        assert all(isinstance(json.loads(line), dict) for line in path.read_text().splitlines())


def test_retention_prunes_old_files_and_diagnostic_reads_tolerate_partial_line(tmp_path):
    sink = DiagnosticLog(tmp_path, max_bytes=4096, backups=2, retention_days=14)
    sink.write({"level": "ERROR", "diagnostic_id": "old"})
    old = time.time() - 15 * 86400
    os.utime(sink.path, (old, old))
    sink.write({"level": "ERROR", "diagnostic_id": "new"})
    with sink.path.open("a") as stream:
        stream.write('{"incomplete":')
    assert [item["diagnostic_id"] for item in sink.recent_errors()] == ["new"]


def test_bootstrap_stream_rotates_redacts_fragmented_credentials_and_is_utf8(tmp_path):
    stream = BootstrapLogStream(tmp_path / "logs", max_bytes=4096, backups=2)
    assert stream.encoding == "utf-8" and stream.writable() and not stream.isatty()
    stream.write("Authorization: Bearer ")
    stream.write(SECRET + "\n")
    stream.flush()
    assert SECRET not in (tmp_path / "logs" / "desktop.log").read_text()
    for index in range(150):
        stream.write(f"启动进度 {index} " + "x" * 100 + "\n")
    stream.write("startup-final\n")
    stream.close()
    files = list((tmp_path / "logs").glob("desktop.log*"))
    assert len(files) == 3 and all(path.stat().st_size <= 4096 for path in files)
    assert "startup-final" in (tmp_path / "logs" / "desktop.log").read_text()
    with pytest.raises(ValueError):
        stream.write("closed")


def test_bootstrap_partial_lines_and_handler_failure_stay_bounded(tmp_path, monkeypatch):
    stream = BootstrapLogStream(tmp_path / "logs", max_bytes=4096)
    stream.write("x" * 100_000)
    assert len(stream.buffered) < 4096
    monkeypatch.setattr(stream.handler, "_open", lambda: (_ for _ in ()).throw(OSError("disk full")))
    stream.handler.close()
    stream.write("failed write\n")
    stream.close()


def test_packaged_log_directory_honors_equals_and_last_cli_override(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "packaging" / "desktop_entry.py"
    spec = importlib.util.spec_from_file_location("logging_v13_packaged_entry", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    chosen = tmp_path / "final workspace"
    with monkeypatch.context() as context:
        context.setattr(entry.sys, "stdout", None)
        context.setattr(entry.sys, "stderr", None)
        entry._ensure_streams(["--data-dir", str(tmp_path / "first"), "--data-dir=" + str(chosen)])
        stream = entry.sys.stderr
        stream.write("CLI log directory\n")
    stream.close()
    assert (chosen / "logs" / "desktop.log").exists()
    assert not (tmp_path / "first").exists()


def test_logging_failure_never_breaks_application_operation(tmp_path):
    (tmp_path / "logs").write_text("not a directory")
    with diagnostic_context(data_dir=tmp_path):
        identity = record_exception("test", "write", RuntimeError(PRIVATE))
    assert identity.startswith("err-")
    assert recent_error_events(tmp_path) == []


def test_explicit_redaction_hides_keys_urls_and_user_home():
    value = redact(f"Authorization: Bearer {SECRET}; password='abc'; https://me:pass@example.test/a?token=abc /Users/frank/private")
    assert SECRET not in value and "'abc'" not in value and "?token" not in value
    assert "me:pass" not in value and "/Users/frank" not in value


def test_real_model_timeout_preserves_ledger_and_logs_transport_frame_only(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout(PRIVATE + SECRET, request=request)
    models = ModelService(tmp_path, transport=httpx.MockTransport(handler))
    profile = models.save_profile({"provider": "openai_compatible", "model": "synthetic", "base_url": "http://127.0.0.1:1234/v1", "api_key": SECRET})
    with pytest.raises(DataError) as caught:
        models.invoke(profile["id"], [{"role": "user", "content": PRIVATE}], run_id="timeout-task", chunk_id="chunk-1")
    assert caught.value.status_code == 504
    assert models.list_usage("timeout-task")[0]["status"] == "error"
    events = recent_error_events(tmp_path)
    assert any(event["component"] == "models" and event["stage"] == "timeout" and event["task_id"] == "timeout-task" for event in events)
    assert any(any(frame["file"] == "backend/inkreference/models.py" for frame in event.get("frames", [])) for event in events)
    assert PRIVATE not in json.dumps(events, ensure_ascii=False) and SECRET not in json.dumps(events)


@pytest.mark.parametrize("provider_status", [401, 503])
def test_provider_http_reason_is_preserved_without_response_content(tmp_path, provider_status):
    models = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(provider_status, text=PRIVATE + SECRET)))
    profile = models.save_profile({"provider": "openai_compatible", "model": "synthetic", "base_url": "http://127.0.0.1:1234/v1"})
    with pytest.raises(DataError):
        models.test_profile(profile["id"])
    events = recent_error_events(tmp_path)
    assert any(event.get("http_status") == provider_status and event["status"] == f"provider_http_{provider_status}" for event in events)
    assert PRIVATE not in json.dumps(events, ensure_ascii=False) and SECRET not in json.dumps(events)


def test_ingestion_failure_is_locatable_after_worker_swallows_exception(tmp_path):
    documents, conversions = DocumentStore(tmp_path), ConversionStore(tmp_path)
    store = IngestionStore(tmp_path, documents, conversions)
    job = store.create("synthetic.txt", b"\xff\xfe\x00", encoding="utf-8")
    with diagnostic_context(data_dir=tmp_path, request_id="d" * 32):
        store.start(job["id"])
    for thread in store.active_threads():
        thread.join(5)
    assert store.get(job["id"])["status"] == "failed"
    errors = recent_error_events(tmp_path)
    event = next(event for event in errors if event["component"] == "ingestion" and event.get("error_type"))
    assert event["task_id"] == job["id"] and event["request_id"] == "d" * 32
    assert any(frame["file"] == "backend/inkreference/ingestions.py" for frame in event["frames"])


def test_conversion_failure_is_locatable_without_binary_or_filename_contents(tmp_path):
    store = ConversionStore(tmp_path)
    job = store.create("synthetic.epub", b"not-a-zip", {})
    store.start(job["id"])
    for thread in store.active_threads():
        thread.join(5)
    assert store.get(job["id"])["status"] == "failed"
    assert any(event["component"] == "conversion" and event["task_id"] == job["id"] and event.get("frames") for event in recent_error_events(tmp_path))


def test_embedding_failure_keeps_download_task_id_without_raw_provider_errors(tmp_path):
    models = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=(json.dumps({"error": PRIVATE + SECRET}) + "\n").encode())))
    service = EmbeddingDownloads(tmp_path, models)
    target = service.target("synthetic-embedding")
    job = service.create("synthetic-embedding", expected_target_fingerprint=target["fingerprint"], start=False)
    assert service.execute(job["id"])["status"] == "failed"
    events = recent_error_events(tmp_path)
    assert any(event["component"] == "embedding" and event["task_id"] == job["id"] and event.get("frames") for event in events)
    assert PRIVATE not in json.dumps(events, ensure_ascii=False) and SECRET not in json.dumps(events)


def test_workflow_failure_is_logged_with_original_task_context(tmp_path, monkeypatch):
    documents, models = DocumentStore(tmp_path), ModelService(tmp_path)
    doc = documents.upload("synthetic.txt", "第1章 起程\n合成正文。".encode())
    profile = models.save_profile({"provider": "openai_compatible", "model": "synthetic", "base_url": "http://127.0.0.1:1234/v1", "context_window": 160000, "max_output_tokens": 16000})
    runner = WorkflowRunner(tmp_path, documents=documents, models=models)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    def fail_plan(run):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(runner, "_audit_plan", fail_plan)
    runner.start(run["id"])
    result = runner.wait(run["id"], 5)
    assert result["status"] == "failed"
    event = next(event for event in recent_error_events(tmp_path) if event["component"] == "extraction" and event.get("error_type"))
    assert event["task_id"] == run["id"]
    assert any(frame["file"] == "backend/inkreference/workflows.py" for frame in event["frames"])
    assert PRIVATE not in json.dumps(logged(tmp_path), ensure_ascii=False)


def test_fixed_schema_failure_records_exact_stage_after_successful_model_call(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": '{"schema_version":4}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 3}}))
    documents, models = DocumentStore(tmp_path), ModelService(tmp_path, transport=transport)
    doc = documents.upload("synthetic.txt", "第1章 起程\n合成正文。".encode())
    profile = models.save_profile({"provider": "openai_compatible", "model": "synthetic", "base_url": "http://127.0.0.1:1234/v1", "context_window": 160000, "max_output_tokens": 16000})
    runner = WorkflowRunner(tmp_path, documents=documents, models=models)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    result = runner.execute(run["id"])
    assert result["status"] in {"paused", "failed"}
    assert models.usage_summary(run["id"])["successful_calls"] > 0
    assert any(event["operation"] == "validate_fixed_output" and event["task_id"] == run["id"] and event["stage"] == "plot" for event in recent_error_events(tmp_path))


@pytest.mark.parametrize("timeout", [False, True])
def test_web_search_failure_preserves_parent_task_and_provider_reason(tmp_path, timeout):
    def handler(request):
        if timeout:
            raise httpx.ReadTimeout(PRIVATE + SECRET, request=request)
        return httpx.Response(401, text=PRIVATE + SECRET)
    models = ModelService(tmp_path)
    sources = ResearchSources(models, transport=httpx.MockTransport(handler))
    sources.save_settings({"provider": "tavily", "endpoint": "https://search.example.test/search", "credential_mode": "session", "api_key": SECRET})
    with diagnostic_context(data_dir=tmp_path, request_id="e" * 32, task_id="research-task"):
        with pytest.raises(DataError):
            sources.search(PRIVATE, sources._config()[0])
    events = recent_error_events(tmp_path)
    assert all(event["task_id"] == "research-task" and event["request_id"] == "e" * 32 for event in events)
    assert any(event.get("error_type") == "ReadTimeout" for event in events) if timeout else any(event.get("http_status") == 401 for event in events)
    assert PRIVATE not in json.dumps(events, ensure_ascii=False) and SECRET not in json.dumps(events)


@pytest.mark.parametrize("timeout", [False, True])
def test_recoverable_source_fetch_records_warning_without_url_or_body(tmp_path, timeout):
    def handler(request):
        if timeout:
            raise httpx.ReadTimeout(PRIVATE + SECRET, request=request)
        return httpx.Response(404, text=PRIVATE + SECRET)
    models = ModelService(tmp_path)
    resolver = lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    sources = ResearchSources(models, transport=httpx.MockTransport(handler), resolver=resolver)
    with diagnostic_context(data_dir=tmp_path, task_id="research-task"):
        source = sources.fetch({"url": "https://source.example.test/book?api_key=" + SECRET, "title": PRIVATE})
    assert source["fetch_status"] == "failed"
    event = recent_error_events(tmp_path)[0]
    assert event["component"] == "web" and event["operation"] == "fetch" and event["level"] == "WARNING"
    assert event["status"] == "skipped" and event["task_id"] == "research-task" and event.get("source")
    assert event["error_type"] == "ReadTimeout" if timeout else event["http_status"] == 404
    assert PRIVATE not in json.dumps(event, ensure_ascii=False) and SECRET not in json.dumps(event)
    assert "source.example.test" not in json.dumps(event)


def test_semantic_embedding_keeps_native_timeout_and_metering_without_inputs(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout(PRIVATE + SECRET, request=request)
    models = ModelService(tmp_path, transport=httpx.MockTransport(handler))
    profile = models.save_profile({"provider": "openai_compatible", "model": "synthetic", "base_url": "http://127.0.0.1:1234/v1", "api_key": SECRET})
    index = SemanticIndex(models)
    with pytest.raises(DataError):
        index.embed([PRIVATE], models.snapshot(profile["id"]), model=None, run_id="semantic-task")
    assert models.usage_summary("semantic-task")["failed_calls"] == 1
    events = recent_error_events(tmp_path)
    assert any(event["error_type"] == "ReadTimeout" and event["task_id"] == "semantic-task" and event["component"] == "embedding" for event in events)
    assert PRIVATE not in json.dumps(events, ensure_ascii=False) and SECRET not in json.dumps(events)
