"""Explicit catalog binding, versioned completion and chapter fact projections."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.catalog_links import CatalogLinks, reference_projection
from inkreference.database import DataError, DataStore
from inkreference.documents import DocumentStore
from inkreference.reference_analysis import ANALYSIS_HISTORY_LIMIT, ReferenceAnalysisStore


def fixture(tmp_path):
    store = DataStore(tmp_path / "inkreference.sqlite3")
    docs = DocumentStore(tmp_path)
    reference = ReferenceAnalysisStore(tmp_path, documents=docs)
    links = CatalogLinks(store, docs, lambda: reference)
    work = store.create_record("works", {"作品ID": "W01", "作品": "同名作品"})
    document = docs.upload("novel.txt", "第1章 入城\n林青进城。\n第2章 出城\n林青离开。".encode(), title="同名作品")
    return store, docs, reference, links, work, document


def test_link_is_explicit_and_same_title_never_merges_editions(tmp_path):
    store, docs, reference, links, first, document = fixture(tmp_path)
    assert links.get(document["id"])["work_id"] is None
    linked = links.link(document["id"], expected_work_id=None,
                        expected_version_id=document["latest_version_id"], new_work={"title": "同名作品"}, complete=True)
    assert linked["work_id"] != first["id"]
    assert linked["work"]["title"] == first["values"]["作品"]
    assert store.stats()["works"] == 2
    assert links.detail(first["id"])["documents"] == []
    assert links.detail(linked["work_id"])["documents"][0]["id"] == document["id"]
    assert linked["completion"]["version_id"] == document["latest_version_id"]


def test_failed_link_rolls_back_new_work_and_completion(tmp_path):
    store, docs, reference, links, work, document = fixture(tmp_path)
    with pytest.raises(DataError) as conflict:
        links.link(document["id"], expected_work_id=None, expected_version_id="stale-version",
                   new_work={"title": "不应被创建"}, complete=True)
    assert conflict.value.status_code == 409
    assert store.stats()["works"] == 1
    assert links.get(document["id"])["completion"] is None
    with pytest.raises(DataError):
        links.link(document["id"], expected_work_id=None, expected_version_id=document["latest_version_id"], work_id="missing")
    assert links.get(document["id"])["work_id"] is None


def test_completion_pins_version_and_unlink_keeps_text_and_reference(tmp_path):
    store, docs, reference, links, work, document = fixture(tmp_path)
    state = reference.save_section(document["id"], "characters", [{"name": "林青", "appearance": [{"chapter": "第1章", "text": "黑发"}]}], 0)
    before = docs.get_version(document["id"])
    links.link(document["id"], expected_work_id=None, expected_version_id=before["id"], work_id=work["id"], complete=True)
    after = docs.create_literal_version(document["id"], before["id"], before["text"] + "\n新句子。")
    assert links.get(document["id"])["completion"]["version_id"] == before["id"]
    with pytest.raises(DataError) as conflict:
        links.link(document["id"], expected_work_id=None, expected_version_id=after["id"], work_id=work["id"])
    assert conflict.value.status_code == 409
    detached = links.link(document["id"], expected_work_id=work["id"], expected_version_id=after["id"], work_id=None)
    assert detached["work_id"] is None and detached["completion"] is None
    assert reference.get(document["id"])["sections"] == state["sections"]
    assert docs.get_version(document["id"])["text"] == after["text"]
    with pytest.raises(DataError) as missing_link:
        links.analysis(work["id"], document["id"])
    assert missing_link.value.status_code == 409


def test_metadata_filters_do_not_hydrate_text_or_initialize_analysis(tmp_path, monkeypatch):
    store, docs, reference, links, work, document = fixture(tmp_path)
    empty = store.create_record("works", {"作品ID": "W02", "作品": "另一版本"})
    links.link(document["id"], expected_work_id=None, expected_version_id=document["latest_version_id"], work_id=work["id"])
    reference.get(document["id"])  # An empty, visited analysis is not content.
    def forbid(*args, **kwargs):
        raise AssertionError("list metadata may not hydrate text/reference")
    monkeypatch.setattr(docs, "get_version", forbid)
    monkeypatch.setattr(reference, "get", forbid)
    assert links.filter_work_ids(has_text=True) == [work["id"]]
    assert links.filter_work_ids(has_text=False) == [empty["id"]]
    assert links.filter_work_ids(has_analysis=True) == []
    assert links.detail(work["id"])["documents"][0]["analysis"] is None
    reference.save_section(document["id"], "settings", [{"title": "城门", "updates": [{"chapter": "第1章", "text": "有守卫"}]}], 0)
    assert links.filter_work_ids(has_analysis=True) == [work["id"]]
    detail = links.detail(work["id"])
    assert detail["documents"][0]["analysis"]["counts"] == {"plot": 0, "characters": 0, "settings": 1}
    assert "sections" not in detail["documents"][0]["analysis"]


def test_rich_fact_projection_preserves_own_experiences_and_two_clocks():
    sections = {"plot": {"logline": "旧城故事", "epochs": [{"title": "城中", "periods": [{"time": "2033年", "events": [
        {"subject": "林青", "name": "入城", "description": "进入旧城", "first_chapter": "第1章", "time_marker": "2033年3月"},
        {"subject": "林青", "name": "离城", "description": "离开旧城", "first_chapter": "第十二章", "time_markers": ["2033年4月"]},
        {"subject": "林青的朋友", "name": "无关事件", "description": "别人的经历", "first_chapter": "第2章"},
        {"subject": "陆舟", "name": "等待", "description": "在门口等待", "first_chapter": "第2章"},
    ]}]}]}, "characters": [
        {"name": "林青", "first_chapter": "第1章", "first_seen_at": "2033年3月", "experiences": [], "appearance": [
            {"chapter": "第1章", "text": "黑发"}, {"chapter": "第十二章", "text": "白发"}, {"chapter": "未知", "text": "待定位事实"}]},
        {"name": "陆舟", "experiences": [{"chapter": "第12章", "text": "人工维护经历"}]},
        {"name": "后出场", "first_chapter": "第20章"},
    ], "settings": [{"title": "城门", "first_introduced_at": "2033年", "updates": [
        {"chapter": "第2章", "text": "守卫换班"}, {"chapter": "第十二章", "text": "门被封锁"}]}]}
    original = copy.deepcopy(sections)
    projected = reference_projection(sections, 2)
    assert [item["name"] for item in projected["characters"]] == ["林青", "陆舟"]
    assert projected["characters"][0]["experiences"] == [{"chapter": "第1章", "text": "进入旧城"}]
    assert projected["characters"][1]["experiences"] == []  # Later own facts do not trigger fallback.
    assert len(projected["characters"][0]["appearance"]) == 2
    assert projected["characters"][0]["first_seen_at"] == "2033年3月"
    assert len(projected["settings"][0]["updates"]) == 1
    assert projected["plot"]["epochs"][0]["periods"][0]["events"][0]["time_marker"] == "2033年3月"
    assert sections == original


def test_analysis_history_is_bounded_and_restore_keeps_commit_ledger(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    state = store.get("document")
    for i in range(ANALYSIS_HISTORY_LIMIT + 8):
        state = store.save_section("document", "settings", [{"title": "城门", "content": str(i)}], state["revision"])
    history = store.list_history("document")
    assert len(history) == ANALYSIS_HISTORY_LIMIT
    assert history[0]["revision"] == state["revision"]
    with pytest.raises(DataError) as missing:
        store.restore("document", 0, state["revision"])
    assert missing.value.status_code == 404
    restored = store.restore("document", history[-1]["revision"], state["revision"])
    assert restored["revision"] == state["revision"] + 1
    assert len(ReferenceAnalysisStore(tmp_path).list_history("document")) == ANALYSIS_HISTORY_LIMIT


def test_reading_current_analysis_does_not_rebuild_history_ledger(tmp_path, monkeypatch):
    store = ReferenceAnalysisStore(tmp_path)
    state = store.save_section("document", "settings", [{"title": "城门"}], 0)
    def forbidden(*args):
        raise AssertionError("normal reads must not serialize the entire approval ledger")
    monkeypatch.setattr(store, "_ledger", forbidden)
    assert store.get("document") == state


def test_catalog_http_contract_is_lazy_and_requires_exact_version(tmp_path):
    application = create_app(data_dir=tmp_path, static_dir=tmp_path / "absent")
    with TestClient(application, base_url="http://127.0.0.1") as client:
        assert application.state.runtime._studio is None
        document = client.post("/api/studio/documents", files={"file": ("book.txt", "第1章 入城\n林青进城。".encode(), "text/plain")}).json()
        assert "id" in document, document
        path = f"/api/studio/documents/{document['id']}/catalog"
        assert client.get(path).json()["work_id"] is None
        invalid = client.put(path, json={"new_work": {"title": "城中"}, "expected_work_id": None, "expected_version_id": "old", "complete": True})
        assert invalid.status_code == 409
        linked = client.put(path, json={"new_work": {"title": "城中"}, "expected_work_id": None, "expected_version_id": document["latest_version_id"], "complete": True})
        assert linked.status_code == 200, linked.text
        work_id = linked.json()["work_id"]
        assert client.get("/api/studio/catalog/works").json()["items"][0]["id"] == work_id
        assert client.get(f"/api/studio/catalog/works/{work_id}").json()["counts"]["documents"] == 1
