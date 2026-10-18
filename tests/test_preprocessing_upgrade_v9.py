"""Lazy upgrade of default note rules preserves explicit user settings."""
from __future__ import annotations

import pytest

import inkreference.chapter_preprocessing as rules
import inkreference.documents as documents
from inkreference.api import create_app
from fastapi.testclient import TestClient


SOURCE = "第1章 灯下\n" + "林舟沿着石阶走回车站。\n" * 30 + "PS：本章写完，明日见。\n"


def legacy_document(store, monkeypatch, keywords=None):
    with monkeypatch.context() as old:
        old.setattr(documents, "READ_MODEL_VERSION", 4)
        old.setattr(documents, "PREPROCESSING_VERSION", 1)
        old.setattr(documents, "AUTHOR_KEYWORDS", list(rules.LEGACY_AUTHOR_KEYWORDS))
        old.setattr(rules, "AUTHOR_KEYWORDS", list(rules.LEGACY_AUTHOR_KEYWORDS))
        item = store.upload("旧规则.txt", SOURCE.encode(), preserve_text=True)
        if keywords is not None:
            version = store.update_preprocess_settings(item["id"], item["latest_version_id"], author_keywords=keywords)
            item["latest_version_id"] = version["id"]
        assert not any("PS：" in finding["text"] for finding in store.findings(item["id"])["items"])
    return item


def test_opening_legacy_cache_adds_default_ps_without_a_new_snapshot(tmp_path, monkeypatch):
    store = documents.DocumentStore(tmp_path)
    item = legacy_document(store, monkeypatch)
    before = store.get_version(item["id"])
    index = store.chapter_index(item["id"])
    assert "PS：" in index["options"]["author_keywords"]
    findings = store.findings(item["id"])["items"]
    note = next(finding for finding in findings if finding["text"].startswith("PS："))
    page = store.get_chapter(item["id"], before["id"], note["chapter_id"], body_only=True)
    assert any(proposal["id"] == note["id"] for proposal in page["proposals"])
    assert index["chapters"][0]["first_body_issue_start"] == note["start"]
    assert store.get_version(item["id"]) == before
    assert len(store.get_document(item["id"])["versions"]) == 1
    with store._connection() as connection:
        assert connection.execute("SELECT layout_version FROM doc_version_views WHERE version_id=?", (before["id"],)).fetchone()[0] == documents.READ_MODEL_VERSION


@pytest.mark.parametrize("keywords", [[], ["仅此自定义关键词"]])
def test_legacy_custom_keywords_survive_cache_upgrade(tmp_path, monkeypatch, keywords):
    store = documents.DocumentStore(tmp_path)
    item = legacy_document(store, monkeypatch, keywords)
    assert store.chapter_index(item["id"])["options"]["author_keywords"] == keywords
    assert not any(finding["text"].startswith("PS：") for finding in store.findings(item["id"])["items"])


def test_removing_new_ps_default_is_not_undone_by_later_reads_or_edits(tmp_path):
    store = documents.DocumentStore(tmp_path)
    item = store.upload("自选规则.txt", SOURCE.encode(), preserve_text=True)
    assert any(finding["text"].startswith("PS：") for finding in store.findings(item["id"])["items"])
    saved = store.update_preprocess_settings(item["id"], item["latest_version_id"], author_keywords=list(rules.LEGACY_AUTHOR_KEYWORDS))
    with store._connection() as connection:
        connection.execute("UPDATE doc_version_views SET layout_version=4 WHERE version_id=?", (saved["id"],))
    assert "PS：" not in store.chapter_index(item["id"])["options"]["author_keywords"]
    assert not any(finding["text"].startswith("PS：") for finding in store.findings(item["id"])["items"])
    renamed = store.rename_chapter(item["id"], saved["id"], "chapter-1", "新灯下")
    assert "PS：" not in store.chapter_index(item["id"], renamed["id"])["options"]["author_keywords"]
    assert "PS：本章写完，明日见。" in renamed["text"]


def test_character_and_cliche_filter_query_contracts(tmp_path):
    with TestClient(create_app(tmp_path / "api", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        store = client.app.state.runtime.store
        for number, role in ((1, "主角"), (2, "配角")):
            store.create_record("characters", {"人物ID": f"P{number}", "角色": f"人物{number}", "角色定位": role})
        for number, trait, mechanism in ((1, "强大", "信息反转"), (2, "共情", "信息反转"), (3, "强大", "代价交换")):
            store.create_record("cliches", {"clichéID": f"C{number}", "cliché内容": f"桥段{number}", "人设塑造": trait, "剧情机制": mechanism})
        people = client.get("/api/records/characters?role=" + "主角")
        assert people.status_code == 200 and [row["id"] for row in people.json()["items"]] == ["P1"]
        page = client.get("/api/records/cliches", params={"character_trait": "强大", "plot_mechanism": "信息反转", "limit": 1})
        assert page.status_code == 200 and page.json()["total"] == 1
        assert page.json()["items"][0]["id"] == "C1"
        assert client.get("/api/records/works?character_trait=" + "强大").status_code == 422
