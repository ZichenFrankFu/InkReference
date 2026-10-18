"""Cross-service user journeys for the catalog and text-cleaning contracts."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from urllib.parse import unquote

from inkreference.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-dist"), base_url="http://127.0.0.1") as session:
        yield session


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def add_work(client, identifier, *, category="中文小说", seen=""):
    return checked(client.post("/api/records/works", json={"values": {
        "作品ID": identifier, "作品": f"合成作品{identifier}", "类别": category,
        "用户是否完整体验过": seen,
    }}), 201)


def test_catalog_filters_run_before_pagination_and_opening_empty_analysis_is_not_an_analysis(client, monkeypatch):
    for identifier, category, seen in (("W1", "游戏", "是"), ("W2", "中文小说", "否"),
                                       ("W3", "中文小说", ""), ("W4", "中文小说", "是")):
        add_work(client, identifier, category=category, seen=seen)
    runtime = client.app.state.runtime
    assert runtime._studio is None
    novels = checked(client.get("/api/records/works", params={"category": "中文小说", "seen": "是"}))
    assert [row["id"] for row in novels["items"]] == ["W4"]
    assert runtime._studio is None
    assert checked(client.get("/api/records/works?seen=unknown"))["items"][0]["id"] == "W3"
    documents = runtime.studio.documents
    docs = {key: documents.upload(f"{key}.txt", "第1章 合成\n夜色落在车站。".encode(), work_id=key)
            for key in ("W2", "W3", "W4")}
    reference = runtime.studio.reference
    reference.get(docs["W3"]["id"])
    state = reference.get(docs["W4"]["id"])
    reference.save_section(docs["W4"]["id"], "settings", [
        {"category": "地点", "title": "旧车站", "content": "夜间关闭。", "first_chapter": "1", "updates": []}
    ], state["revision"])

    def no_full_text(*args, **kwargs):
        pytest.fail("catalog metadata filtering must not read a whole book")

    monkeypatch.setattr(documents, "get_version", no_full_text)
    page = checked(client.get("/api/records/works", params={
        "has_text": "true", "has_analysis": "false", "sort_by": "作品ID", "sort_direction": "asc", "limit": 1, "offset": 1,
    }))
    assert page["total"] == 2 and [item["id"] for item in page["items"]] == ["W3"]
    analyzed = checked(client.get("/api/records/works?has_analysis=true"))
    assert [item["id"] for item in analyzed["items"]] == ["W4"]
    assert checked(client.get("/api/records/works?has_text=false"))["items"][0]["id"] == "W1"
    assert client.get("/api/records/characters?has_text=true").status_code == 422


def test_seen_toggle_is_revision_checked_and_link_protection_has_a_working_unlink_path(client):
    work = add_work(client, "W-LINK")
    updated = checked(client.patch("/api/records/works/W-LINK", json={
        "values": {"用户是否完整体验过": "是"}, "expected_revision": work["revision"],
    }))
    assert updated["values"]["用户是否完整体验过"] == "是"
    assert client.patch("/api/records/works/W-LINK", json={
        "values": {"用户是否完整体验过": "否"}, "expected_revision": work["revision"],
    }).status_code == 409
    documents = client.app.state.runtime.studio.documents
    document = documents.upload("关联测试.txt", "第1章 灯\n正文保留。".encode())
    path = f"/api/studio/documents/{document['id']}/catalog"
    link = {"expected_work_id": None, "expected_version_id": document["latest_version_id"],
            "work_id": work["id"], "complete": True}
    checked(client.put(path, json=link))
    blocked = client.delete(f"/api/records/works/{work['id']}?expected_revision={updated['revision']}&cascade=true")
    assert blocked.status_code == 409 and "解除关联" in blocked.text
    checked(client.put(path, json={"expected_work_id": work["id"],
        "expected_version_id": document["latest_version_id"], "work_id": None}))
    checked(client.delete(f"/api/records/works/{work['id']}?expected_revision={updated['revision']}&cascade=true"))
    assert documents.get_version(document["id"])["text"].endswith("正文保留。")
    assert checked(client.get(path))["work_id"] is None


def test_resegment_http_requires_a_choice_and_never_unions_two_directories(client):
    documents = client.app.state.runtime.studio.documents
    body = "正文" * 1819
    source = f"第1章 开始\n开场。\n第2章 火灶房\n第2章\n{body}\n第3章 尾声\n灯熄了。"
    document = documents.upload("目录冲突.txt", source.encode(), preserve_text=True)
    path = f"/api/studio/documents/{document['id']}/chapters"
    payload = {"base_version_id": document["latest_version_id"], "heading_formats": ["第N章"],
               "custom_formats": [], "repair_duplicates": True}
    assert client.post(path + "/resegment?summary=true", json=payload).status_code == 409
    preview = checked(client.post(path + "/resegment/preview", json=payload))
    assert preview["conflict"] and preview["old"]["chapter_count"] > preview["new"]["chapter_count"]
    kept = checked(client.post(path + "/resegment?summary=true", json={
        **payload, "preview_token": preview["preview_token"], "conflict_policy": "keep_old",
    }), 201)
    assert kept["id"] == document["latest_version_id"]
    adopted = checked(client.post(path + "/resegment?summary=true", json={
        **payload, "preview_token": preview["preview_token"], "conflict_policy": "use_new",
    }), 201)
    index_path = f"/api/studio/documents/{document['id']}/versions/{adopted['id']}/chapters"
    index = checked(client.get(index_path))
    actual = [item for item in index["chapters"] if not item.get("placeholder")]
    assert [item["number"] for item in actual] == [1, 2, 3]
    assert actual[1]["title"] == "火灶房"
    content = checked(client.get(index_path + f"/{actual[1]['id']}?body_only=true"))
    assert content["text"].strip() == body
    deleted = checked(client.post(path + "/delete?summary=true", json={
        "base_version_id": adopted["id"], "chapter_ids": [actual[0]["id"]],
    }), 201)
    final_index = checked(client.get(f"/api/studio/documents/{document['id']}/versions/{deleted['id']}/chapters"))
    assert [item["number"] for item in final_index["chapters"]] == [1, 2]
    exported = client.get(f"/api/studio/documents/{document['id']}/versions/{deleted['id']}/download?format=clean")
    assert exported.status_code == 200
    assert unquote(exported.headers["content-disposition"]).endswith("目录冲突.txt")
    assert exported.content.decode("utf-8-sig").count(body) == 1
    assert documents.get_version(document["id"], document["latest_version_id"])["text"] == source
