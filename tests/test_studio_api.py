"""Workbench HTTP integration; all model responses come from MockTransport."""

from __future__ import annotations

import copy
import csv
import io
import json
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app


PREFIX = "/api/studio"
SOURCE = "第一章 借伞\r\n江屿捡起红伞。\r\n许云蝉告诉他：&quot;明天还给我。&quot;\r\n"
EVIDENCE = "江屿捡起红伞。"
EXTRACTION = {
    "summary": "江屿捡到红伞，许云蝉请他次日归还。",
    "characters": [{"name": "江屿", "description": "捡起红伞的人", "evidence": EVIDENCE}],
    "events": [{"name": "捡伞", "description": "江屿捡起红伞。", "evidence": EVIDENCE}],
    "cliches": [{"name": "借物相识", "description": "人物因红伞发生接触。", "evidence": EVIDENCE}],
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-dist"), base_url="http://127.0.0.1") as session:
        yield session


def items(response):
    assert response.status_code == 200, response.text
    value = response.json()
    return value if isinstance(value, list) else value["items"]


def upload_document(client):
    result = client.post(PREFIX + "/documents", files={"file": (r"C:\sample\全文.txt", SOURCE.encode("utf-8-sig"), "text/plain")}, data={"title": "合成参考作品", "encoding": "auto"})
    assert result.status_code in {200, 201}, result.text
    return result.json()


def create_model(client, **values):
    response = client.post(PREFIX + "/models", json={
        "name": "离线协议测试", "provider": "openai_compatible", "base_url": "http://127.0.0.1:1234/v1",
        "model": "mock-only", "context_window": 65536, "max_output_tokens": 1024, "timeout_seconds": 10,
        **values,
    })
    assert response.status_code in {200, 201}, response.text
    return response.json()


def wait_for_job(client, run_id):
    deadline = time.monotonic() + 10
    while True:
        response = client.get(PREFIX + f"/jobs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] not in {"queued", "running", "cancelling"} or time.monotonic() >= deadline:
            return run
        time.sleep(.02)


def test_document_upload_preserves_original_and_version_history(client):
    assert items(client.get(PREFIX + "/documents")) == []
    document = upload_document(client)
    assert document["filename"] == "全文.txt"
    detail = client.get(PREFIX + f"/documents/{document['id']}").json()
    assert len(detail["versions"]) == 1
    version_id = detail["latest_version_id"]
    version = client.get(PREFIX + f"/documents/{document['id']}/versions/{version_id}").json()
    assert '"明天还给我。"' in version["text"]
    assert "\r" not in version["text"]
    assert version["coverage"]["complete"] is True
    assert version["coverage"]["chunks_complete"] is True
    assert "".join(chunk["text"] for chunk in version["chunks"]) == version["text"]
    original = client.get(PREFIX + f"/documents/{document['id']}/original/download")
    assert original.status_code == 200
    assert "attachment" in original.headers["content-disposition"]
    assert original.content == SOURCE.encode("utf-8-sig")
    edited = version["text"] + "江屿记下了归还日期。\n"
    saved = client.post(PREFIX + f"/documents/{document['id']}/versions", json={"base_version_id": version_id, "options": version["options"], "manual_text": edited})
    assert saved.status_code in {200, 201}, saved.text
    assert saved.json()["version_number"] == 2
    old = client.get(PREFIX + f"/documents/{document['id']}/versions/{version_id}").json()
    assert old["text"] == version["text"]
    assert client.get(PREFIX + f"/documents/{document['id']}/original/download").content == original.content
    stale = client.post(PREFIX + f"/documents/{document['id']}/versions", json={"base_version_id": version_id, "options": {}, "manual_text": "过期修改"})
    assert stale.status_code == 409


def test_model_session_secret_never_returned_and_connection_test_is_real_transport(client):
    profile = create_model(client, api_key="studio-session-secret-888")
    assert "api_key" not in profile
    assert profile["has_session_key"] is True
    assert "studio-session-secret-888" not in client.get(PREFIX + "/models").text
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 1}})
    client.app.state.runtime.studio.models.transport = httpx.MockTransport(handle)
    response = client.post(PREFIX + f"/models/{profile['id']}/test", json={})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer studio-session-secret-888"
    usage_response = client.get(PREFIX + "/usage")
    assert usage_response.status_code == 200, usage_response.text
    assert "studio-session-secret-888" not in usage_response.text


def test_model_draft_discovery_and_credential_clear_are_gui_ready(client):
    requests = []
    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "synthetic-discovered"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})
    client.app.state.runtime.studio.models.transport = httpx.MockTransport(handle)
    body = {"name": "草稿", "provider": "openai_compatible", "connection_mode": "local", "base_url": "http://127.0.0.1:1234/v1", "model": "synthetic", "api_key": "draft-private-918"}
    assert "vault_available" in client.get(PREFIX + "/models/security").json()
    assert client.post(PREFIX + "/models/test-draft", json=body).json()["ok"] is True
    assert items(client.get(PREFIX + "/models")) == []
    discovered = client.post(PREFIX + "/models/discover", json={**body, "model": ""})
    assert discovered.status_code == 200 and discovered.json()["models"] == [{"id": "synthetic-discovered"}]
    assert all(request.headers.get("Authorization") == "Bearer draft-private-918" for request in requests)
    saved = create_model(client, api_key="clear-private-225")
    cleared = client.delete(PREFIX + f"/models/{saved['id']}/credentials")
    assert cleared.status_code == 200 and not cleared.json()["key_available"]


def test_remote_tests_are_direct_and_job_confirmation_is_reused_on_resume(client):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(EXTRACTION, ensure_ascii=False)}, "finish_reason": "stop"}]})
    client.app.state.runtime.studio.models.transport = httpx.MockTransport(handle)
    profile = create_model(client, base_url="https://provider.example/v1", connection_mode="remote", api_key="remote-private-113")
    path = PREFIX + f"/models/{profile['id']}/test"
    assert client.post(path, json={}).status_code == 200
    assert len(requests) == 1
    requests.clear()
    document = upload_document(client)
    body = {"document_id": document["id"], "version_id": document["latest_version_id"], "profile_id": profile["id"], "workflow_version": "1.1.0", "start": False}
    assert client.post(PREFIX + "/jobs", json=body).status_code == 428
    assert not items(client.get(PREFIX + "/jobs"))
    assert client.post(PREFIX + "/jobs", json={**body, "remote_consent": True}).status_code == 428
    created = client.post(PREFIX + "/jobs", json={**body, "remote_consent": True, "expected_model_fingerprint": profile["target_fingerprint"]})
    assert created.status_code == 201
    job_path = PREFIX + f"/jobs/{created.json()['id']}"
    assert client.post(job_path + "/cancel", json={}).status_code == 200
    assert not requests
    assert client.post(job_path + "/resume", json={}).status_code == 200
    assert wait_for_job(client, created.json()["id"])["status"] == "needs_review"
    assert len(requests) == 1


@pytest.mark.parametrize("changed", [{"base_url": "https://changed-without-reconfirm.example/v1"}, {"model": "changed-model"}], ids=["endpoint", "model"])
def test_remote_confirmation_is_bound_to_displayed_target_and_model(client, changed):
    requests = []
    client.app.state.runtime.studio.models.transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(500))
    shown = create_model(client, base_url="https://shown-provider.example/v1", connection_mode="remote")
    document = upload_document(client)
    # Another tab changes the profile after this tab displays the consent panel.
    updated = client.put(PREFIX + f"/models/{shown['id']}", json=changed)
    assert updated.status_code == 200
    request_body = {"document_id": document["id"], "version_id": document["latest_version_id"], "profile_id": shown["id"], "remote_consent": True, "expected_model_fingerprint": shown["target_fingerprint"]}
    response = client.post(PREFIX + "/jobs", json=request_body)
    assert response.status_code == 409
    assert not requests and items(client.get(PREFIX + "/jobs")) == []


def test_builtin_definitions_and_json_skill_upload(client):
    skills = items(client.get(PREFIX + "/skills"))
    workflows = items(client.get(PREFIX + "/workflows"))
    builtin = next(item for item in skills if item["id"] == "unified-reference")
    assert any(item["id"] == "default" for item in workflows)
    custom = {key: value for key, value in copy.deepcopy(builtin).items() if key not in {"builtin", "created_at"}}
    custom.update({"id": "custom-evidence", "version": "1.0.0", "name": "证据提取测试"})
    response = client.post(PREFIX + "/skills/upload", files={"file": ("skill.json", json.dumps(custom, ensure_ascii=False).encode("utf-8"), "application/json")})
    assert response.status_code in {200, 201}, response.text
    assert response.json()["id"] == "custom-evidence"
    assert any(item["id"] == "custom-evidence" for item in items(client.get(PREFIX + "/skills")))


def test_mocked_job_review_and_approve_integrates_with_csv_library(client):
    document = upload_document(client)
    profile = create_model(client)
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(EXTRACTION, ensure_ascii=False)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 600, "completion_tokens": 100}})
    client.app.state.runtime.studio.models.transport = httpx.MockTransport(handle)
    response = client.post(PREFIX + "/jobs", json={"document_id": document["id"], "version_id": document["latest_version_id"], "workflow_id": "default", "workflow_version": "1.0.0", "profile_id": profile["id"], "start": True})
    assert response.status_code in {200, 201, 202}, response.text
    run_id = response.json()["id"]
    run = wait_for_job(client, run_id)
    assert run["status"] == "needs_review", {"error": run["error"], "candidates": run["candidates"]}
    assert requests
    assert run["coverage"]["complete"] is True
    assert run["completed"] == run["total"] > 0
    assert run["results"]["characters"][0]["evidence"] == EVIDENCE
    assert client.get("/api/stats").json()["works"] == 0
    revised = copy.deepcopy(run["results"])
    revised["summary"] = "人工审核后的剧情梗概。"
    saved = client.put(PREFIX + f"/jobs/{run_id}/results", json={"results": revised, "expected_revision": run["revision"]})
    assert saved.status_code == 200, saved.text
    old_revision = run["revision"]
    run = saved.json()
    assert client.put(PREFIX + f"/jobs/{run_id}/results", json={"results": revised, "expected_revision": old_revision}).status_code == 409
    approved = client.post(PREFIX + f"/jobs/{run_id}/approve", json={"expected_revision": run["revision"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    stats = client.get("/api/stats").json()
    assert stats == {"works": 1, "characters": 1, "cliches": 1, "work_cliches": 1}
    works = client.get("/api/records/works").json()["items"]
    assert works[0]["values"]["剧情梗概"] == "人工审核后的剧情梗概。"
    exported = client.get("/api/export/works")
    assert exported.status_code == 200
    exported_work = next(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"), newline="")))
    assert exported_work["剧情梗概"] == "人工审核后的剧情梗概。"
    again = client.post(PREFIX + f"/jobs/{run_id}/approve", json={"expected_revision": approved.json()["revision"]})
    assert again.status_code in {200, 409}
    assert client.get("/api/stats").json() == stats


def test_project_backup_restores_document_custom_skill_workflow_layout_and_profiles(client, tmp_path):
    document = upload_document(client)
    profile = create_model(client, api_key="backup-must-not-persist-this-secret")
    skill = copy.deepcopy(client.get(PREFIX + "/skills/unified-reference/versions/1.1.0").json())
    skill.pop("builtin", None)
    skill.update({"id": "backup-skill", "name": "备份 Skill"})
    assert client.post(PREFIX + "/skills", json=skill).status_code == 201
    workflow = copy.deepcopy(client.get(PREFIX + "/workflows/default/versions/1.1.0").json())
    workflow.pop("builtin", None)
    workflow.update({"id": "backup-workflow", "name": "备份工作流", "skill_id": "backup-skill"})
    assert client.post(PREFIX + "/workflows", json=workflow).status_code == 201
    layout_path = PREFIX + f"/workflows/backup-workflow/versions/{workflow['version']}/layout"
    layout = {"nodes": [{"id": "extract", "position": {"x": 120, "y": 80}}], "edges": []}
    saved_layout = client.put(layout_path, json={"layout": layout, "expected_revision": 0})
    assert saved_layout.status_code == 200, saved_layout.text
    assert client.put(layout_path, json={"layout": layout, "expected_revision": 0}).status_code == 409
    backup = client.get(PREFIX + "/projects/export")
    assert backup.status_code == 200, backup.text
    assert backup.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        assert {"inkreference.sqlite3", "workbench.sqlite3", "skills.sqlite3", "workflow_definitions.sqlite3", "manifest.json"}.issubset(archive.namelist())
        for name in archive.namelist():
            assert b"backup-must-not-persist-this-secret" not in archive.read(name)
    restored = client.post(PREFIX + "/projects/restore", files={"file": ("project.zip", backup.content, "application/zip")})
    assert restored.status_code == 201, restored.text
    assert restored.json()["restored"] is True
    assert restored.json()["restart_required"] is True
    destination = restored.json()["data_dir"]
    with TestClient(create_app(destination, tmp_path / "no-restored-dist"), base_url="http://127.0.0.1") as reopened:
        assert items(reopened.get(PREFIX + "/documents"))[0]["id"] == document["id"]
        assert reopened.get(PREFIX + f"/documents/{document['id']}/original/download").content == SOURCE.encode("utf-8-sig")
        assert any(item["id"] == "backup-skill" for item in items(reopened.get(PREFIX + "/skills")))
        assert any(item["id"] == "backup-workflow" for item in items(reopened.get(PREFIX + "/workflows")))
        assert reopened.get(layout_path).json()["layout"] == layout
        profiles = items(reopened.get(PREFIX + "/models"))
        assert profiles[0]["id"] == profile["id"]
        assert profiles[0]["has_session_key"] is False
        assert profiles[0]["key_available"] is False
    # Restore creates a separate copy and never rewrites the live project.
    assert client.get(layout_path).json()["revision"] == saved_layout.json()["revision"]


def test_studio_rejects_unsafe_uploads_foreign_origin_and_missing_relations(client):
    unsafe = client.post(PREFIX + "/skills/upload", files={"file": ("skill.py", b"raise RuntimeError('must not execute')", "text/x-python")})
    assert unsafe.status_code == 422
    assert client.post(PREFIX + "/projects/restore", files={"file": ("broken.zip", b"not a zip", "application/zip")}).status_code == 422
    upload = {"file": ("sample.txt", SOURCE.encode("utf-8"), "text/plain")}
    assert client.post(PREFIX + "/documents", files=upload, data={"work_id": "missing-work"}).status_code == 404
    assert items(client.get(PREFIX + "/documents")) == []
    response = client.post(PREFIX + "/models", json={"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen"}, headers={"Origin": "https://foreign.example"})
    assert response.status_code == 403
    assert items(client.get(PREFIX + "/models")) == []


def test_insufficient_workflow_budget_stops_before_any_model_request(client):
    document = upload_document(client)
    profile = create_model(client)
    workflow = copy.deepcopy(items(client.get(PREFIX + "/workflows"))[0])
    workflow.pop("builtin", None)
    workflow.update({"id": "insufficient-budget", "budget_tokens": 1})
    saved = client.post(PREFIX + "/workflows", json=workflow)
    assert saved.status_code == 201, saved.text
    requests = []
    client.app.state.runtime.studio.models.transport = httpx.MockTransport(lambda request: requests.append(request))
    created = client.post(PREFIX + "/jobs", json={"document_id": document["id"], "version_id": document["latest_version_id"], "workflow_id": workflow["id"], "workflow_version": workflow["version"], "profile_id": profile["id"], "start": True})
    assert created.status_code == 201, created.text
    run = wait_for_job(client, created.json()["id"])
    assert run["status"] == "failed", run["error"]
    assert "预算" in run["error"]
    assert requests == []
    assert client.get(PREFIX + "/usage").json()["summary"]["calls"] == 0


def test_linked_full_text_keeps_work_id_from_being_deleted_and_reused(client):
    work = client.post("/api/records/works", json={"values": {"作品ID": "W-linked", "作品": "保留原件"}}).json()
    document = client.post(PREFIX + "/documents", files={"file": ("source.txt", SOURCE.encode("utf-8"))}, data={"work_id": "W-linked"})
    assert document.status_code == 201
    response = client.delete(f"/api/records/works/W-linked?expected_revision={work['revision']}&cascade=true")
    assert response.status_code == 409
    assert "全文" in response.json()["detail"]
    assert client.get("/api/records/works/W-linked").status_code == 200
