"""Synthetic catalog search, exact relationship scope and derived status cases."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest

from inkreference.api import create_app
from inkreference.catalog_links import CatalogLinks
from inkreference.catalog_search import CatalogSearch
from inkreference.database import DataStore
from inkreference.documents import DocumentStore
from inkreference.reference_analysis import ReferenceAnalysisStore


def fixture_catalog(tmp_path):
    store = DataStore(tmp_path / "catalog.db")
    for identity, author, genre, style, discussion, market in (
        ("W1", "夜航作者", "科幻", "赛博朋克", '{"min":8,"max":10,"sources":["fictional"]}', "2"),
        ("W2", "另一作者", "奇幻", "克苏鲁", "10", "10"),
        ("W3", "另一作者", "未分类原文\n第二行", "", "2", ""),
    ):
        store.create_record("works", {"作品ID": identity, "作品": "同名作品", "剧情梗概": "synthetic",
            "类别": "中文小说", "作者": author, "作品题材": genre, "作品风格": style,
            "讨论热度": discussion, "商业规模": market, "用户是否完整体验过": "是" if identity == "W1" else "否"})
    store.create_record("characters", {"人物ID": "CH1", "角色名": "夜灯人", "角色定位": "主角", "性格基调": "利他", "关联作品": '[{"作品ID":"W1"}]'})
    store.create_record("characters", {"人物ID": "CH2", "角色名": "同行者", "关联作品": '[{"作品ID":"W1"}]'})
    store.create_record("characters", {"人物ID": "CH3", "角色名": "异界人", "关联作品": '[{"作品ID":"W2"}]'})
    store.create_record("cliches", {"clichéID": "C1", "cliché内容": "霓虹护送", "人设塑造": "利他", "关联作品": '[{"作品ID":"W1","关联内容":"合成关联原文"}]', "关联角色": '[{"人物ID":"CH1","作品ID":"W1","关联内容":"保护同伴"}]'})
    store.create_record("cliches", {"clichéID": "C2", "cliché内容": "异界之门", "关联作品": '[{"作品ID":"W2"}]'})
    documents = DocumentStore(tmp_path / "texts")
    return store, documents, CatalogSearch(store, documents)


def ids(result, table):
    return [item["id"] for item in result["groups"][table]["items"]]


def test_shared_search_author_work_metadata_and_linked_materials(tmp_path, monkeypatch):
    store, documents, search = fixture_catalog(tmp_path)
    monkeypatch.setattr(store, "_all", lambda *args: pytest.fail("search must not hydrate a whole table"))
    monkeypatch.setattr(documents, "get_version", lambda *args: pytest.fail("search must not read full text"))
    result = search.search("夜航作者")
    assert ids(result, "works") == ["W1"]
    assert ids(result, "characters") == ["CH1", "CH2"]
    assert ids(result, "cliches") == ["C1"]
    assert result["groups"]["characters"]["items"][0]["match"]["related"][0]["id"] == "W1"
    assert store.list_records("works", q="夜灯人")["total"] == 1
    assert ids(search.search("霓虹护送"), "works") == ["W1"]
    assert ids(search.search("赛博朋克"), "works") == ["W1"]
    assert all(group["total"] == 0 for group in search.search("%_' OR 1=1 --")["groups"].values())


def test_scope_is_direct_never_infers_coappearance_and_filters_before_paging(tmp_path):
    _, _, search = fixture_catalog(tmp_path)
    result = search.search(character_id="CH1")
    assert ids(result, "works") == ["W1"]
    assert ids(result, "characters") == ["CH1"]
    assert ids(result, "cliches") == ["C1"]
    assert "CH2" not in ids(result, "characters")
    assert result["groups"]["cliches"]["items"][0]["match"]["related"][0]["description"] == "保护同伴"
    filtered = search.search("同名作品", filters={"genre": "科幻", "style": "赛博朋克", "seen": "是", "role": "主角"})
    assert ids(filtered, "works") == ["W1"] and ids(filtered, "characters") == ["CH1"]
    assert ids(filtered, "cliches") == ["C1"]
    page = search.search("中文小说", offset=1, limit=1)
    assert all(len(group["items"]) <= 1 for group in page["groups"].values())
    assert page["groups"]["works"]["total"] == 3


def test_heat_sorts_numeric_midpoint_and_missing_last(tmp_path):
    store, _, search = fixture_catalog(tmp_path)
    assert [r["id"] for r in store.list_records("works", sort_by="discussion_heat", sort_direction="desc")["items"]] == ["W2", "W1", "W3"]
    assert ids(search.search(sort_by="commercial_scale", sort_direction="asc"), "works") == ["W1", "W2", "W3"]
    assert store.list_records("works", filters={"genre": "未分类原文\n第二行"})["total"] == 1
    assert "未分类原文\n第二行" in search.search()["facets"]["genres"]


def confirmed_run(documents, reference, document, *, approved=True, full=True, all_stages=True, all_commits=True):
    """Minimal real SQLite ledger shape; no provider or model is called."""
    version = document["latest_version_id"]
    length = documents.get_version(document["id"])["coverage"]["characters"]
    reference.save_section(document["id"], "settings", [], 0)
    with documents._connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS run_jobs(id TEXT PRIMARY KEY,document_id TEXT,version_id TEXT,status TEXT,total INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS run_chunks(run_id TEXT,id TEXT,start INTEGER,end INTEGER,status TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS run_stage_chunks(run_id TEXT,chunk_id TEXT,stage TEXT,status TEXT)")
        run = "R" + document["id"]
        db.execute("INSERT INTO run_jobs VALUES(?,?,?,?,1)", (run, document["id"], version, "approved" if approved else "needs_review"))
        db.execute("INSERT INTO run_chunks VALUES(?,?,?,?,?)", (run, "K", 0, length if full else max(1, length // 2), "completed"))
        for stage in ("plot", "characters", "settings", "language")[:4 if all_stages else 3]:
            db.execute("INSERT INTO run_stage_chunks VALUES(?,?,?,?)", (run, "K", stage, "completed"))
        for section in ("plot", "characters", "settings", "style", "rhythm", "opening", "era_background")[:7 if all_commits else 6]:
            db.execute("INSERT INTO reference_section_commits VALUES(?,?,?,?,?,?,?,?)", (document["id"], run, "K", section, "[]" if section in ("characters", "settings") else "{}", length, "2026-01-01", version))


def test_derived_completed_requires_current_approved_whole_text_four_stage_review(tmp_path, monkeypatch):
    store = DataStore(tmp_path / "catalog.db")
    documents = DocumentStore(tmp_path / "texts")
    reference = ReferenceAnalysisStore(tmp_path / "texts", documents=documents)
    links = CatalogLinks(store, documents, lambda: reference)
    docs = {}
    for identity in ("Wgood", "Wpartial", "Wreview", "Wstage", "Wcommit", "Wstale", "Wmanual", "Wempty"):
        store.create_record("works", {"作品ID": identity, "作品": identity})
        docs[identity] = documents.upload(identity + ".txt", "第1章 灯\n合成正文。".encode(), work_id=identity)
    for identity, options in (("Wgood", {}), ("Wpartial", {"full": False}), ("Wreview", {"approved": False}),
                              ("Wstage", {"all_stages": False}), ("Wcommit", {"all_commits": False}), ("Wstale", {})):
        confirmed_run(documents, reference, docs[identity], **options)
    reference.save_section(docs["Wmanual"]["id"], "settings", [{"category": "地点", "title": "车站", "content": "合成", "first_chapter": "1", "updates": []}], 0)
    reference.get(docs["Wempty"]["id"])
    with documents._connection() as db:
        db.execute("UPDATE doc_documents SET latest_version_id='other-current-version' WHERE id=?", (docs["Wstale"]["id"],))
    monkeypatch.setattr(store, "all_records", lambda *args: pytest.fail("no all-record load"))
    monkeypatch.setattr(documents, "get_version", lambda *args: pytest.fail("no full-text hydration"))
    result = links.list_records("works", analysis_completed=True, limit=1)
    assert result["total"] == 1 and result["items"][0]["id"] == "Wgood"
    assert links.list_records("works", analysis_completed=False)["total"] == 7
    assert links.list_records("works", has_analysis=True)["total"] == 7  # Historical compatibility.
    assert links.list_records("works", has_uploaded_text=True)["total"] == 8


def test_common_search_http_contract(tmp_path):
    app = create_app(tmp_path / "app", tmp_path / "missing-dist")
    app.state.runtime.store.create_record("works", {"作品ID": "W1", "作品": "合成", "作者": "测试作者", "作品题材": "科幻"})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/catalog/search", params={"q": "测试作者", "genre": "科幻", "limit": 1})
        assert response.status_code == 200, response.text
        assert ids(response.json(), "works") == ["W1"]
        assert set(response.json()["groups"]) == {"works", "characters", "cliches"}
