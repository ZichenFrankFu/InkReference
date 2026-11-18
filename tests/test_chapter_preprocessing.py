"""Reference-tab chapter parity: exact spans, reversible review and append sources."""
import hashlib
import sqlite3

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.text_processing import split_chapters


@pytest.fixture
def store(tmp_path):
    return DocumentStore(tmp_path)


def upload(store, source):
    document = store.upload("章节.txt", source.encode())
    return document["id"], store.get_version(document["id"])


def assert_lossless(version):
    assert "".join(c["text"] for c in version["chapters"]) == version["text"]
    assert "".join(c["text"] for c in version["chunks"]) == version["text"]
    assert version["coverage"]["complete"]
    assert version["coverage"]["chunks_complete"]


def test_append_preserves_all_sources_and_old_edits_and_rejects_stale_base(store):
    raw = "第一章 星夜\n原正文。\n".encode()
    document = store.upload("原件.txt", raw)
    first = store.get_version(document["id"])
    edited = store.edit_chapter(document["id"], first["id"], "chapter-1", "人工修改🌟。")
    append_raw = "第二章 归航\r\n接续正文。".encode("gb18030")
    appended = store.append(document["id"], edited["id"], "接续.txt", append_raw, "gb18030")
    assert appended["text"] == "第一章 星夜\n人工修改🌟。\n第二章 归航\n接续正文。"
    assert "原正文" in appended["source_text"]
    assert "接续正文" in appended["source_text"]
    assert store.get_original(document["id"])[0] == raw
    sources = store.list_sources(document["id"])
    assert len(sources) == 2
    assert store.get_source_original(document["id"], sources[1]["id"]) == (append_raw, "接续.txt")
    assert sources[1]["original_sha256"] == hashlib.sha256(append_raw).hexdigest()
    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE doc_sources SET original=?", (b"changed",))
    with pytest.raises(DataError) as caught:
        store.append(document["id"], first["id"], "过期.txt", "第三章 尾声\n过期".encode())
    assert caught.value.status_code == 409
    assert len(store.list_sources(document["id"])) == 2
    assert_lossless(appended)
    # A subsequent legacy version must retain all appended source provenance.
    next_version = store.create_version(document["id"], appended["id"], {})
    assert next_version["source_text"] == appended["source_text"]


def test_failed_append_decode_leaves_document_and_sources_untouched(store):
    document_id, first = upload(store, "第一章 原作\n正文。")
    with pytest.raises(DataError):
        store.append(document_id, first["id"], "错误.txt", b"\xff", "utf-8")
    assert len(store.list_sources(document_id)) == 1
    assert store.get_document(document_id)["latest_version_id"] == first["id"]


def test_inline_candidates_are_chapter_local_exact_and_individually_reversible(store):
    source = "第一章 星夜\n🌟正文。\n他说：“求月票只是暗号。”\n\n求月票，感谢书友！\n第二章 尾声\n尾部锟斤拷和�需核对。\n求订阅！"
    document_id, first = upload(store, source)
    index = store.chapter_index(document_id, first["id"])
    assert len(index["chapters"]) == 2
    assert {i["kind"] for i in index["chapters"][0]["issues"]} == {"author_note"}
    assert {i["kind"] for i in index["chapters"][1]["issues"]} >= {"author_note", "mojibake", "missing_glyph"}
    page = store.get_chapter(document_id, first["id"], "chapter-1", start=3, limit=9)
    assert page["text"] == first["chapters"][0]["text"][3:12]
    proposed = [p for p in page["proposals"] if p["removable"]]
    assert len(proposed) == 1
    assert proposed[0]["text"] == "求月票，感谢书友！"
    assert first["chapters"][0]["text"][proposed[0]["start"]:proposed[0]["end"]] == proposed[0]["text"]
    cleaned = store.apply_chapter_cleaning(document_id, first["id"], [{"chapter_id": "chapter-1", "proposal_ids": [proposed[0]["id"]]}])
    assert "求月票，感谢书友！" not in cleaned["text"]
    assert "他说：“求月票只是暗号。”" in cleaned["text"]
    assert "求订阅！" in cleaned["text"]  # unselected chapter remains exact
    assert store.get_version(document_id, first["id"])["text"] == source
    restored = store.restore_version(document_id, cleaned["id"], first["id"])
    assert restored["text"] == source
    assert_lossless(restored)


def test_missing_glyph_is_diagnosed_never_removed_by_false_candidate(store):
    document_id, first = upload(store, "第一章 夜\n缺失字符�和乱码锟斤拷。")
    page = store.get_chapter(document_id, first["id"], "chapter-1")
    assert page["proposals"] and all(not p["removable"] for p in page["proposals"])
    with pytest.raises(DataError) as caught:
        store.apply_chapter_cleaning(document_id, first["id"], [{"chapter_id": "chapter-1", "proposal_ids": [page["proposals"][0]["id"]]}])
    assert caught.value.status_code == 409


def test_edit_only_addressed_body_keeps_prefix_and_other_chapters_exact(store):
    source = "作者序言\n第一章 初见\n第一章正文。\n\n第二章 星夜\n第二章正文。\n第三章 归航\n结尾。"
    document_id, first = upload(store, source)
    edited = store.edit_chapter(document_id, first["id"], "chapter-3", "\n人工🌟&amp;字面内容。\n")
    old = first["chapters"][2]
    assert edited["text"] == source[:old["body_start"]] + "\n人工🌟&amp;字面内容。\n" + source[old["end"]:]
    assert edited["text"].startswith("作者序言\n第一章 初见\n第一章正文。\n\n")
    assert_lossless(edited)
    with pytest.raises(DataError) as caught:
        store.edit_chapter(document_id, first["id"], "chapter-3", "过期修改")
    assert caught.value.status_code == 409


def test_multiformat_detection_duplicate_gaps_and_length_outliers_preserve_every_byte(store):
    source = "第一章 开头\n" + "故事。" * 100 + "\n第三章 跳号\n短\n第三章 同号作品\n" + "正文。" * 120 + "\nChapter 4 End\n末尾。"
    document_id, first = upload(store, source)
    index = store.chapter_index(document_id)
    assert len(index["chapters"]) == 5
    assert len({c["id"] for c in index["chapters"]}) == 5
    actual = [chapter for chapter in index["chapters"] if not chapter.get("placeholder")]
    assert len(actual) == 4
    assert index["chapters"][1]["number"] == 2
    assert index["chapters"][1]["placeholder"] is True
    assert index["gaps"][0]["missing_numbers"] == [2]
    assert "duplicate" in {i["kind"] for i in actual[1]["issues"]}
    assert "length" in {i["kind"] for i in actual[1]["issues"]}
    assert first["text"] == source
    assert_lossless(first)


def test_custom_template_regex_selection_and_settings_are_version_pinned(store):
    source = "卷一 初见\n小说正文。\n卷二 尾声\n自定义水印\n粉丝求续更！"
    document_id, first = upload(store, source)
    second = store.resegment(document_id, first["id"], ["卷N"], [{"name": "卷N", "format": "卷N", "enabled": True}])
    assert len(second["chapters"]) == 2
    assert second["text"] == source
    third = store.update_preprocess_settings(document_id, second["id"], ["粉丝求续更"], [{"name": "测试水印", "pattern": "自定义.*印", "enabled": True}])
    page = store.get_chapter(document_id, third["id"], "chapter-2")
    assert {p["kind"] for p in page["proposals"]} == {"noise", "author_note"}
    assert store.get_version(document_id, first["id"])["options"]["author_keywords"] != ["粉丝求续更"]
    fourth = store.resegment(document_id, third["id"], ["自定义正则"], [{"name": "自定义正则", "regex": r"^卷([一二]) ([^\n]+)$", "enabled": True}])
    assert len(fourth["chapters"]) == 2
    assert_lossless(fourth)


def test_rename_insert_delete_and_restore_are_versioned(store):
    document_id, first = upload(store, "第一章 星夜\n原正文。\n第二章 尾声\n原尾声。")
    renamed = store.rename_chapter(document_id, first["id"], "chapter-1", "新章名")
    assert renamed["text"].startswith("第一章 新章名\n原正文。")
    inserted = store.insert_chapter(document_id, renamed["id"], "chapter-1", "第一章 插曲", "新增正文。")
    assert len(inserted["chapters"]) == 3
    assert "第一章 插曲\n新增正文。\n第二章" in inserted["text"]
    deleted = store.delete_chapters(document_id, inserted["id"], ["chapter-2"])
    assert deleted["text"] == renamed["text"]
    restored = store.restore_version(document_id, deleted["id"], first["id"])
    assert restored["text"] == first["text"]
    for version in (renamed, inserted, deleted, restored):
        assert_lossless(version)


def test_custom_regex_timeout_is_atomic(store):
    document_id, first = upload(store, "第一章 夜\n" + "a" * 20000 + "!")
    with pytest.raises(DataError) as caught:
        store.update_preprocess_settings(document_id, first["id"], garble_rules=[{"name": "过慢规则", "pattern": "(a+)+$", "enabled": True}])
    assert caught.value.status_code == 422
    assert "超时" in caught.value.detail
    assert store.get_document(document_id)["latest_version_id"] == first["id"]


def test_unnumbered_heading_lookahead_keeps_first_body_character():
    source = "初见之夜\n　正文首字。\n白塔重逢\n　尾字。\n归来以后\n　终字。"
    parsed = split_chapters(source)
    assert parsed["pattern"] == "缩进对比"
    assert len(parsed["chapters"]) == 3
    assert "".join(c["text"] for c in parsed["chapters"]) == source
    assert "正文首字" in parsed["chapters"][0]["content"]


def test_empty_reference_collection_has_no_fake_uploaded_text(store):
    document = store.create_collection("世界设定集")
    assert document["format"] == "collection"
    assert document["original_bytes"] == 0
    version = store.get_version(document["id"])
    assert version["text"] == version["source_text"] == ""
    assert version["chapters"] == []
    assert_lossless(version)
    assert store.chapter_index(document["id"])["chapters"] == []
    assert store.get_original(document["id"])[0] == b""


def test_unrecognized_insert_heading_is_rejected_without_a_version(store):
    document_id, first = upload(store, "第一章 初见\n原正文。\n第二章 尾声\n原尾声。")
    with pytest.raises(DataError) as caught:
        store.insert_chapter(document_id, first["id"], "chapter-1", "普通正文而非章节标题。", "新增正文")
    assert caught.value.status_code == 422
    assert store.get_document(document_id)["latest_version_id"] == first["id"]
    assert len(store.get_document(document_id)["versions"]) == 1
    assert store.get_version(document_id)["text"] == first["text"]


def test_literal_reference_source_keeps_entities_crlf_emoji_and_provenance(store):
    document = store.create_collection("原始设定")
    first = store.get_version(document["id"])
    source = "# 魔法与符号\r\n🌟 &amp; &quot;原样内容&quot;\r\n第二行。"
    literal = store.create_literal_version(document["id"], first["id"], source)
    assert literal["text"] == source
    assert literal["source_text"] == first["source_text"] == ""
    assert literal["changes"] == [{"stage": "reference_source", "count": 1}]
    assert_lossless(literal)
    assert store.get_original(document["id"])[0] == b""
    with pytest.raises(DataError) as caught:
        store.create_literal_version(document["id"], first["id"], source)
    assert caught.value.status_code == 409


def test_document_current_count_tracks_latest_without_changing_source_count(store):
    source = "第一章 初见\n原始正文。"
    document_id, first = upload(store, source)
    assert store.get_document(document_id)["current_characters"] == len(source)
    appended = store.append(document_id, first["id"], "接续.txt", "第二章 星夜\n🌟接续内容。".encode())
    metadata = store.get_document(document_id)
    assert metadata["current_characters"] == len(appended["text"])
    assert metadata["source_characters"] == len(source)
    collection = store.create_collection("空设定集")
    assert collection["current_characters"] == collection["source_characters"] == 0
    text = "🌟设定 &amp; 原样内容。\x00后续正文。"
    prepared = store.create_literal_version(collection["id"], collection["latest_version_id"], text)
    metadata = store.get_document(collection["id"])
    assert metadata["current_characters"] == len(text)
    assert metadata["source_characters"] == 0
    listed = {item["id"]: item for item in store.list_documents()}
    assert listed[document_id]["current_characters"] == len(appended["text"])
    assert listed[collection["id"]]["current_characters"] == len(prepared["text"])
    assert store.get_original(collection["id"])[0] == b""
