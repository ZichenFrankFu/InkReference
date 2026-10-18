"""Real document read/save scenarios: bounded responses, exact history and gaps."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from inkreference import chapter_preprocessing
from inkreference.database import DataError
from inkreference.documents import DocumentStore


@pytest.fixture
def store(tmp_path):
    return DocumentStore(tmp_path)


def upload(store, text, **options):
    document = store.upload("合成.txt", text.encode(), preserve_text=True, **options)
    return document, store.get_version(document["id"])


def assert_complete(version):
    assert "".join(item["text"] for item in version["chapters"]) == version["text"]
    assert "".join(item["text"] for item in version["chunks"]) == version["text"]
    assert version["coverage"]["characters"] == len(version["text"])
    assert version["coverage"]["covered_characters"] == len(version["text"])
    assert version["coverage"]["reconstructed_sha256"] == hashlib.sha256(version["text"].encode()).hexdigest()
    assert version["coverage"]["complete"] and version["coverage"]["chunks_complete"]


def test_cold_reopen_navigation_and_body_do_not_fetch_or_scan_entire_book(store, monkeypatch):
    text = "".join(f"第{i}章 出发\n" + ("同一段重复内容。\n" * 100) for i in range(1, 201))
    document, original = upload(store, text)
    reopened = DocumentStore(store.data_dir)

    def unexpected(*args, **kwargs):
        raise AssertionError("a small navigation/body read must not load or rescan the book")

    monkeypatch.setattr(reopened, "get_version", unexpected)
    monkeypatch.setattr(chapter_preprocessing, "chapter_proposals", unexpected)
    summary = reopened.get_version_summary(document["id"])
    index = reopened.chapter_index(document["id"])
    navigation = reopened.get_version_navigation(document["id"])
    page = reopened.get_chapter(document["id"], original["id"], "chapter-100", body_only=True)
    assert summary["characters"] == len(text)
    assert summary["chapter_count"] == len(index["chapters"]) == 200
    assert not {"text", "source_text", "chapters", "chunks", "diff"}.intersection(summary)
    assert len(json.dumps(summary, ensure_ascii=False).encode()) < 10000
    assert navigation["chapters"][99]["id"] == "chapter-100"
    assert all("text" not in item and "content" not in item for item in navigation["chapters"] + navigation["chunks"])
    assert page["editable_text"].startswith("同一段") and "第100章" not in page["editable_text"]


def test_legacy_snapshot_read_model_rebuild_never_changes_immutable_version(store):
    document, original = upload(store, "第一章 故事\n正文。\n求月票！\n第三章 后续\n尾标。")
    with sqlite3.connect(store.db_path) as connection:
        before = connection.execute("SELECT payload_json FROM doc_versions WHERE id=?", (original["id"],)).fetchone()[0]
        connection.execute("DELETE FROM doc_chapter_views")
        connection.execute("DELETE FROM doc_version_views")
    reopened = DocumentStore(store.data_dir)
    assert reopened.get_version_summary(document["id"])["id"] == original["id"]
    assert any(chapter.get("placeholder") for chapter in reopened.chapter_index(document["id"])["chapters"])
    with sqlite3.connect(store.db_path) as connection:
        after = connection.execute("SELECT payload_json FROM doc_versions WHERE id=?", (original["id"],)).fetchone()[0]
    assert after == before
    assert reopened.get_original(document["id"])[0] == original["source_text"].encode()


def test_incremental_save_scans_only_changed_chapter_and_preserves_all_evidence(store, monkeypatch):
    text = "".join(f"第{i}章 航行\n" + ("重复正文🌟。\n" * 50) + ("求月票！\n" if i == 20 else "") for i in range(1, 41))
    document, original = upload(store, text)
    calls = []
    scan = chapter_preprocessing.chapter_proposals

    def counted(chapter, options):
        calls.append(chapter["id"])
        return scan(chapter, options)

    monkeypatch.setattr(chapter_preprocessing, "chapter_proposals", counted)
    changed = store.edit_chapter(document["id"], original["id"], "chapter-10", "修改后的正文。\n\n第二段。")
    assert calls == ["chapter-10"]
    assert_complete(changed)
    assert changed["chapters"][9]["content"] == "\n修改后的正文。\n第二段。\n"
    assert changed["chapters"][19]["text"] == original["chapters"][19]["text"]
    assert store.get_version(document["id"], original["id"])["text"] == text
    assert store.get_original(document["id"])[0] == text.encode()
    findings = store.findings(document["id"], changed["id"])["items"]
    later = next(item for item in findings if item["chapter_id"] == "chapter-20")
    chapter = changed["chapters"][19]
    assert chapter["text"][later["start"]:later["end"]] == later["text"]
    assert all(chunk["chapter_index"] < 40 for chunk in changed["chunks"])


def test_body_only_normalized_unicode_mapping_and_crlf_save(store):
    source = "第一章 星舟\r\n🌟开航。\r\n\r\n　\r\n求月票！\r\n第二章 归来\r\n尾标。"
    document, original = upload(store, source)
    page = store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)
    assert page["editable_text"] == "🌟开航。\r\n求月票！"
    assert page["body_text"] is None
    mapping = page["source_offset_map"]
    chapter = original["chapters"][0]
    assert len(mapping) == len(page["editable_text"]) + 1
    for index, character in enumerate(page["editable_text"]):
        assert chapter["text"][mapping[index]] == character
    changed = store.edit_chapter_slice(document["id"], original["id"], "chapter-1",
                                       page["editable_start"], page["editable_end"],
                                       page["editable_text"].replace("开航", "返航"), page["expected_text"])
    assert changed["text"] == "第一章 星舟\r\n🌟返航。\r\n求月票！\r\n第二章 归来\r\n尾标。"
    assert_complete(changed)


@pytest.mark.parametrize("source", ["第一章 空章", "第一章 空章\r\n"])
def test_saving_empty_chapter_does_not_join_body_to_heading(store, source):
    document, original = upload(store, source)
    page = store.get_chapter(document["id"], original["id"], "chapter-1", body_only=True)
    assert page["editable_text"] == ""
    changed = store.edit_chapter_slice(document["id"], original["id"], "chapter-1",
                                       page["editable_start"], page["editable_end"], "补上的正文。", page["expected_text"])
    reparsed = store.resegment(document["id"], changed["id"], ["第N章"])
    assert reparsed["chapters"][0]["title"] == "空章"
    assert "补上的正文。" in reparsed["chapters"][0]["content"]
    assert_complete(reparsed)


def test_saving_middle_of_large_body_does_not_truncate_unseen_text(store):
    source = "第一章 长章\n" + "🌟很长的正文。\n" * 30000 + "最终尾标。\n第二章 后章\n后章尾标。"
    document, original = upload(store, source)
    page = store.get_chapter(document["id"], original["id"], "chapter-1", start=50000, limit=1000, body_only=True)
    changed = store.edit_chapter_slice(document["id"], original["id"], "chapter-1", page["editable_start"],
                                       page["editable_end"], "这页被替换。", page["expected_text"])
    start = original["chapters"][0]["start"] + page["editable_start"]
    end = original["chapters"][0]["start"] + page["editable_end"]
    assert changed["text"] == source[:start] + "这页被替换。" + source[end:]
    assert changed["text"].endswith("最终尾标。\n第二章 后章\n后章尾标。")
    assert_complete(changed)


def test_five_missing_items_are_empty_and_can_be_filled_with_title(store):
    document, original = upload(store, "第11章 前章\n十一正文。\n第17章 后章\n十七正文。")
    index = store.chapter_index(document["id"])
    missing = [item for item in index["chapters"] if item.get("placeholder")]
    assert [item["number"] for item in missing] == [12, 13, 14, 15, 16]
    assert len(original["chapters"]) == 2  # virtual directory preserves analysis snapshots
    for item in missing:
        page = store.get_chapter(document["id"], original["id"], item["id"], body_only=True)
        assert page["editable_text"] == page["expected_text"] == ""
        assert page["total"] == 0
    target = missing[2]
    filled = store.edit_chapter(document["id"], original["id"], target["id"], "补齐十四正文。", title="归航")
    assert "第14章 归航\n补齐十四正文。\n第17章" in filled["text"]
    remaining = [item["number"] for item in store.chapter_index(document["id"])["chapters"] if item.get("placeholder")]
    assert remaining == [12, 13, 15, 16]
    assert store.get_version(document["id"], original["id"])["text"] == original["text"]
    assert_complete(filled)
    with pytest.raises(DataError) as error:
        store.edit_chapter(document["id"], original["id"], target["id"], "过期编辑")
    assert error.value.status_code == 409


def test_number_restarts_and_explicit_volume_boundaries_do_not_invent_gaps(store):
    source = "第1章 初遇\n甲\n第2章 航行\n乙\n第二卷 归来\n第8章 新卷\n丙\n第1章 再次开始\n丁\n第2章 尾声\n尾标"
    document, _ = upload(store, source)
    index = store.chapter_index(document["id"])
    assert not any(chapter.get("placeholder") for chapter in index["chapters"])
    assert index["gaps"] == []


def test_findings_paging_counts_affected_chapters_once(store):
    document, original = upload(store, "第一章 初遇\n私用字\ue001与锟斤拷。\n求月票！\n第二章 归来\n尾部\ufffd。")
    all_items = store.findings(document["id"], original["id"])
    assert all_items["issue_chapter_count"] == 2
    assert all_items["total"] >= 4
    gathered = []
    start = 0
    while True:
        result = store.findings(document["id"], original["id"], start=start, limit=2)
        gathered.extend(result["items"])
        if result["next_start"] is None:
            break
        start = result["next_start"]
    assert gathered == all_items["items"]
    chapter_only = store.findings(document["id"], original["id"], chapter_id="chapter-1")
    assert chapter_only["issue_chapter_count"] == 1
    assert all(item["chapter_title"] == "第1章 初遇" for item in chapter_only["items"])


def test_clean_export_is_canonical_with_blank_missing_items_and_no_empty_body_lines(store):
    source = "第十一章 前章\n第一段。\n\n第二段。\n第十七章 后章\n末章。\n"
    document, original = upload(store, source)
    exported = store.formatted_export(document["id"], original["id"])
    assert exported == "第11章 前章\n第一段。\n第二段。\n第12章\n第13章\n第14章\n第15章\n第16章\n第17章 后章\n末章。\n"
    assert "\n\n" not in exported
    assert store.get_original(document["id"])[0] == source.encode()
    assert store.get_version(document["id"], original["id"])["text"] == source


def test_gui_ingestion_layout_normalizes_derived_bodies_without_changing_original(store):
    source = "第一章 开始\r\n第一段。\r\n\r\n第二段。\r\n"
    document, original = upload(store, source, normalize_layout=True)
    assert original["text"] == "第一章 开始\r\n第一段。\r\n第二段。\r\n"
    assert original["source_text"] == source
    assert store.get_original(document["id"])[0] == source.encode()
    appended = store.append(document["id"], original["id"], "补充.txt", "第二章 后续\n甲\n\n乙".encode(), preserve_text=True, normalize_layout=True)
    assert appended["text"].endswith("第二章 后续\n甲\n乙")
    assert "\n\n乙" in appended["source_text"]
    assert_complete(appended)


def test_stale_save_fails_before_expensive_preparation_and_preserves_latest(store, monkeypatch):
    document, original = upload(store, "第一章 原作\n正文。")
    changed = store.edit_chapter(document["id"], original["id"], "chapter-1", "第一处修改。")
    monkeypatch.setattr(store, "get_version", lambda *args: (_ for _ in ()).throw(AssertionError("stale save loaded full version")))
    with pytest.raises(DataError) as error:
        store.edit_chapter(document["id"], original["id"], "chapter-1", "过期修改。")
    assert error.value.status_code == 409
    assert store.get_version_summary(document["id"])["id"] == changed["id"]


def test_simultaneous_legacy_reads_rebuild_consistently_without_database_lock_errors(store):
    from concurrent.futures import ThreadPoolExecutor

    document, original = upload(store, "第一章 初遇\n正文。\n求月票！\n第三章 后续\n尾标。")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("DELETE FROM doc_chapter_views")
        connection.execute("DELETE FROM doc_version_views")
    reopened = DocumentStore(store.data_dir)
    readers = [lambda: reopened.get_version_summary(document["id"]),
               lambda: reopened.chapter_index(document["id"]),
               lambda: reopened.get_chapter(document["id"], original["id"], "chapter-1", body_only=True),
               lambda: reopened.findings(document["id"])] * 3
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda reader: reader(), readers))
    assert all(result.get("version_id", result.get("id")) == original["id"] for result in results)
    assert len(store.get_document(document["id"])["versions"]) == 1


def test_finding_context_is_actual_bounded_text_not_invented_explanation(store):
    source = "第一章 夜航\n" + "前" * 80 + "\ue001" + "后" * 80
    document, original = upload(store, source)
    finding = store.findings(document["id"], original["id"])["items"][0]
    assert finding["context_before"] == "前" * 40
    assert finding["context_after"] == "后" * 40
    assert finding["text"] == "\ue001"


def test_legacy_full_body_slice_preserves_exact_crlf_heading_separator(store):
    source = "第一章 星舟\r\n原文。\r\n第二章 归航\r\n尾标。"
    document, original = upload(store, source)
    chapter = original["chapters"][0]
    body_start = chapter["body_start"] - chapter["start"]
    changed = store.edit_chapter_slice(document["id"], original["id"], "chapter-1", body_start,
                                       len(chapter["text"]), "\r\n修改正文。\r\n", chapter["text"][body_start:])
    assert changed["text"] == "第一章 星舟\r\n修改正文。\r\n第二章 归航\r\n尾标。"
    assert_complete(changed)


@pytest.mark.parametrize("title", ["长" * 81, "归途。", "怎么回来？", "出发！"])
def test_unrecognized_missing_title_is_rejected_atomically_without_absorbing_body(store, title):
    document, original = upload(store, "第11章 前章\n十一正文。\n第17章 后章\n十七正文。")
    missing = next(item for item in store.chapter_index(document["id"])["chapters"] if item.get("number") == 12)
    with pytest.raises(DataError) as error:
        store.edit_chapter(document["id"], original["id"], missing["id"], "这段补写正文不能并入前章。", title=title)
    assert error.value.status_code == 422
    assert store.get_document(document["id"])["latest_version_id"] == original["id"]
    assert len(store.get_document(document["id"])["versions"]) == 1
    assert store.get_version(document["id"])["text"] == original["text"]
    assert len([item for item in store.chapter_index(document["id"])["chapters"] if item.get("placeholder")]) == 5


@pytest.mark.parametrize("title", ["长" * 81, "改名。", "怎么回来？", "出发！"])
def test_unrecognized_renamed_title_is_rejected_atomically_without_losing_chapter(store, title):
    document, original = upload(store, "第一章 初遇\r\n正文一。\r\n第二章 归航\r\n正文二。")
    with pytest.raises(DataError) as error:
        store.rename_chapter(document["id"], original["id"], "chapter-1", title)
    assert error.value.status_code == 422
    assert store.get_document(document["id"])["latest_version_id"] == original["id"]
    assert len(store.get_document(document["id"])["versions"]) == 1
    assert store.get_version(document["id"])["text"] == original["text"]


def test_valid_rename_keeps_exact_crlf_and_one_real_chapter(store):
    document, original = upload(store, "第一章 初遇\r\n正文一。\r\n第二章 归航\r\n正文二。")
    changed = store.rename_chapter(document["id"], original["id"], "chapter-1", "星舟归来")
    assert changed["text"] == "第一章 星舟归来\r\n正文一。\r\n第二章 归航\r\n正文二。"
    assert [item["number"] for item in changed["chapters"]] == [1, 2]
    assert_complete(changed)


def test_gap_presentation_counts_each_missing_item_without_warning_real_next_chapter_twice(store):
    document, original = upload(store, "第11章 前章\n十一正文。\n第17章 后章\n十七正文。")
    index = store.chapter_index(document["id"])
    assert index["placeholder_count"] == index["issue_chapter_count"] == 5
    real_next = next(item for item in index["chapters"] if item.get("number") == 17)
    assert real_next["issues"] == [] and real_next["issue_count"] == 0
    assert index["gaps"][0]["missing_count"] == 5
    assert store.findings(document["id"])["issue_chapter_count"] == 5
    assert store.version_summary(original)["placeholder_count"] == 5
    assert store.version_summary(original)["issue_chapter_count"] == 5
    assert store.get_version_summary(document["id"])["placeholder_count"] == 5
    assert store.get_version_summary(document["id"])["issue_chapter_count"] == 5
    target = next(item for item in index["chapters"] if item.get("number") == 14)
    filled = store.edit_chapter(document["id"], original["id"], target["id"], "补齐十四章。", title="归航")
    assert store.version_summary(filled)["placeholder_count"] == 4
    assert store.get_version_summary(document["id"])["placeholder_count"] == 4


def test_rename_empty_source_title_keeps_heading_crlf_separator(store):
    document, original = upload(store, "第一章\r\n正文一。\r\n第二章\r\n正文二。")
    changed = store.rename_chapter(document["id"], original["id"], "chapter-1", "补上的标题")
    assert changed["text"] == "第一章 补上的标题\r\n正文一。\r\n第二章\r\n正文二。"
    assert [item["number"] for item in changed["chapters"]] == [1, 2]
