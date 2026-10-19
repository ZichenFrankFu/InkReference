"""Real SQLite/domain adapters and metered HTTPX provider boundary; no inference."""
from __future__ import annotations

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.chronicle_schema import canonical_stage_schema
from inkreference.database import DataError
from inkreference.prompt_workbench import PromptWorkbench, create_prompt_workbench_router
from inkreference.quick_repair import QuickRepair
from inkreference.workflow_definitions import ADAPTIVE_WORKFLOW


@pytest.fixture
def context(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        studio = app.state.runtime.studio
        document = studio.documents.upload("fixture.txt", "第1章 抵达\n林青抵达旧城。\n第2章 归途\n林青找到归途。".encode(), title="合成小说")
        state = studio.reference.save_section(document["id"], "raw_entries", [{"title": "资料", "content": "Hello world"}], 0)
        profile = {"id": "review-model", "provider": "openai_compatible", "model": "synthetic-review", "base_url": "http://127.0.0.1:12345/v1", "context_window": 64000, "max_output_tokens": 2048}
        studio.models.save_profile(profile)
        calls, config = [], {}

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            if config.get("gate"):
                config["gate"][0].set()
                assert config["gate"][1].wait(10)
            if config.get("error"):
                return httpx.Response(500, json={"error": {"message": "synthetic failure"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": config.get("reply", "{}")}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 123, "completion_tokens": 47}})

        studio.models.transport = httpx.MockTransport(handler)
        workbench = PromptWorkbench(studio, app.state.quick_repair)
        yield {"app": app, "client": client, "studio": studio, "document": document, "state": state, "profile": profile,
               "calls": calls, "config": config, "workbench": workbench}
        if config.get("gate"):
            config["gate"][1].set()


def prepare_translation(c, **kwargs):
    return c["workbench"].prepare("translate", {"document_id": c["document"]["id"], "expected_revision": c["state"]["revision"], "target_language": "简体中文"}, profile_id="review-model", **kwargs)


def test_prepare_no_call_edited_prompt_exact_actual_http_request_and_preview_no_write(context):
    c = context
    prepared = prepare_translation(c, mode="automatic")
    assert not c["calls"]
    reply = copy.deepcopy(prepared["template"])
    reply["data"] = {"text": "你好，世界"}
    c["config"]["reply"] = json.dumps(reply, ensure_ascii=False)
    edited = prepared["prompt"] + "\n保留称呼。\n"
    response = c["workbench"].execute("translate", prepared["prepared_id"], edited)
    assert len(c["calls"]) == 1
    assert c["calls"][0]["messages"][-1]["content"] == edited
    assert response["raw_reply"] == c["config"]["reply"]
    assert c["workbench"].get(prepared["prepared_id"])["status"] == "needs_review"
    result = c["workbench"].preview("translate", prepared["prepared_id"], response["raw_reply"])
    assert result["preview"]["text"] == "你好，世界"
    assert c["studio"].reference.get(c["document"]["id"])["revision"] == c["state"]["revision"]
    assert c["studio"].models.usage_summary(run_id=prepared["prepared_id"])["calls"] == 1
    with pytest.raises(DataError, match="已发送"):
        c["workbench"].execute("translate", prepared["prepared_id"], edited)


def test_prompt_budget_wrong_binding_source_revision_and_model_change_are_pre_send(context):
    c = context
    p = prepare_translation(c)
    with pytest.raises(DataError, match="上下文"):
        c["workbench"].execute("translate", p["prepared_id"], "译" * 100000)
    assert not c["calls"]
    reply = copy.deepcopy(p["template"])
    reply["request_id"] = "different"
    reply["data"] = {"text": "译文"}
    with pytest.raises(DataError) as mismatch:
        c["workbench"].preview("translate", p["prepared_id"], json.dumps(reply))
    assert mismatch.value.status_code == 409
    c["studio"].reference.save_section(c["document"]["id"], "raw_entries", [{"title": "修改", "content": "changed"}], c["state"]["revision"])
    with pytest.raises(DataError, match="已变化"):
        c["workbench"].execute("translate", p["prepared_id"], p["prompt"])
    assert not c["calls"]


def test_changed_target_and_cross_domain_prepared_id_rejected(context):
    c = context
    p = prepare_translation(c)
    with pytest.raises(DataError) as cross:
        c["workbench"].preview("summarize", p["prepared_id"], "{}")
    assert cross.value.status_code == 404
    c["studio"].models.save_profile({**c["profile"], "model": "changed-target"})
    with pytest.raises(DataError, match="模型目标已变化"):
        c["workbench"].execute("translate", p["prepared_id"], p["prompt"])
    assert not c["calls"]


@pytest.mark.parametrize("action,status", [("pause", "paused"), ("terminate", "terminated")])
def test_stop_while_sending_retains_paid_reply_resume_never_invokes(context, action, status):
    c = context
    p = prepare_translation(c)
    reply = copy.deepcopy(p["template"])
    reply["data"] = {"text": "保留的回复"}
    c["config"]["reply"] = json.dumps(reply)
    arrived, release = threading.Event(), threading.Event()
    c["config"]["gate"] = (arrived, release)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(c["workbench"].execute, "translate", p["prepared_id"], p["prompt"])
        assert arrived.wait(5)
        c["workbench"].control_preparation(p["prepared_id"], action)
        with pytest.raises(DataError, match="仍在进行"):
            c["workbench"].control_preparation(p["prepared_id"], "resume")
        release.set()
        pending.result(10)
    restored = PromptWorkbench(c["studio"], c["app"].state.quick_repair)
    detail = restored.get(p["prepared_id"])
    assert detail["status"] == status and detail["raw_reply"] == c["config"]["reply"]
    with pytest.raises(DataError, match="恢复"):
        restored.preview("translate", p["prepared_id"], detail["raw_reply"])
    detail = restored.control_preparation(p["prepared_id"], "resume")
    assert detail["status"] == "needs_review" and len(c["calls"]) == 1
    assert restored.preview("translate", p["prepared_id"], detail["raw_reply"])["preview"]["text"] == "保留的回复"


def _empty(schema):
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        if "null" in kind:
            return None
        kind = kind[0]
    if kind == "object":
        return {key: _empty(schema["properties"][key]) for key in schema.get("required", [])}
    if kind == "array":
        return []
    if kind in {"integer", "number"}:
        return schema.get("minimum", 0)
    return "x" * schema.get("minLength", 0)


@pytest.mark.parametrize("version", ["2.0.0", "3.0.0", "4.0.0"])
def test_extraction_frozen_schema_source_stage_binding_and_explicit_paste(context, version):
    c = context
    runner = c["studio"].runner
    run = runner.create_run(c["document"]["id"], workflow_id="default", workflow_version=version, profile_id="review-model", reuse_existing=False)
    chunk_id = run["candidates"][0]["chunk_id"]
    p = c["workbench"].prepare("extraction", {"run_id": run["id"], "chunk_id": chunk_id, "expected_revision": run["revision"]}, profile_id="review-model")
    assert not c["calls"]
    reply = copy.deepcopy(p["template"])
    reply["data"] = _empty(canonical_stage_schema(p["binding"]["stage"], version=int(version[0])))
    changed = copy.deepcopy(reply)
    changed["binding"]["source_version_id"] = "other-source"
    with pytest.raises(DataError) as wrong:
        c["workbench"].preview("extraction", p["prepared_id"], json.dumps(changed))
    assert wrong.value.status_code == 409
    preview = c["workbench"].preview("extraction", p["prepared_id"], json.dumps(reply))["preview"]
    assert runner.get_run(run["id"])["revision"] == run["revision"]
    c["workbench"].validate_for_confirmation("extraction", p["prepared_id"], run_id=run["id"], chunk_id=chunk_id)
    saved = runner.paste_chunk(run["id"], chunk_id, preview["content"], preview["expected_revision"])
    assert saved["revision"] > run["revision"] and not c["calls"]
    with pytest.raises(DataError, match="阶段或原文已变化"):
        c["workbench"].preview("extraction", p["prepared_id"], json.dumps(reply))


def test_router_prepare_execute_preview_are_separate(context):
    c = context
    # Install on a small independent app to exercise request validation without
    # relying on the main application's integration order.
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(create_prompt_workbench_router(lambda: c["studio"], c["app"].state.quick_repair))
    with TestClient(app) as client:
        prepared = client.post("/api/studio/dynamic-functions/translate/prepare", json={"payload": {"document_id": c["document"]["id"], "expected_revision": c["state"]["revision"]}, "profile_id": "review-model"})
        assert prepared.status_code == 200, prepared.text
        p = prepared.json()
        assert not c["calls"]
        reply = copy.deepcopy(p["template"])
        reply["data"] = {"text": "已译"}
        c["config"]["reply"] = json.dumps(reply)
        edited = p["prompt"] + "\n保持标点。"
        executed = client.post("/api/studio/dynamic-functions/translate/execute", json={"prepared_id": p["prepared_id"], "prompt_override": edited, "model_profile_id": "review-model"})
        assert executed.status_code == 200, executed.text
        assert "preview" not in executed.json()
        preview = client.post("/api/studio/dynamic-functions/translate/preview", json={"prepared_id": p["prepared_id"], "reply": executed.json()["raw_reply"]})
        assert preview.status_code == 200 and preview.json()["preview"]["text"] == "已译"
        assert client.get("/api/studio/dynamic-functions/prepared/" + p["prepared_id"]).json()["prompt"] == edited


def test_summary_preserves_event_identity_source_evidence_and_dates(context):
    c = context
    event = {"id": "event-arrive", "name": "抵达", "theme": "旅途", "description": "抵达旧城", "narrative_progress": 50,
             "story_time": "1349年7月", "participants": [], "setting_ids": [], "causes": [], "results": [], "plotline_ids": [], "volume": None,
             "source_version_id": c["document"]["latest_version_id"], "revealed_at": {"chapter": 1, "offset": None}, "evidence": "林青抵达旧城。", "origin": "source"}
    state = c["studio"].reference.save_section(c["document"]["id"], "plot", {"schema_version": 4, "logline": "抵达", "events": [event], "plotlines": []}, c["state"]["revision"])
    p = c["workbench"].prepare("summarize", {"document_id": c["document"]["id"], "expected_revision": state["revision"]})
    reply = copy.deepcopy(p["template"])
    reply["data"]["events"][0]["story_time"] = "1350年"
    with pytest.raises(DataError, match="时间、定位、来源或关联"):
        c["workbench"].preview("summarize", p["prepared_id"], json.dumps(reply))
    reply = copy.deepcopy(p["template"])
    reply["data"]["logline"] = "旅人抵达"
    assert c["workbench"].preview("summarize", p["prepared_id"], json.dumps(reply))["preview"]["data"]["logline"] == "旅人抵达"
    assert not c["calls"]


def test_catalog_preview_has_matching_job_binding_and_never_schedules_models(context):
    c = context
    p = c["workbench"].prepare("catalog", {"title": "合成小说", "author": "测试作者"})
    source = {"id": "web_1", "url": "https://fixture.example/novel", "title": "合成小说 测试作者", "excerpt": "合成小说，作者测试作者，旅人前往旧城。"}
    draft = {"identity": {"confirmed": True, "alternatives": [], "notes": "作品与作者匹配"},
             "work": {"title": "合成小说", "author": "测试作者", "category": "中文小说", "publication_date": "", "synopsis": "旅人前往旧城", "discussion_heat": None, "market_scale": None, "source_ids": ["web_1"]},
             "characters": [], "cliches": [], "warnings": []}
    reply = {"protocol": "inkreference.catalog.v1", "request_id": p["binding"]["request_id"], "sources": [source], "draft": draft, "suggested_matches": {}}
    result = c["workbench"].preview("catalog", p["prepared_id"], json.dumps(reply))["preview"]
    assert result["draft"] == draft
    assert c["studio"].research.get(result["job_id"])["draft"] is None
    assert not c["calls"]
    reply["request_id"] = "wrong-job"
    with pytest.raises(DataError) as wrong:
        c["workbench"].preview("catalog", p["prepared_id"], json.dumps(reply))
    assert wrong.value.status_code == 409


def test_automatic_volume_sources_are_read_before_prompt_and_canonical_on_preview(context, monkeypatch):
    c = context
    documents, doc = c["studio"].documents, c["document"]
    documents.update_book_metadata(doc["id"], {"title": "合成小说", "author": "测试作者"}, expected_version_id=doc["latest_version_id"])
    source = {"id": "verified-1", "url": "https://fixture.example/volumes", "title": "官方目录", "excerpt": "合成小说，测试作者，第一卷包括本地两章。", "fetch_status": "verified"}
    calls = []
    def sources(title, author, query):
        calls.append((title, author, query))
        return [source], "fixture-search"
    monkeypatch.setattr(c["workbench"], "_sources", sources)
    monkeypatch.setattr(c["studio"].research.sources, "settings", lambda: {"target_fingerprint": "fixture-search"})
    p = c["workbench"].prepare("volumes", {"document_id": doc["id"], "version_id": doc["latest_version_id"], "base_revision": 0}, profile_id="review-model", mode="automatic")
    assert calls and not c["calls"] and source["excerpt"] in p["prompt"]
    reply = copy.deepcopy(p["template"])
    reply.update(volumes=[{"title": "第一卷", "start_chapter": 1, "end_chapter": 2}], sources=[{"url": source["url"], "title": "伪造标题", "excerpt": "伪造内容"}], source_ids=[source["id"]])
    result = c["workbench"].preview("volumes", p["prepared_id"], json.dumps(reply))["preview"]
    assert result["sources"] == [source]
    reply["source_ids"] = ["unknown"]
    with pytest.raises(DataError, match="实际读取的来源"):
        c["workbench"].preview("volumes", p["prepared_id"], json.dumps(reply))
    assert c["studio"].volumes.get(doc["id"], doc["latest_version_id"])["revision"] == 0


def test_repair_prompt_edit_sends_verbatim_and_source_session_binding_stays_fixed(context, tmp_path):
    c = context
    root = tmp_path / "fixture-project"
    (root / "backend/inkreference").mkdir(parents=True)
    (root / "frontend/src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.0.0"\n')
    target = root / "backend/inkreference/documents.py"
    target.write_text("def value():\n    return 1\n")
    repair = QuickRepair(tmp_path / "repair-data", source_root=root, models=c["studio"].models)
    workbench = PromptWorkbench(c["studio"], lambda: repair)
    try:
        p = workbench.prepare("repair", {"description": "修改合成返回值", "topic": "chapters"}, profile_id="review-model", mode="automatic")
        assert not c["calls"]
        reply = copy.deepcopy(p["template"])
        reply["summary"] = "修改返回值"
        reply["files"][0]["edits"] = [{"before": "return 1", "after": "return 2"}]
        c["config"]["reply"] = json.dumps(reply)
        edited = p["prompt"] + "\n只处理该返回值。"
        workbench.execute("repair", p["prepared_id"], edited)
        assert c["calls"][-1]["messages"][-1]["content"] == edited
        wrong = copy.deepcopy(reply)
        wrong["session_id"] = "different-session"
        with pytest.raises(DataError) as mismatch:
            workbench.preview("repair", p["prepared_id"], json.dumps(wrong))
        assert mismatch.value.status_code == 409
        preview = workbench.preview("repair", p["prepared_id"], json.dumps(reply))["preview"]
        assert preview["preview_id"] and "return 1" in target.read_text()
        target.write_text("def value():\n    return 3\n")
        with pytest.raises(DataError, match="源码已变化"):
            workbench.validate_for_confirmation("repair", p["prepared_id"])
    finally:
        repair.shutdown()


def test_catalog_automatic_keeps_verified_sources_actual_usage_and_metered_embedding(context, monkeypatch):
    c = context
    # Use the shared instance mounted by the real application for final guards.
    from inkreference.prompt_workbench import get_prompt_workbench
    workbench = get_prompt_workbench(c["studio"], c["app"].state.quick_repair)
    source = {"id": "verified-web", "url": "https://fixture.example/novel", "title": "合成小说 测试作者", "excerpt": "合成小说，作者测试作者。旅人帮助他人获得伙伴。", "fetch_status": "verified"}
    monkeypatch.setattr(workbench, "_sources", lambda *args: ([source], "fixture-search"))
    monkeypatch.setattr(c["studio"].research.sources, "settings", lambda: {"target_fingerprint": "fixture-search"})
    p = workbench.prepare("catalog", {"title": "合成小说", "author": "测试作者", "embedding_profile_id": "review-model", "embedding_model": "synthetic-embedding"}, profile_id="review-model", mode="automatic")
    assert not c["calls"]
    draft = {"identity": {"confirmed": True, "alternatives": [], "notes": "匹配作品和作者"},
             "work": {"title": "合成小说", "author": "测试作者", "category": "中文小说", "publication_date": "", "synopsis": "旅人助人", "discussion_heat": None, "market_scale": None, "source_ids": [source["id"]]},
             "characters": [], "cliches": [{"candidate_id": "trope-one", "content": "帮助陌生人获得同伴", "character_trait": "利他", "plot_mechanism": "", "character_ids": [], "source_ids": [source["id"]]}], "warnings": []}
    reply = {"protocol": "inkreference.catalog.v1", "request_id": p["binding"]["request_id"], "sources": [{key: source[key] for key in ("id", "url", "title", "excerpt")}], "draft": draft, "suggested_matches": {}}
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0, 0.0, 0.0]} for i in range(len(body["input"]))], "usage": {"prompt_tokens": 17, "total_tokens": 17}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 123, "completion_tokens": 47}})
    c["studio"].models.transport = httpx.MockTransport(handler)
    workbench.execute("catalog", p["prepared_id"], p["prompt"] + "\n保留已核实来源。")
    workbench.preview("catalog", p["prepared_id"], json.dumps(reply))
    assert len(calls) == 1 and calls[0][0].endswith("chat/completions")
    response = c["client"].post(f"/api/studio/research/jobs/{p['domain_context']['job_id']}/reply", json={"prepared_id": p["prepared_id"], "reply": json.dumps(reply), "expected_revision": 1, "expected_embedding_fingerprint": p["embedding_fingerprint"]})
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = c["studio"].research.get(p["domain_context"]["job_id"])
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(.005)
    assert job["status"] == "needs_review", job["error"]
    assert job["mode"] == "automatic" and job["dedup_mode"] == "vector"
    assert job["sources"][0]["fetch_status"] == "verified"
    assert job["output_metering"]["kind"] == "app_api" and job["usage"]["calls"] == 2
    assert len(calls) == 2 and calls[1][0].endswith("embeddings")
    assert workbench.get(p["prepared_id"])["usage"]["input_tokens"] == 123


def test_transform_final_editor_rechecks_proof_and_actual_save_binding(context):
    c = context
    from inkreference.prompt_workbench import get_prompt_workbench
    workbench = get_prompt_workbench(c["studio"], c["app"].state.quick_repair)
    p = workbench.prepare("translate", {"document_id": c["document"]["id"], "expected_revision": c["state"]["revision"], "entry_index": 0})
    reply = copy.deepcopy(p["template"])
    reply["data"] = {"text": "第一版译文"}
    workbench.preview("translate", p["prepared_id"], json.dumps(reply))
    validated = c["client"].post(f"/api/studio/documents/{c['document']['id']}/reference/transform", json={"kind": "translate", "prepared_id": p["prepared_id"], "payload": {"output_contract": 4, "expected_revision": c["state"]["revision"], "manual_data": {"text": "用户修订译文"}}})
    assert validated.status_code == 200, validated.text
    entries = copy.deepcopy(c["state"]["sections"]["raw_entries"])
    entries[0]["content"] = "未经最终校验的其他内容"
    path = f"/api/studio/documents/{c['document']['id']}/reference/raw_entries"
    failed = c["client"].put(path, json={"prepared_id": p["prepared_id"], "expected_revision": c["state"]["revision"], "data": entries})
    assert failed.status_code == 409
    entries[0]["content"] = validated.json()["data"]["text"]
    saved = c["client"].put(path, json={"prepared_id": p["prepared_id"], "expected_revision": c["state"]["revision"], "data": entries})
    assert saved.status_code == 200, saved.text
    assert workbench.get(p["prepared_id"])["status"] == "completed"


def test_fixed_cache_repair_mirrors_only_scalar_progress_into_task_metadata(context):
    c = context
    arrived, release = threading.Event(), threading.Event()
    def rebuild(document_id, version_id):
        arrived.set()
        assert release.wait(5)
        return {"ok": True}
    service = QuickRepair(c["studio"].data_dir, cache_rebuilder=rebuild)
    try:
        operation = service.rebuild_cache(c["document"]["id"], c["document"]["latest_version_id"])
        assert arrived.wait(5)
        with c["studio"].models._connect() as db:
            row = dict(db.execute("SELECT * FROM task_external_metadata WHERE id=?", (operation["operation_id"],)).fetchone())
        assert row["status"] == "running" and row["operation"] == "repair_cache" and row["document_id"] == c["document"]["id"]
        assert "files" not in row and "prompt" not in row
        release.set()
        for thread in service.active_threads():
            thread.join(5)
        with c["studio"].models._connect() as db:
            row = dict(db.execute("SELECT * FROM task_external_metadata WHERE id=?", (operation["operation_id"],)).fetchone())
        assert row["status"] == "succeeded" and row["progress"] == 1
    finally:
        release.set()
        service.shutdown()


@pytest.mark.parametrize("stage_budget", [False, True])
def test_edited_extraction_prompt_respects_task_and_stage_token_budget_before_send(context, stage_budget):
    c = context
    definition = copy.deepcopy(ADAPTIVE_WORKFLOW)
    definition.update(id="review-budget", budget_tokens=800000 if stage_budget else 1)
    if stage_budget:
        definition["stage_settings"] = {"plot": {"budget_tokens": 1}}
    c["studio"].workflows.save(definition)
    runner = c["studio"].runner
    run = runner.create_run(c["document"]["id"], workflow_id=definition["id"], workflow_version=definition["version"], profile_id="review-model", reuse_existing=False)
    p = c["workbench"].prepare("extraction", {"run_id": run["id"], "chunk_id": run["candidates"][0]["chunk_id"]}, profile_id="review-model")
    with pytest.raises(DataError, match="阶段 token 预算" if stage_budget else "任务 token 预算"):
        c["workbench"].execute("extraction", p["prepared_id"], p["prompt"] + "\n用户追加要求。")
    assert not c["calls"]
