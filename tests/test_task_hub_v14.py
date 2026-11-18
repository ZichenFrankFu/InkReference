"""Task metadata and recovery use local SQLite and synthetic transports only."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inkreference.database import DataError
from inkreference.task_hub import TaskHub
from inkreference.task_routes import install_task_routes
from test_workflow_v12 import setup


def test_listing_does_not_initialize_lazy_services_and_is_bounded(tmp_path):
    class Runtime:
        data_dir = tmp_path
        @property
        def studio(self):
            raise AssertionError("A metadata query constructed Studio")
    hub = TaskHub(Runtime())
    with sqlite3.connect(hub.path) as db:
        db.execute("CREATE TABLE research_jobs(id TEXT PRIMARY KEY,status TEXT,stage TEXT,payload_json TEXT,created_at TEXT,updated_at TEXT)")
        db.execute("CREATE TABLE prompt_preparations(id TEXT PRIMARY KEY,operation TEXT,payload_json TEXT,status TEXT,created_at REAL,updated_at REAL,summary_json TEXT)")
        for index in range(125):
            db.execute("INSERT INTO research_jobs VALUES (?,?,?,?,?,?)", (f"research-{index:03}", "paused" if index % 2 else "needs_review", "review", json.dumps({"title": "合成检索", "prompt": "PRIVATE_PROMPT" * 10000, "draft": {"content": "PRIVATE_DRAFT" * 1000}, "profiles": {"model": {"id": f"model-{index % 2}"}}, "progress": {"completed": 2, "total": 4}}), "2026-01-01", f"2026-01-{index % 28 + 1:02}"))
        db.execute("INSERT INTO prompt_preparations VALUES ('prompt-1','volumes',?,'prepared',1,1,?)", (json.dumps({"prompt": "PRIVATE_BODY", "raw_reply": "PRIVATE_REPLY"}), json.dumps({"title": "分卷测试", "document_id": "doc-1", "version_id": "version-1", "profile_id": "volume-model", "mode": "automatic", "progress": {"completed": 0, "total": 1}})))
    page = hub.list(kind="research", status="paused", offset=10, limit=7)
    assert page["total"] == 62 and len(page["items"]) == 7
    assert page["counts"] == {"paused": 62, "needs_review": 63}
    assert page["kinds"] == {"prompt_workbench": 1, "research": 125}
    assert {item["profile_id"] for item in page["items"]} == {"model-1"}
    assert all(item["progress"] == .5 for item in page["items"])
    assert "PRIVATE_" not in json.dumps(page)
    assert len(hub.list(limit=100)["items"]) == 100
    assert hub.list(q="research-00", limit=100)["total"] == 10
    assert hub.list(q="%", limit=100)["total"] == 0
    prepared = hub.get("prompt_workbench", "prompt-1")
    assert prepared["navigation"]["prepared_id"] == "prompt-1"
    assert prepared["navigation"]["profile_id"] == "volume-model"
    for params in ({"limit": 101}, {"limit": 0}, {"offset": -1}, {"kind": "bogus"}):
        with pytest.raises(DataError):
            hub.list(**params)


def test_task_reopen_preserves_fixed_stage_source_ledger_and_does_not_generate(tmp_path):
    runner, doc, models, profile, _, _, requests = setup(tmp_path)
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    chunk = run["candidates"][0]["chunk_id"]
    # Preserve a real successful model call before a deliberate local stop.
    runner.start_chunk(run["id"], chunk)
    completed = runner.wait(run["id"], 10)
    # start_chunk runs all remaining stages in a chunk; make a synthetic
    # unfinished later chunk state without altering this successful history.
    with runner._db(write=True) as db:
        db.execute("UPDATE run_jobs SET status='terminated',stop_kind='terminated',cancel_requested=1 WHERE id=?", (run["id"],))
        before_attempts = [tuple(row) for row in db.execute("SELECT * FROM run_attempts WHERE run_id=?", (run["id"],))]
        before_stages = [tuple(row) for row in db.execute("SELECT * FROM run_stage_chunks WHERE run_id=?", (run["id"],))]
    count = len(requests)
    runtime = SimpleNamespace(data_dir=tmp_path, studio=SimpleNamespace(runner=runner))
    hub = TaskHub(runtime)
    response = hub.control("extraction", run["id"], "resume")
    assert response["task"]["status"] == "paused"
    assert response["action"]["action"] == "open_prompt"
    assert response["action"]["profile_id"] == profile["id"]
    assert len(requests) == len(models.list_usage(run["id"])) == count
    assert runner.get_run(run["id"])["snapshots"] == completed["snapshots"]
    with runner._db() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM run_attempts WHERE run_id=?", (run["id"],))] == before_attempts
        assert [tuple(row) for row in db.execute("SELECT * FROM run_stage_chunks WHERE run_id=?", (run["id"],))] == before_stages


def test_durable_pause_override_waits_for_cancel_then_explicit_retry_same_task(tmp_path):
    calls = []
    hub = TaskHub(SimpleNamespace(data_dir=tmp_path))
    with sqlite3.connect(hub.path) as db:
        db.execute("CREATE TABLE embedding_downloads(id TEXT PRIMARY KEY,data_json TEXT)")
        db.execute("INSERT INTO embedding_downloads VALUES ('download-1',?)", (json.dumps({"status": "running", "stage": "下载", "model": "synthetic-embed", "profile_id": "embed-model", "completed": 30, "total": 100, "created_at": "2026-01-01", "updated_at": "2026-01-01"}),))
    def cancel(identifier):
        calls.append(identifier)
    def retry(identifier):
        calls.append("retry:" + identifier)
        with sqlite3.connect(hub.path) as db:
            db.execute("UPDATE embedding_downloads SET data_json=json_set(data_json,'$.status','queued') WHERE id=?", (identifier,))
    hub.runtime.studio = SimpleNamespace(embedding_downloads=SimpleNamespace(cancel=cancel, retry=retry))
    stopped = hub.control("embedding_download", "download-1", "terminate")
    assert stopped["task"]["status"] == "terminating"
    assert not stopped["task"]["capabilities"]["resume"]
    with sqlite3.connect(hub.path) as db:
        db.execute("UPDATE embedding_downloads SET data_json=json_set(data_json,'$.status','cancelled')")
    reopened = TaskHub(hub.runtime)
    assert reopened.get("embedding_download", "download-1")["status"] == "terminated"
    result = reopened.control("embedding_download", "download-1", "resume")
    assert "action" not in result
    assert result["task"]["status"] == "queued"
    assert result["task"]["profile_id"] == "embed-model"
    assert calls == ["download-1", "retry:download-1"]


def test_task_route_http_limits_and_empty_lazy_state(tmp_path):
    app = FastAPI()
    runtime = SimpleNamespace(data_dir=tmp_path, _studio=None)
    install_task_routes(app, runtime)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/studio/tasks?limit=101").status_code == 422
        response = client.get("/api/studio/tasks?limit=15")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert runtime._studio is None


def test_reviewed_prompt_records_actual_model_usage_and_invalidates_default_reuse(tmp_path):
    import copy
    import hashlib
    import httpx
    from inkreference.prompt_workbench import PromptWorkbench
    from test_workflow_v12 import stage_result
    import threading
    from concurrent.futures import ThreadPoolExecutor

    runner, doc, models, profile, _, _, _ = setup(tmp_path)
    other_profile = models.save_profile({"provider": "openai_compatible", "model": "synthetic-reviewed-b", "base_url": "http://127.0.0.1:1234/v1", "context_window": 160000, "max_output_tokens": 16000})
    run = runner.create_run(doc["id"], profile_id=profile["id"])
    chunk_id = run["candidates"][0]["chunk_id"]
    assert run["snapshots"]["scope_signature"]
    studio = SimpleNamespace(models=models, runner=runner)
    workbench = PromptWorkbench(studio)
    studio._prompt_workbench = workbench
    hub = TaskHub(SimpleNamespace(data_dir=tmp_path, studio=studio))
    prepared = workbench.prepare("extraction", {"run_id": run["id"], "chunk_id": chunk_id}, profile_id=other_profile["id"], mode="automatic")
    envelope = copy.deepcopy(prepared["template"])
    envelope["data"] = stage_result("plot", runner.chunk_prompt(run["id"], chunk_id)["text"])
    requests = []
    entered, release = threading.Event(), threading.Event()
    def respond(request):
        requests.append(json.loads(request.content))
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(envelope)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 21, "completion_tokens": 13}})
    models.transport = httpx.MockTransport(respond)
    edited = prepared["prompt"] + "\n保留这次任务的额外要求。"
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(workbench.execute, "extraction", prepared["prepared_id"], edited)
        assert entered.wait(5)
        try:
            hub.control("extraction", run["id"], "pause")
            assert not hub.get("extraction", run["id"])["capabilities"]["resume"]
            with pytest.raises(DataError):
                hub.control("extraction", run["id"], "resume")
        finally:
            release.set()
        output = pending.result(10)
    restored = hub.control("extraction", run["id"], "resume")
    assert restored["action"]["prepared_id"] == prepared["prepared_id"]
    assert restored["action"]["profile_id"] == other_profile["id"]
    assert runner.get_run(run["id"])["revision"] == run["revision"]
    assert len(requests) == 1
    preview = workbench.preview("extraction", prepared["prepared_id"], output["raw_reply"])["preview"]
    provenance = workbench.confirmation_provenance("extraction", prepared["prepared_id"])
    saved = runner.paste_chunk(run["id"], chunk_id, preview["content"], preview["expected_revision"], prompt_review=provenance)
    assert len(requests) == 1 and requests[0]["model"] == "synthetic-reviewed-b"
    assert requests[0]["messages"][-1]["content"] == edited
    assert models.usage_summary(run_id=run["id"])["calls"] == 1
    assert "scope_signature" not in saved["snapshots"]
    review = saved["snapshots"]["prompt_reviews"][0]
    assert review["prepared_id"] == prepared["prepared_id"]
    assert review["profile_id"] == other_profile["id"] and review["model"] == "synthetic-reviewed-b"
    assert review["call_id"] and review["prompt_sha256"] == hashlib.sha256(json.dumps(edited, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert saved["stages"][0]["profile_id"] == other_profile["id"]
    assert saved["stages"][0]["configured_profile_id"] == profile["id"]
    assert saved["stages"][0]["tokens"] == 34
    assert "模型回复" in saved["events"][-1]["message"]
    assert "未调用模型" not in saved["events"][-1]["message"]
    with runner._db() as db:
        assert not db.execute("SELECT 1 FROM run_scope_fingerprints WHERE run_id=?", (run["id"],)).fetchone()
        attempt = db.execute("SELECT call_id,stage FROM run_attempts WHERE run_id=?", (run["id"],)).fetchone()
        assert attempt["call_id"] == review["call_id"] and attempt["stage"] == "plot"
    fresh = runner.create_run(doc["id"], profile_id=profile["id"])
    assert fresh["id"] != run["id"]
    assert len(requests) == 1


def test_metadata_union_covers_each_persisted_domain_and_status_buckets(tmp_path):
    hub = TaskHub(SimpleNamespace(data_dir=tmp_path))
    with sqlite3.connect(hub.path) as db:
        db.execute("CREATE TABLE volume_research_jobs(id TEXT,status TEXT,document_id TEXT,version_id TEXT,payload_json TEXT,updated_at TEXT)")
        db.execute("INSERT INTO volume_research_jobs VALUES ('volume-1','running','doc-1','version-1',?,'2026-01-01')", (json.dumps({"title": "合成分卷", "stage": "fetch", "progress": {"completed": 1, "total": 3}, "profile": {"id": "volume-model"}, "sources": [{"excerpt": "PRIVATE_TEXT"}]}),))
        db.execute("CREATE TABLE repair_generations(id TEXT,status TEXT,payload_json TEXT,summary_json TEXT,updated_at REAL)")
        db.execute("INSERT INTO repair_generations VALUES ('repair-1','needs_review',?,?,1)", (json.dumps({"profile": {"id": "repair-model"}, "reply": "PRIVATE_REPLY"}), json.dumps({"stage": "review", "progress": {"completed": 1, "total": 1}})))
        for name, extra, status in (("ing_jobs", "document_id TEXT,version_id TEXT", "interrupted"), ("conv_jobs", "imported_document_id TEXT", "completed")):
            db.execute(f"CREATE TABLE {name}(id TEXT,filename TEXT,status TEXT,stage TEXT,progress REAL,completed INTEGER,total INTEGER,error TEXT,created_at TEXT,updated_at TEXT,{extra})")
            if name == "ing_jobs":
                db.execute("INSERT INTO ing_jobs VALUES ('ing-1','source.txt',?,'decode',NULL,0,1,NULL,'2026','2026','doc-1','version-1')", (status,))
            else:
                db.execute("INSERT INTO conv_jobs VALUES ('conv-1','source.pdf',?,'completed',1,1,1,NULL,'2026','2026','doc-2')", (status,))
    page = hub.list()
    assert page["total"] == 4
    assert {item["kind"] for item in page["items"]} == {"volume_research", "repair", "ingestion", "conversion"}
    assert "PRIVATE_" not in json.dumps(page)
    assert hub.list(status="active")["items"][0]["id"] == "volume-1"
    assert hub.list(status="review")["items"][0]["id"] == "repair-1"
    assert hub.list(status="failed")["items"][0]["id"] == "ing-1"
    assert hub.list(status="paused")["total"] == 0
    assert hub.list(status="completed")["items"][0]["id"] == "conv-1"
    with pytest.raises(DataError):
        hub.list(status="nonexistent")


def test_integrated_task_route_stays_lazy_before_any_domain_is_opened(tmp_path):
    from inkreference.api import create_app
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/studio/tasks?limit=15")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert app.state.runtime._studio is None


def test_actual_repair_operation_metadata_transitions_and_mirror_failure_is_harmless(tmp_path, monkeypatch):
    from inkreference.quick_repair import QuickRepair
    from inkreference import task_hub
    import threading

    entered, release = threading.Event(), threading.Event()
    def rebuild(document_id, version_id):
        entered.set()
        assert release.wait(10)
        return {"document_id": document_id, "version_id": version_id, "state": "ready"}
    repair = QuickRepair(tmp_path, frozen=True, cache_rebuilder=rebuild)
    hub = TaskHub(SimpleNamespace(data_dir=tmp_path))
    try:
        operation = repair.rebuild_cache("doc-1", "version-1")
        assert entered.wait(5)
        active = hub.get("repair", operation["operation_id"])
        assert active["status"] == "running" and active["operation"] == "repair_cache"
        assert active["document_id"] == "doc-1"
        assert active["capabilities"] == {"pause": False, "resume": False, "terminate": False}
        assert active["navigation"]["action"] == "open"
        release.set()
        for worker in repair.active_threads():
            worker.join(10)
        finished = hub.get("repair", operation["operation_id"])
        assert finished["status"] == "succeeded" and finished["progress"] == 1
        assert not any(field in finished for field in ("files", "checks", "payload_json", "reply", "prompt"))
        # A metadata failure must not change the repair's durable result.
        monkeypatch.setattr(task_hub, "record_external_task", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("metadata unavailable")))
        repair._persist(repair.operations[operation["operation_id"]])
        assert repair.operations[operation["operation_id"]]["status"] == "succeeded"
        assert (repair.backups / operation["operation_id"] / "operation.json").is_file()
    finally:
        release.set()
        repair.shutdown()


def test_repair_metadata_retains_bounded_history_and_all_active_operations(tmp_path):
    from inkreference.task_hub import record_external_task
    for index in range(30):
        assert record_external_task(tmp_path, identifier=f"repair-{index}", operation="repair_source", status="succeeded", progress=100, title="修复", error="x" * 1000)
    assert record_external_task(tmp_path, identifier="repair-active", operation="repair_source", status="running", progress=1)
    page = TaskHub(SimpleNamespace(data_dir=tmp_path)).list(kind="repair", limit=100)
    assert page["total"] == 25
    active = next(item for item in page["items"] if item["id"] == "repair-active")
    assert active["progress"] == .01
    assert max(len(item["error"]) for item in page["items"]) == 600


def test_manual_terminated_run_reopens_same_source_and_continues_filling_without_provider(tmp_path):
    from test_workflow_v12 import stage_result
    runner, doc, models, _, _, _, requests = setup(tmp_path)
    run = runner.create_run(doc["id"], manual=True, interaction_mode="manual")
    runner.terminate(run["id"])
    hub = TaskHub(SimpleNamespace(data_dir=tmp_path, studio=SimpleNamespace(runner=runner)))
    result = hub.control("extraction", run["id"], "resume")
    assert result["action"]["action"] == "open"
    assert result["action"]["mode"] == "manual"
    chunk_id = run["candidates"][0]["chunk_id"]
    prompt = runner.chunk_prompt(run["id"], chunk_id)
    current = runner.get_run(run["id"])
    saved = runner.paste_chunk(run["id"], chunk_id, json.dumps(stage_result("plot", prompt["text"])), current["revision"])
    assert saved["stages"][0]["completed"] == 1
    assert saved["snapshots"]["document"] == run["snapshots"]["document"]
    assert saved["snapshots"]["interaction_mode"] == "manual"
    assert not requests and not models.list_usage(run["id"])


def test_paid_prompt_row_resume_and_direct_row_open_reopen_bound_run_over_http(tmp_path):
    import copy
    import httpx
    from inkreference.api import create_app
    from inkreference.prompt_workbench import get_prompt_workbench
    from test_workflow_v12 import stage_result

    runner, doc, models, profile, _, _, _ = setup(tmp_path)
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        studio = app.state.runtime.studio
        studio._runner, studio._models = runner, models
        workbench = get_prompt_workbench(studio, app.state.quick_repair)
        run = runner.create_run(doc["id"], profile_id=profile["id"])
        chunk_id = run["candidates"][0]["chunk_id"]
        prepared = workbench.prepare("extraction", {"run_id": run["id"], "chunk_id": chunk_id}, profile_id=profile["id"], mode="automatic")
        envelope = copy.deepcopy(prepared["template"])
        envelope["data"] = stage_result("plot", runner.chunk_prompt(run["id"], chunk_id)["text"])
        calls = []
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(envelope)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 21, "completion_tokens": 13}})
        models.transport = httpx.MockTransport(respond)
        output = workbench.execute("extraction", prepared["prepared_id"], prepared["prompt"] + "\n保留收费回复。")
        hub = app.state.task_hub()
        preview_url = "/api/studio/dynamic-functions/extraction/preview"
        preview_body = {"prepared_id": prepared["prepared_id"], "reply": output["raw_reply"]}
        # The task center's prompt-row resume must reopen its parent run too.
        hub.control("extraction", run["id"], "terminate")
        response = client.post(f"/api/studio/tasks/prompt_workbench/{prepared['prepared_id']}/resume")
        assert response.status_code == 200, response.text
        assert response.json()["action"]["prepared_id"] == prepared["prepared_id"]
        assert runner.get_run(run["id"])["status"] == "paused"
        assert client.post(preview_url, json=preview_body).status_code == 200
        # Clicking the row uses the direct prompt control route, without hub.
        hub.control("extraction", run["id"], "terminate")
        response = client.post(f"/api/studio/dynamic-functions/preparations/{prepared['prepared_id']}/control", json={"action": "resume"})
        assert response.status_code == 200, response.text
        assert response.json()["raw_reply"] == output["raw_reply"]
        assert client.post(preview_url, json=preview_body).status_code == 200
        assert runner.get_run(run["id"])["revision"] == run["revision"]
        assert len(calls) == models.usage_summary(run_id=run["id"])["calls"] == 1
        # Stale binding remains a conflict, rather than reopening a changed run.
        hub.control("extraction", run["id"], "terminate")
        with runner._db(write=True) as db:
            db.execute("UPDATE run_jobs SET revision=revision+1 WHERE id=?", (run["id"],))
        response = client.post(f"/api/studio/dynamic-functions/preparations/{prepared['prepared_id']}/control", json={"action": "resume"})
        assert response.status_code == 409, response.text
        assert runner.get_run(run["id"])["status"] == "terminated"
        assert len(calls) == 1
