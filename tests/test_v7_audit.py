"""Independent end-to-end invariants for title/body editing and reviewed cleanup."""
from __future__ import annotations

import hashlib

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore


def _assert_coverage(version):
    text = version["text"]
    assert "".join(item["text"] for item in version["chapters"]) == text
    assert "".join(item["text"] for item in version["chunks"]) == text
    assert version["coverage"]["reconstructed_sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_title_and_late_body_page_save_retains_all_other_source_spans(tmp_path):
    store = DocumentStore(tmp_path)
    source = "第1章 起航（求订阅）\r\n" + "重复🌟正文。\r\n" * 5000 + "尾部专属标记。\r\n第2章 以后\r\n另一章完整保留。"
    document = store.upload("合成.txt", source.encode(), preserve_text=True)
    old = store.get_version(document["id"])
    chapter = old["chapters"][0]
    page = store.get_chapter(document["id"], old["id"], chapter["id"], start=32000, limit=500, body_only=True)
    replacement = page["expected_text"].replace("重复", "修订", 1)
    saved = store.edit_chapter_slice(document["id"], old["id"], chapter["id"],
                                     page["editable_start"], page["editable_end"], replacement,
                                     page["expected_text"], title="远航🌟")
    before = chapter["text"][:page["editable_start"]]
    after = chapter["text"][page["editable_end"]:]
    new_marker = saved["chapters"][0]["raw_marker"]
    assert saved["chapters"][0]["text"] == new_marker + (before + replacement + after)[len(chapter["raw_marker"]):]
    assert saved["chapters"][0]["title"] == "远航🌟"
    assert saved["chapters"][1]["text"] == old["chapters"][1]["text"]
    assert "尾部专属标记。" in saved["text"]
    assert store.get_version(document["id"], old["id"])["text"] == source
    assert store.get_original(document["id"])[0] == source.encode()
    with pytest.raises(DataError) as conflict:
        store.edit_chapter_slice(document["id"], old["id"], chapter["id"],
                                 page["editable_start"], page["editable_end"], "错误覆盖", page["expected_text"], title="错误标题")
    assert conflict.value.status_code == 409
    assert store.get_document(document["id"])["latest_version_id"] == saved["id"]
    _assert_coverage(saved)


def test_heading_cleanup_and_body_cleanup_preserve_dialogue_and_unicode_provenance(tmp_path):
    store = DocumentStore(tmp_path)
    source = "第1章 🌟归途（求订阅、求月票）\n他说：“求月票是接头暗号。”\n实际剧情必须保留。\n求收藏！\n第2章 尾声\n后文完整。"
    document = store.upload("合成.txt", source.encode(), preserve_text=True)
    old = store.get_version(document["id"])
    page = store.get_chapter(document["id"], old["id"], "chapter-1", body_only=True)
    heading = [item for item in page["proposals"] if item.get("scope") == "heading" and item["removable"]]
    body = [item for item in page["proposals"] if item.get("scope") == "body" and item["removable"]]
    assert len(heading) == len(body) == 1
    assert heading[0]["text"] == "（求订阅、求月票）"
    assert body[0]["text"] == "求收藏！"
    for item in heading + body:
        assert old["chapters"][0]["text"][item["start"]:item["end"]] == item["text"]
    saved = store.apply_chapter_cleaning(document["id"], old["id"],
                                          [{"chapter_id": "chapter-1", "proposal_ids": [item["id"] for item in heading + body]}])
    assert saved["chapters"][0]["number"] == 1
    assert saved["chapters"][0]["title"] == "🌟归途"
    assert "他说：“求月票是接头暗号。”" in saved["text"]
    assert "实际剧情必须保留。" in saved["text"]
    assert saved["chapters"][1]["text"] == old["chapters"][1]["text"]
    assert store.get_version(document["id"], old["id"])["text"] == source
    assert store.get_original(document["id"])[0] == source.encode()
    _assert_coverage(saved)
