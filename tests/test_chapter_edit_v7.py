"""Atomic title/page editing, visible evidence and reviewed heading cleanup."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.database import DataError
from inkreference.documents import DocumentStore


@pytest.fixture
def store(tmp_path):
    return DocumentStore(tmp_path / "data")


def seed(store, text):
    document = store.upload("合成章节.txt", text.encode(), preserve_text=True)
    return document, store.get_version(document["id"])


def save_page(store, document, version, chapter_id="chapter-1", *, title=None, start=0, limit=20000, text=None):
    page = store.get_chapter(document["id"], version["id"], chapter_id, body_only=True, start=start, limit=limit)
    return store.edit_chapter_slice(document["id"], version["id"], chapter_id,
                                    page["editable_start"], page["editable_end"],
                                    page["editable_text"] if text is None else text,
                                    page["expected_text"], title=title)


def test_title_and_large_unicode_middle_page_save_once_without_touching_unseen_tail(store):
    source = "第1章 旧标题\r\n" + "🌙航行正文。\r\n" * 10000 + "第一章尾标。\r\n第2章 后续\r\n后续尾标。"
    document, original = seed(store, source)
    page = store.get_chapter(document["id"], original["id"], "chapter-1", start=20000, limit=700, body_only=True)
    saved = store.edit_chapter_slice(document["id"], original["id"], "chapter-1", page["editable_start"],
                                    page["editable_end"], "🌟修改后的页。\r\n", page["expected_text"], title="新🌙标题")
    expected = source[:page["editable_start"]] + "🌟修改后的页。\r\n" + source[page["editable_end"]:]
    expected = expected.replace("第1章 旧标题", "第1章 新🌙标题", 1)
    assert saved["text"] == expected
    assert saved["version_number"] == original["version_number"] + 1
    assert saved["parent_version_id"] == original["id"]
    assert saved["chapters"][0]["title"] == "新🌙标题"
    assert saved["chapters"][1]["text"] == original["chapters"][1]["text"]
    assert "".join(chapter["text"] for chapter in saved["chapters"]) == saved["text"]
    assert "".join(chunk["text"] for chunk in saved["chunks"]) == saved["text"]
    assert store.get_original(document["id"])[0] == source.encode()
    assert store.get_version(document["id"], original["id"])["text"] == source


def test_title_only_preserves_exact_legacy_body_and_uses_same_save_path(store):
    document, original = seed(store, "第一章 故事\r\n🌟正文。\r\n\r\n第二段。\r\n第二章 尾声\r\n末尾。")
    saved = save_page(store, document, original, title="出航")
    assert saved["text"] == original["text"].replace("第一章 故事", "第一章 出航", 1)
    assert saved["chapters"][0]["content"] == original["chapters"][0]["content"]
    assert saved["chapters"][0]["raw_marker"] == "第一章 出航\r"
    new_page = store.get_chapter(document["id"], saved["id"], "chapter-1", body_only=True)
    assert new_page["title"] == "出航" and "第一章" not in new_page["text"]


@pytest.mark.parametrize("title", ["问号？", "标题。", "超" * 81, "跨\n行"])
def test_invalid_title_rejects_both_changes_without_new_version(store, title):
    document, original = seed(store, "第1章 故事\n原正文。\n第2章 后续\n尾部。")
    with pytest.raises(DataError) as error:
        save_page(store, document, original, title=title, text="不应被保存的正文。")
    assert error.value.status_code == 422
    assert store.get_version(document["id"])["id"] == original["id"]
    assert store.get_version(document["id"])["text"] == original["text"]
    assert len(store.get_document(document["id"])["versions"]) == 1


def test_stale_title_body_and_proposal_saves_are_atomic_conflicts(store):
    document, original = seed(store, "第1章 星舟（求订阅）\n原正文。\n求月票！\n第2章 后续\n尾部。")
    old = store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)
    saved = save_page(store, document, original, title="星舟", text="新正文。")
    with pytest.raises(DataError) as stale_edit:
        save_page(store, document, original, title="旧编辑", text="旧正文")
    with pytest.raises(DataError) as stale_clean:
        store.apply_chapter_cleaning(document["id"], original["id"], [{"chapter_id": "chapter-1", "proposal_ids": old["proposal_ids"]}])
    assert stale_edit.value.status_code == stale_clean.value.status_code == 409
    with pytest.raises(DataError) as invalid_proposals:
        store.apply_chapter_cleaning(document["id"], saved["id"], [{"chapter_id": "chapter-1", "proposal_ids": old["proposal_ids"]}])
    assert invalid_proposals.value.status_code == 409
    assert store.get_version(document["id"])["id"] == saved["id"]


def test_heading_and_body_cleanup_select_exact_fragments_keep_heading_number_and_real_title(store):
    source = "第1章 星🌙舟（求订阅，求月票）\r\n原正文。\r\n求月票！\r\n第2章 后续\r\n尾部。"
    document, original = seed(store, source)
    page = store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)
    heading = next(proposal for proposal in page["proposals"] if proposal["scope"] == "heading")
    body = next(proposal for proposal in page["proposals"] if proposal["scope"] == "body" and proposal["removable"])
    assert heading["text"] == "（求订阅，求月票）"
    assert page["title"][heading["start"] - page["title_source_start"]:heading["end"] - page["title_source_start"]] == heading["text"]
    assert page["title_proposals"] == [heading]
    assert page["first_body_issue_start"] == body["start"]
    result = store.apply_chapter_cleaning(document["id"], original["id"], [{"chapter_id": "chapter-1", "proposal_ids": [heading["id"], body["id"]]}])
    assert result["chapters"][0]["number"] == 1
    assert result["chapters"][0]["title"] == "星🌙舟"
    assert "原正文。" in result["chapters"][0]["content"]
    assert "求订阅" not in result["text"] and "求月票" not in result["text"]
    assert len(result["chapters"]) == 2
    assert result["chapters"][1]["text"] == original["chapters"][1]["text"]
    assert store.findings(document["id"], original["id"])["issue_chapter_count"] == 1
    assert store.get_original(document["id"])[0] == source.encode()


@pytest.mark.parametrize("title", ["求订阅", "（求订阅）", "求订阅 求月票"])
def test_all_noise_title_cleanup_leaves_a_numbered_empty_title_not_deleted_chapter(store, title):
    document, original = seed(store, f"第1章 {title}\n必须保留的正文。\n第2章 后续\n尾部。")
    page = store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)
    selected = [item["id"] for item in page["heading_proposals"] if item["removable"]]
    cleaned = store.apply_chapter_cleaning(document["id"], original["id"], [{"chapter_id": "chapter-1", "proposal_ids": selected}])
    assert cleaned["chapters"][0]["title"] == ""
    assert cleaned["chapters"][0]["number"] == 1
    assert "必须保留的正文。" in cleaned["chapters"][0]["content"]
    assert store.chapter_index(document["id"])["chapters"][0]["display_label"] == "第1章"


def test_heading_keyword_inside_a_real_title_is_not_a_partial_solicitation(store):
    document, original = seed(store, "第1章 求订阅的旅人\n正文。\n第2章 月票大战\n后续。")
    for chapter in original["chapters"]:
        page = store.get_chapter(document["id"], original["id"], chapter["id"], body_only=True)
        assert page["heading_proposals"] == []


def test_purple_evidence_has_exact_title_and_far_body_spans_and_structural_context(store):
    source = "第1章 星\ue001舟\n" + "正文。" * 7000 + "远处\ufffd损坏。\n第2章 后续\n短。"
    document, original = seed(store, source)
    page = store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)
    title_flag = next(item for item in page["title_proposals"] if item["kind"] == "missing_glyph")
    assert title_flag["text"] == "\ue001" and title_flag["scope"] == "heading"
    assert page["title"][title_flag["start"] - page["title_source_start"]] == "\ue001"
    assert page["first_body_issue_start"] > page["end"]
    located = store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True, start=page["first_body_issue_start"], limit=100)
    assert located["text"].startswith("\ufffd")
    flag = next(item for item in located["proposals"] if item["start"] == located["first_body_issue_start"])
    assert original["chapters"][0]["text"][flag["start"]:flag["end"]] == "\ufffd"
    short = store.get_chapter(document["id"], original["id"], "chapter-2", body_only=True)
    assert short["structured_issues"] and short["structured_issues"][0]["kind"] == "length"
    assert short["structured_issues"][0]["location"] == "heading"
    assert short["proposals"] == []  # no invented character damage


def downgrade_cache(store, version):
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE doc_version_views SET layout_version=3 WHERE version_id=?", (version["id"],))
        connection.execute("UPDATE doc_chapter_views SET proposals_json='[]' WHERE version_id=?", (version["id"],))


def test_old_cache_upgrades_heading_proposals_before_reads_and_direct_other_chapter_edit(store):
    document, original = seed(store, "第1章 星舟（求订阅）\n第一章正文。\n第2章 后续\n第二章正文。")
    downgrade_cache(store, original)
    saved = save_page(store, document, original, "chapter-2", text="第二章已编辑。")
    page = store.get_chapter(document["id"], saved["id"], "chapter-1", body_only=True)
    assert page["heading_proposals"] and page["heading_proposals"][0]["text"] == "（求订阅）"
    assert store.get_version(document["id"], original["id"])["text"] == original["text"]
    downgrade_cache(store, saved)
    index = store.chapter_index(document["id"], saved["id"])
    assert index["chapters"][0]["heading_proposal_count"] == 1
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT layout_version FROM doc_version_views WHERE version_id=?", (saved["id"],)).fetchone()[0] == 4


def test_rebuild_views_repairs_partial_rows_without_mutating_sources_or_versions(store, monkeypatch):
    document, original = seed(store, "第1章 星舟（求订阅）\n正文。\n第2章 后续\n尾部。")
    with sqlite3.connect(store.db_path) as connection:
        before = connection.execute("SELECT payload_json FROM doc_versions WHERE id=?", (original["id"],)).fetchone()[0]
        connection.execute("DELETE FROM doc_chapter_views WHERE version_id=? AND chapter_id='chapter-1'", (original["id"],))
    rebuilt = store.rebuild_read_views(document["id"])
    assert rebuilt["id"] == original["id"] and "chapters" not in rebuilt
    assert store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)["heading_proposals"]
    with pytest.raises(DataError) as invalid:
        store.rebuild_read_views(document["id"], "absent-version")
    assert invalid.value.status_code == 404
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT payload_json FROM doc_versions WHERE id=?", (original["id"],)).fetchone()[0] == before
        assert connection.execute("SELECT COUNT(*) FROM doc_chapter_views WHERE version_id=?", (original["id"],)).fetchone()[0] == 2
    original_writer = store._write_views
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic interrupted rebuild")
    monkeypatch.setattr(store, "_write_views", fail)
    with pytest.raises(RuntimeError):
        store.rebuild_read_views(document["id"])
    monkeypatch.setattr(store, "_write_views", original_writer)
    assert store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)["heading_proposals"]
    assert store.get_original(document["id"])[0] == original["text"].encode()


def test_http_same_save_endpoint_accepts_title_and_rejects_invalid_atomic_update(tmp_path):
    with TestClient(create_app(tmp_path / "api", tmp_path / "dist"), base_url="http://127.0.0.1") as client:
        store = client.app.state.runtime.studio.documents
        document, original = seed(store, "第1章 旧标题\n原正文。\n第2章 后续\n尾部。")
        prefix = f"/api/studio/documents/{document['id']}"
        page = client.get(f"{prefix}/versions/{original['id']}/chapters/chapter-1?body_only=true").json()
        payload = {"base_version_id": original["id"], "chapter_id": "chapter-1", "start": page["editable_start"],
                   "end": page["editable_end"], "text": "新正文。", "expected_text": page["expected_text"], "title": "新标题"}
        response = client.post(prefix + "/chapters/edit-slice?summary=true", json=payload)
        assert response.status_code == 201, response.text
        summary = response.json()
        assert "text" not in summary and summary["version_number"] == 2
        assert store.get_version(document["id"])["text"].startswith("第1章 新标题\n新正文。")
        assert client.post(prefix + "/chapters/edit-slice?summary=true", json=payload).status_code == 409
        current = store.get_chapter(document["id"], summary["id"], "chapter-1", body_only=True)
        payload.update(base_version_id=summary["id"], title="坏标题？", expected_text=current["expected_text"], start=current["editable_start"], end=current["editable_end"])
        assert client.post(prefix + "/chapters/edit-slice?summary=true", json=payload).status_code == 422
        assert store.get_version(document["id"])["id"] == summary["id"]
