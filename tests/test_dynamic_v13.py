"""Decision modes share contracts; real services use synthetic model responses."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.database import DataError
from inkreference.dynamic_functions import operation_definition, operation_registry, parse_json_reply, validate_binding
from inkreference.dynamic_routes import create_dynamic_router
from inkreference.models import ModelService
from inkreference.quick_repair import PROTOCOL, QuickRepair


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def context(tmp_path):
    root = tmp_path / "checkout"
    (root / "backend/inkreference").mkdir(parents=True)
    (root / "frontend/src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname="synthetic-repair"\nversion="0.0.0"\n')
    target = root / "backend/inkreference/documents.py"
    target.write_text("def value():\n    return 1\n")
    models = ModelService(tmp_path / "data")
    models.save_profile({"id": "repair-test", "provider": "openai_compatible", "model": "synthetic-repair-v1", "base_url": "http://127.0.0.1:12345/v1", "context_window": 32000, "max_output_tokens": 2000})
    service = QuickRepair(tmp_path / "data", source_root=root, models=models)
    calls, config = [], {}

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if config.get("gate"):
            config["gate"][0].set()
            assert config["gate"][1].wait(10)
        content = body["messages"][-1]["content"]
        payload = json.loads(content[content.find("\n\n{") + 2:])
        session_id = config.get("session_id", payload["session_id"])
        plan = {"protocol": PROTOCOL, "session_id": session_id, "summary": "修改合成返回值", "files": [
            {"path": "backend/inkreference/documents.py", "sha256": digest(target.read_bytes()), "edits": [{"before": "return 1", "after": "return 2"}]}]}
        if config.get("unknown"):
            plan["commands"] = ["arbitrary-command"]
        result = config.get("reply", json.dumps(plan, ensure_ascii=False))
        return httpx.Response(200, json={"choices": [{"message": {"content": result}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 101, "completion_tokens": 47}})

    models.transport = httpx.MockTransport(handler)
    yield {"service": service, "models": models, "target": target, "calls": calls, "config": config, "root": root, "data": tmp_path / "data"}
    if config.get("gate"):
        config["gate"][1].set()
    service.shutdown()


def complete(service, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = service.generation(identifier)
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(.005)
    pytest.fail("synthetic generation did not complete")


def test_registry_six_adapters_three_reviewable_modes_and_copy_isolation():
    value = operation_registry()
    assert {item["id"] for item in value["operations"]} == {"catalog", "volumes", "extraction", "repair", "summarize", "translate"}
    assert {mode["id"] for mode in value["modes"]} == {"automatic", "semi", "manual"}
    assert all(mode["editable_preview"] and mode["explicit_confirmation"] for mode in value["modes"])
    value["operations"][0]["manual_template"]["work"]["title"] = "mutation"
    assert operation_definition("catalog")["manual_template"]["work"]["title"] == ""
    with pytest.raises(DataError):
        operation_definition("not-a-domain")


@pytest.mark.parametrize("content", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', '[1]', 'prefix {"a":1}', '```json\n{"a":1}\n``` prose', '{"a":"\\ud800"}'])
def test_shared_reply_rejects_ambiguous_or_invalid_json(content):
    with pytest.raises(DataError):
        parse_json_reply(content)


def test_shared_reply_fence_depth_size_and_bound_session():
    assert parse_json_reply('```json\n{"request_id":"a","v":1}\n```') == {"request_id": "a", "v": 1}
    with pytest.raises(DataError):
        parse_json_reply('{"long":"abc"}', maximum=4)
    with pytest.raises(DataError):
        parse_json_reply('{"a":{"b":{"c":1}}}', max_depth=2)
    with pytest.raises(DataError):
        parse_json_reply('{"a":[1,2,3]}', max_nodes=3)
    validate_binding({"request_id": "a"}, {"request_id": "a"})
    with pytest.raises(DataError) as error:
        validate_binding({"request_id": "old"}, {"request_id": "new"})
    assert error.value.status_code == 409


def test_manual_template_source_picker_pins_version_and_never_changes_files(context):
    service, target = context["service"], context["target"]
    before = target.read_bytes()
    result = service.prompt("修改返回值", "chapters")
    template = result["template"]
    assert template["session_id"] == result["session_id"]
    source = service.session_source(result["session_id"], "backend/inkreference/documents.py")
    assert source["content"].encode() == before and source["sha256"] == digest(before)
    template["summary"] = "手动修复"
    template["files"][0]["edits"] = [{"before": "return 1", "after": "return 2"}]
    preview = service.preview(json.dumps(template))
    assert "+    return 2" in preview["files"][0]["diff"]
    assert target.read_bytes() == before and not context["calls"]
    target.write_text("def value():\n    return 3\n")
    template["files"][0]["sha256"] = digest(target.read_bytes())
    with pytest.raises(DataError) as error:
        service.preview(json.dumps(template))
    assert error.value.status_code == 409
    with pytest.raises(DataError):
        service.session_source(result["session_id"], "backend/inkreference/documents.py")


def test_automatic_real_provider_ledger_waits_for_review_edit_and_explicit_apply(context):
    service, target = context["service"], context["target"]
    before = target.read_bytes()
    result = service.automatic("修复返回值", "chapters", profile_id="repair-test", background=False)
    assert result["status"] == "needs_review", result
    assert result["progress"] == {"completed": 3, "total": 3, "label": "等待确认"}
    assert result["preview"]["can_apply"]
    assert target.read_bytes() == before
    assert result["usage"]["calls"] == 1 and result["usage"]["actual_output_tokens"] == 47
    assert len(context["calls"]) == 1
    edited = json.loads(result["reply"])
    edited["files"][0]["edits"][0]["after"] = "return 4"
    reviewed = service.preview(json.dumps(edited))
    operation = service.apply(reviewed["preview_id"])
    for thread in service.active_threads():
        thread.join(5)
    assert service.operation(operation["operation_id"])["status"] == "succeeded"
    assert "return 4" in target.read_text()
    service.rollback(operation["operation_id"])
    for thread in service.active_threads():
        thread.join(5)
    assert target.read_bytes() == before


def test_invalid_automatic_reply_retains_charged_reply_and_diagnostic_without_apply(context):
    context["config"]["unknown"] = True
    result = context["service"].automatic("修复", "chapters", profile_id="repair-test", background=False)
    assert result["status"] == "failed" and result["reply"]
    assert result["usage"]["actual_output_tokens"] == 47
    assert result["diagnostic_id"].startswith("err-")
    assert "return 1" in context["target"].read_text()


def test_wrong_session_from_model_cannot_retarget_automatic_preview(context):
    context["config"]["session_id"] = "different-session"
    result = context["service"].automatic("修复", "chapters", profile_id="repair-test", background=False)
    assert result["status"] == "failed" and "会话" in result["error"]
    assert result["preview"] is None


def test_cancel_in_flight_keeps_reply_usage_and_never_applies(context):
    entered, release = threading.Event(), threading.Event()
    context["config"]["gate"] = entered, release
    service = context["service"]
    result = service.automatic("修复", "chapters", profile_id="repair-test")
    assert entered.wait(3)
    cancelled = service.cancel_generation(result["id"])
    assert cancelled["cancel_requested"]
    release.set()
    final = complete(service, result["id"])
    assert final["status"] == "cancelled" and final["reply"]
    assert final["usage"]["calls"] == 1 and final["usage"]["actual_output_tokens"] == 47
    assert final["preview"] is None and "return 1" in context["target"].read_text()


def test_model_target_changes_in_flight_rejects_preview_but_keeps_ledger(context):
    entered, release = threading.Event(), threading.Event()
    context["config"]["gate"] = entered, release
    service = context["service"]
    result = service.automatic("修复", "chapters", profile_id="repair-test")
    assert entered.wait(3)
    context["models"].save_profile({"model": "another-target"}, profile_id="repair-test")
    release.set()
    final = complete(service, result["id"])
    assert final["status"] == "failed" and "模型目标" in final["error"]
    assert final["reply"] and final["usage"]["actual_output_tokens"] == 47


def test_durable_reply_restores_session_for_reparse_and_interrupted_tasks_fail(context):
    service = context["service"]
    result = service.automatic("修复", "chapters", profile_id="repair-test", background=False)
    with context["models"]._connect() as db:
        row = db.execute("SELECT payload_json FROM repair_generations WHERE id=?", (result["id"],)).fetchone()
        db.execute("INSERT INTO repair_generations(id,status,cancel_requested,payload_json,updated_at) VALUES ('interrupted','running',0,?,?)", (row[0], time.time()))
    restarted = QuickRepair(context["data"], source_root=context["root"], models=context["models"])
    try:
        saved = restarted.generation(result["id"])
        assert saved["reply"] == result["reply"]
        assert restarted.preview(saved["reply"])["can_apply"]
        assert restarted.generation("interrupted")["status"] == "failed"
    finally:
        restarted.shutdown()


def test_generation_history_is_bounded_and_summary_does_not_load_replies(context, monkeypatch):
    service = context["service"]
    initial = service.automatic("修复", "chapters", profile_id="repair-test", background=False)
    with context["models"]._connect() as db:
        row = db.execute("SELECT payload_json,summary_json FROM repair_generations WHERE id=?", (initial["id"],)).fetchone()
        for index in range(30):
            db.execute("INSERT INTO repair_generations(id,status,payload_json,summary_json,updated_at) VALUES (?,'failed',?,?,?)", (f"synthetic-{index}", row[0], row[1], time.time() - 60 - index))
    service.automatic("修复", "chapters", profile_id="repair-test", background=False)
    with context["models"]._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM repair_generations").fetchone()[0] == 24
        assert "repair_generations_updated" in {row[1] for row in db.execute("PRAGMA index_list(repair_generations)")}
    monkeypatch.setattr(service, "generation", lambda _: pytest.fail("summary must not fetch full replies or token ledgers"))
    summary = service.recent_generations()
    assert len(summary["items"]) == 24
    assert not any({"reply", "prompt", "template", "profile", "source_files"} & item.keys() for item in summary["items"])


def test_cancel_during_preview_cannot_publish_needs_review(context, monkeypatch):
    service = context["service"]
    original_preview = service.preview

    def cancel_preview(reply):
        result = original_preview(reply)
        with context["models"]._connect() as db:
            db.execute("UPDATE repair_generations SET cancel_requested=1 WHERE status='running'")
        return result

    monkeypatch.setattr(service, "preview", cancel_preview)
    final = service.automatic("修复", "chapters", profile_id="repair-test", background=False)
    assert final["status"] == "cancelled" and final["preview"] is None
    assert final["reply"] and final["usage"]["actual_output_tokens"] == 47


def test_context_fitting_reduces_source_excerpts_but_keeps_binding_and_full_hash(context):
    target = context["target"]
    target.write_text("def value():\n    return 1\n" + "# synthetic source filler\n" * 6000)
    context["models"].save_profile({"context_window": 10000, "max_output_tokens": 1000}, profile_id="repair-test")
    result = context["service"].automatic("修复", "chapters", profile_id="repair-test", background=False)
    assert result["status"] == "needs_review", result
    sent = context["calls"][0]
    assert context["models"].estimate_messages(sent["messages"])["input_tokens"] + sent["max_tokens"] <= 10000
    assert result["truncated"] and digest(target.read_bytes()) in sent["messages"][-1]["content"]


def test_structured_model_timeout_frames_select_actual_source_windows(context):
    import inkreference.models as model_module
    models, service = context["models"], context["service"]
    source = Path(model_module.__file__).read_text(encoding="utf-8")
    (context["root"] / "backend/inkreference/models.py").write_text(source, encoding="utf-8")

    def timeout(request):
        raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_BODY_AND_KEY", request=request)

    models.transport = httpx.MockTransport(timeout)
    with pytest.raises(DataError) as failure:
        models.invoke("repair-test", [{"role": "user", "content": "SYNTHETIC_PRIVATE_INPUT"}], run_id="synthetic-timeout", chunk_id="synthetic")
    assert failure.value.status_code == 504
    # The service exception was already handled before QuickRepair opened;
    # only the structured diagnostic file owns its original model stack.
    assert not service.events
    result = service.prompt("模型请求超时", "models")
    payload = json.loads("{" + result["prompt"].split("\n\n{", 1)[1])
    excerpt = next(item["content"] for item in payload["source_files"] if item["path"] == "backend/inkreference/models.py")
    assert 'response = client.post(' in excerpt
    assert 'except httpx.TimeoutException as error:' in excerpt
    assert "SYNTHETIC_PRIVATE_BODY_AND_KEY" not in result["prompt"] and "SYNTHETIC_PRIVATE_INPUT" not in result["prompt"]
    assert any(event.get("error_type") == "ReadTimeout" for event in result["diagnostics"]["recent_errors"])


def test_unfit_user_context_fails_before_provider_and_remote_target_requires_pin(context):
    context["models"].save_profile({"context_window": 1000, "max_output_tokens": 100}, profile_id="repair-test")
    result = context["service"].automatic("问题" * 100, "chapters", profile_id="repair-test", background=False)
    assert result["status"] == "failed" and "上下文" in result["error"]
    assert not context["calls"] and result["usage"]["calls"] == 0
    context["models"].save_profile({"base_url": "https://models.example.test/v1", "connection_mode": "remote", "context_window": 32000}, profile_id="repair-test")
    with pytest.raises(DataError) as error:
        context["service"].automatic("修复", profile_id="repair-test", remote_consent=True)
    assert error.value.status_code == 428
    with pytest.raises(DataError) as error:
        context["service"].automatic("修复", profile_id="repair-test", remote_consent=True, expected_model_fingerprint="0" * 64)
    assert error.value.status_code == 409


def test_real_http_repair_modes_and_readonly_registry(context, monkeypatch):
    from inkreference.api import create_app
    service = context["service"]
    monkeypatch.setattr("inkreference.repair_routes.QuickRepair", lambda *args, **kwargs: service)
    app = create_app(context["data"])
    app.include_router(create_dynamic_router())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        registry = client.get("/api/studio/dynamic-functions")
        assert registry.status_code == 200 and len(registry.json()["operations"]) == 6
        assert client.get("/api/studio/dynamic-functions/repair").json()["protocol"] == PROTOCOL
        prompt = client.post("/api/studio/repair/prompt", json={"description": "手动", "topic": "chapters"}).json()
        source = client.get(f"/api/studio/repair/sessions/{prompt['session_id']}/source", params={"path": "backend/inkreference/documents.py"})
        assert source.status_code == 200 and source.json()["sha256"] == digest(context["target"].read_bytes())
        response = client.post("/api/studio/repair/automatic", json={"description": "修复", "topic": "chapters", "profile_id": "repair-test"})
        assert response.status_code == 202, response.text
        final = complete(service, response.json()["id"])
        assert client.get("/api/studio/repair/generations/" + final["id"]).json()["status"] == "needs_review"
        assert len(client.get("/api/studio/repair/generations").json()["items"]) == 1
        assert client.post("/api/studio/repair/automatic", json={"description": "修复", "profile_id": "repair-test", "execute_command": "no"}).status_code == 422
        assert "return 1" in context["target"].read_text()
