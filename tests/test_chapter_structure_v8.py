"""Reviewed chapter replacement, deletion numbering and bounded history."""
from __future__ import annotations

import sqlite3
import json

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore, HISTORY_LIMIT, READ_VIEW_LIMIT
from inkreference.volumes import VolumeStore


@pytest.fixture
def store(tmp_path):
    return DocumentStore(tmp_path / "data")


def seed(store, source):
    document = store.upload("合成正文.txt", source.encode(), preserve_text=True)
    return document, store.get_version(document["id"])


def complete(version):
    assert "".join(chapter["text"] for chapter in version["chapters"]) == version["text"]
    assert "".join(chunk["text"] for chunk in version["chunks"]) == version["text"]
    assert version["coverage"]["complete"]


def test_empty_duplicate_is_proposed_then_explicitly_replaced_without_losing_body(store):
    body = "🌙" + "正文" * 1818 + "。"
    assert len(body) == 3638
    original = "第1章 前章\r\n前章正文。\r\n第2章 火灶房\r\n第2章\r\n" + body + "\r\n第3章 尾章\r\n尾章正文。"
    document, first = seed(store, original)
    preview = store.preview_resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True)
    assert preview["conflict"]
    assert preview["old"]["chapter_count"] == 4
    assert preview["new"]["chapter_count"] == 3
    assert preview["old"]["empty_chapter_count"] == 1
    assert preview["new"]["empty_chapter_count"] == 0
    assert preview["duplicate_repairs"][0]["body_characters"] == 3638
    assert preview["duplicate_repairs"][0]["title"] == "火灶房"
    assert len(store.get_document(document["id"])["versions"]) == 1
    with pytest.raises(DataError) as error:
        store.resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True, require_confirmation=True)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "chapter_resegment_conflict"
    kept = store.resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True,
                           preview_token=preview["preview_token"], conflict_policy="keep_old", require_confirmation=True)
    assert kept["id"] == first["id"]
    assert len(store.get_document(document["id"])["versions"]) == 1
    replaced = store.resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True,
                               preview_token=preview["preview_token"], conflict_policy="use_new", require_confirmation=True)
    assert len(replaced["chapters"]) == 3
    assert replaced["chapters"][1]["title"] == "火灶房"
    assert replaced["chapters"][1]["content"].strip() == body
    assert store.get_version(document["id"], first["id"])["text"] == original
    assert store.get_original(document["id"])[0] == original.encode()
    complete(replaced)


def test_real_same_number_bodies_are_never_merge_candidates(store):
    document, first = seed(store, "第2章 上半\n真实正文A。\n第2章 下半\n真实正文B。")
    preview = store.preview_resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True)
    assert preview["duplicate_repairs"] == []
    assert preview["new"]["chapter_count"] == 2
    result = store.resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True,
                            conflict_policy="use_new", preview_token=preview["preview_token"], require_confirmation=True)
    assert result["text"] == first["text"]


def test_empty_chapter_with_different_number_is_not_removed(store):
    document, first = seed(store, "第1章 待补\n第2章 正文\n真实内容。")
    preview = store.preview_resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True)
    assert preview["duplicate_repairs"] == []
    assert preview["new"]["empty_chapter_count"] == 1


def test_duplicate_empty_chain_preserves_last_body_and_meaningful_title(store):
    document, first = seed(store, "第2章 火灶房\n第2章\n第2章\n原始🌙正文。")
    preview = store.preview_resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True)
    assert len(preview["duplicate_repairs"][0]["old_chapter_ids"]) == 3
    result = store.resegment(document["id"], first["id"], ["第N章"], repair_duplicates=True,
                            preview_token=preview["preview_token"], conflict_policy="use_new", require_confirmation=True)
    assert result["text"] == "第2章 火灶房\n原始🌙正文。"
    complete(result)


def test_preview_cannot_be_reused_for_changed_rules_or_stale_version(store):
    document, first = seed(store, "第1章 起点\n正文。\n第2章 终点\n尾声。")
    preview = store.preview_resegment(document["id"], first["id"], ["第N章"])
    with pytest.raises(DataError) as changed:
        store.resegment(document["id"], first["id"], ["第N回"], preview_token=preview["preview_token"],
                        conflict_policy="use_new", require_confirmation=True)
    assert changed.value.status_code == 409
    assert changed.value.detail["code"] == "chapter_preview_changed"
    store.rename_chapter(document["id"], first["id"], "chapter-1", "已修改")
    with pytest.raises(DataError) as stale:
        store.resegment(document["id"], first["id"], ["第N章"], preview_token=preview["preview_token"],
                        conflict_policy="use_new", require_confirmation=True)
    assert stale.value.status_code == 409


def test_switching_formats_replaces_directory_instead_of_unioning_it(store):
    source = "卷一 开始\n第1章 正文\n这一章。\n卷二 结束\n第2章 终章\n另一章。"
    document, first = seed(store, source)
    custom = [{"name": "卷N", "format": "卷N"}]
    preview = store.preview_resegment(document["id"], first["id"], ["卷N"], custom)
    selected = store.resegment(document["id"], first["id"], ["卷N"], custom,
                              preview_token=preview["preview_token"], conflict_policy="use_new", require_confirmation=True)
    assert len(selected["chapters"]) == 2
    assert all(chapter["pattern"] == "卷N" for chapter in selected["chapters"])
    preview = store.preview_resegment(document["id"], selected["id"], ["第N章"])
    again = store.resegment(document["id"], selected["id"], ["第N章"],
                           preview_token=preview["preview_token"], conflict_policy="use_new", require_confirmation=True)
    assert len(again["chapters"]) == 3  # exact preface plus two real chapters
    assert [chapter["number"] for chapter in again["chapters"] if chapter["kind"] == "chapter"] == [1, 2]
    assert again["text"] == source
    complete(again)


@pytest.mark.parametrize("first,second,third", [("第一章", "第二章", "第三章"),
                                              ("第1章", "第2章", "第3章"),
                                              ("第１章", "第２章", "第３章"),
                                              ("Chapter 1", "Chapter 2", "Chapter 3")])
def test_delete_shifts_only_heading_numbers_preserving_style_and_body(store, first, second, third):
    original = first + " 开始\r\n第一章正文。\r\n" + second + " 中间\r\n删除正文。\r\n" + third + " 终点\r\n正文中的第三章不变。"
    document, version = seed(store, original)
    result = store.delete_chapters(document["id"], version["id"], ["chapter-2"])
    assert result["text"] == first + " 开始\r\n第一章正文。\r\n" + second + " 终点\r\n正文中的第三章不变。"
    assert [chapter["number"] for chapter in result["chapters"]] == [1, 2]
    assert result["changes"][0]["renumbered"] == [{"chapter_id": "chapter-3", "before": 3, "after": 2}]
    complete(result)


def test_delete_first_and_multiple_chapters_adjusts_numbers_once(store):
    document, first = seed(store, "".join(f"第{index}章 标题{index}\n正文{index}。\n" for index in range(1, 7)))
    result = store.delete_chapters(document["id"], first["id"], ["chapter-1", "chapter-3", "chapter-5"])
    assert [chapter["number"] for chapter in result["chapters"]] == [1, 2, 3]
    assert [chapter["title"] for chapter in result["chapters"]] == ["标题2", "标题4", "标题6"]
    complete(result)


def test_deletion_preserves_real_missing_gaps_and_separate_volume_restart(store):
    original = "第1章 开始\n甲。\n第2章 删除\n乙。\n第7章 缺章后\n丙。\n第二卷\n第1章 新卷\n丁。\n第2章 新卷尾\n戊。"
    document, first = seed(store, original)
    result = store.delete_chapters(document["id"], first["id"], ["chapter-2"])
    assert [chapter["number"] for chapter in result["chapters"]] == [1, 6, 1, 2]
    assert "第二卷\n第1章 新卷\n丁。" in result["text"]
    assert result["gaps"][0]["missing_numbers"] == [2, 3, 4, 5]


def test_deleting_last_chapter_of_previous_volume_preserves_next_volume_heading(store):
    document, first = seed(store, "第1章 前卷\n前卷正文。\n第二卷 新卷\n第1章 后卷\n后卷正文。\n第2章 后卷尾\n尾声。")
    result = store.delete_chapters(document["id"], first["id"], ["chapter-1"])
    assert result["text"] == "第二卷 新卷\n第1章 后卷\n后卷正文。\n第2章 后卷尾\n尾声。"
    numbered = [chapter for chapter in result["chapters"] if chapter["kind"] == "chapter"]
    assert [chapter["number"] for chapter in numbered] == [1, 2]
    assert "duplicate" not in {issue["kind"] for chapter in numbered for issue in chapter["issues"]}
    complete(result)


def test_deleting_one_duplicate_does_not_shift_that_number_or_following_chapters(store):
    document, first = seed(store, "第1章 开始\n甲。\n第2章 空目录\n第2章 正确正文\n乙。\n第3章 尾声\n丙。")
    result = store.delete_chapters(document["id"], first["id"], ["chapter-2"])
    assert [chapter["number"] for chapter in result["chapters"]] == [1, 2, 3]


def test_unrepresentable_custom_number_fails_atomically(store):
    document, first = seed(store, "卷三 第一\n正文。\n卷五 第二\n正文。")
    version = store.resegment(document["id"], first["id"], ["限号"], [{"name": "限号", "regex": r"^卷([三五]) ([^\n]+)$"}])
    with pytest.raises(DataError) as caught:
        store.delete_chapters(document["id"], version["id"], ["chapter-1"])
    assert caught.value.status_code == 422
    assert store.get_document(document["id"])["latest_version_id"] == version["id"]


def test_history_is_recent_ten_plus_original_and_analysis_pins(store):
    document, first = seed(store, "第1章 起点\n原始正文。")
    # An external analysis snapshot pins an older version independently of
    # parent pointers. Merely having a parent does not protect every version.
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("CREATE TABLE run_jobs (id TEXT PRIMARY KEY,document_id TEXT,version_id TEXT)")
    version = first
    snapshots = [first]
    for index in range(1, 24):
        version = store.rename_chapter(document["id"], version["id"], "chapter-1", f"标题{index}")
        snapshots.append(version)
        if index == 2:
            with sqlite3.connect(store.db_path) as connection:
                connection.execute("INSERT INTO run_jobs VALUES (?,?,?)", ("pinned", document["id"], version["id"]))
    metadata = store.get_document(document["id"])
    retained = {item["id"] for item in metadata["versions"]}
    assert len(retained) == HISTORY_LIMIT + 2
    assert {item["id"] for item in snapshots[-HISTORY_LIMIT:]} <= retained
    assert snapshots[0]["id"] in retained
    assert snapshots[2]["id"] in retained
    assert snapshots[3]["id"] not in retained
    assert metadata["history_limit"] == 10
    assert metadata["read_view_limit"] == 3
    assert store.get_version(document["id"], snapshots[2]["id"])["text"] == snapshots[2]["text"]
    assert store.get_original(document["id"])[0] == first["source_text"].encode()
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM doc_version_views").fetchone()[0] == READ_VIEW_LIMIT
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_source_bytes_and_receipts_survive_without_pinning_full_snapshots(store):
    document, first = seed(store, "第1章 首章\n首章正文。\n")
    appended = store.append(document["id"], first["id"], "新增.txt", "第2章 新增\n新增正文。".encode(),
                            preserve_text=True, idempotency_key="source-added")
    version = appended
    for index in range(14):
        version = store.rename_chapter(document["id"], version["id"], "chapter-2", f"新增{index}")
    assert appended["id"] not in {item["id"] for item in store.get_document(document["id"])["versions"]}
    assert store.ingestion_result("source-added") == {"document_id": document["id"], "version_id": version["id"]}
    sources = store.list_sources(document["id"])
    assert len(sources) == 2
    assert sources[1]["version_id"] is None
    assert sources[1]["mode"] == "append"
    assert store.get_source_original(document["id"], sources[1]["id"])[0] == "第2章 新增\n新增正文。".encode()
    replay = store.append(document["id"], first["id"], "新增.txt", "第2章 新增\n新增正文。".encode(),
                          preserve_text=True, idempotency_key="source-added")
    assert replay["id"] == version["id"]
    assert replay["text"].count("新增正文。") == 1
    with pytest.raises(DataError) as changed_receipt:
        store.append(document["id"], version["id"], "不同.txt", "不同原件。".encode(), idempotency_key="source-added")
    assert changed_receipt.value.status_code == 409


def test_many_replacement_uploads_have_bounded_history_and_exact_source_downloads(store):
    document, version = seed(store, "第1章 原版\n原版正文。")
    originals = []
    for index in range(23):
        raw = f"第1章 更新{index}\n新正文🌙{index}。".encode()
        originals.append(raw)
        version = store.replace(document["id"], version["id"], f"更新{index}.txt", raw,
                                 preserve_text=True, idempotency_key=f"replace-{index}")
    metadata = store.get_document(document["id"])
    assert len(metadata["versions"]) == HISTORY_LIMIT + 1
    sources = store.list_sources(document["id"])[1:]
    assert len(sources) == 23
    assert sources[0]["version_id"] is None
    assert all(source["mode"] == "replace" for source in sources)
    assert [store.get_source_original(document["id"], source["id"])[0] for source in sources] == originals
    assert store.ingestion_result("replace-0")["version_id"] == version["id"]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_pruning_queries_only_scalar_metadata_and_history_cache_stays_bounded(store, monkeypatch):
    document, first = seed(store, "第1章 起点\n大正文🌙。" * 100)
    version = first
    for index in range(13):
        version = store.rename_chapter(document["id"], version["id"], "chapter-1", f"起点{index}")
    queries = []
    with store._connection() as connection:
        connection.set_trace_callback(queries.append)
        store._prune_history(connection, document["id"])
    assert all("payload_json" not in query.lower() for query in queries)
    # Opening an archived original rebuilds only that requested view and
    # evicts old cache copies while leaving every immutable version untouched.
    original_index = store.chapter_index(document["id"], first["id"])
    assert original_index["version_id"] == first["id"]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM doc_version_views").fetchone()[0] <= READ_VIEW_LIMIT


def test_history_navigation_does_not_load_full_version_payloads(store, monkeypatch):
    document, first = seed(store, "第1章 首章\n" + "正文🌙。" * 10000)
    def forbidden(*args, **kwargs):
        raise AssertionError("metadata navigation must not deserialize a full snapshot")
    monkeypatch.setattr(store, "get_version", forbidden)
    metadata = store.get_document(document["id"])
    assert metadata["versions"][0]["id"] == first["id"]
    assert "text" not in metadata["versions"][0]


def test_analysis_history_ledger_protects_version_after_current_ledger_replacement(store):
    document, first = seed(store, "第1章 原版\n真实正文。")
    pinned = store.rename_chapter(document["id"], first["id"], "chapter-1", "历史分析来源")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("CREATE TABLE reference_analysis_history (document_id TEXT,revision INTEGER,commits_json TEXT)")
        ledger = {"commits": [[document["id"], "run-id", "chunk-id", "characters", "{}", 6, "fixture", pinned["id"]]], "overrides": []}
        connection.execute("INSERT INTO reference_analysis_history VALUES (?,1,?)", (document["id"], json.dumps(ledger)))
    version = pinned
    for index in range(15):
        version = store.rename_chapter(document["id"], version["id"], "chapter-1", f"后续{index}")
    assert pinned["id"] in {item["id"] for item in store.get_document(document["id"])["versions"]}
    assert store.get_version(document["id"], pinned["id"])["chapters"][0]["title"] == "历史分析来源"


def test_legacy_source_schema_migrates_without_losing_bytes_receipts_or_mode(store):
    document, first = seed(store, "第1章 原版\n原文。")
    raw = "第2章 新增\n新增原件🌙。".encode()
    appended = store.append(document["id"], first["id"], "追加.txt", raw,
                            preserve_text=True, idempotency_key="legacy-append")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("DROP TRIGGER doc_source_immutable")
        connection.execute("DROP INDEX doc_sources_document")
        connection.execute("ALTER TABLE doc_sources RENAME TO sources_modern")
        connection.execute("""CREATE TABLE doc_sources (
            id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES doc_documents(id),
            filename TEXT NOT NULL,format TEXT NOT NULL,encoding TEXT NOT NULL,original BLOB NOT NULL,
            original_sha256 TEXT NOT NULL,source_text TEXT NOT NULL,
            version_id TEXT NOT NULL REFERENCES doc_versions(id),created_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO doc_sources SELECT id,document_id,filename,format,encoding,original,original_sha256,source_text,version_id,created_at FROM sources_modern")
        connection.execute("DROP TABLE sources_modern")
        connection.execute("ALTER TABLE doc_ingestions RENAME TO ingestions_modern")
        connection.execute("CREATE TABLE doc_ingestions(key TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES doc_documents(id))")
        connection.execute("INSERT INTO doc_ingestions SELECT key,document_id FROM ingestions_modern")
        connection.execute("DROP TABLE ingestions_modern")
        connection.execute("DROP TABLE doc_version_meta")
    reopened = DocumentStore(store.data_dir)
    version = reopened.get_version(document["id"])
    for index in range(14):
        version = reopened.rename_chapter(document["id"], version["id"], "chapter-2", f"新标题{index}")
    assert appended["id"] not in {item["id"] for item in reopened.get_document(document["id"])["versions"]}
    source = reopened.list_sources(document["id"])[1]
    assert source["mode"] == "append"
    assert source["version_id"] is None
    assert reopened.get_source_original(document["id"], source["id"])[0] == raw
    assert reopened.ingestion_result("legacy-append")["version_id"] == version["id"]
    assert reopened.append(document["id"], first["id"], "追加.txt", raw,
                           preserve_text=True, idempotency_key="legacy-append")["id"] == version["id"]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE doc_sources SET source_text='changed'")


def test_identical_resegment_does_not_create_more_history(store):
    document, first = seed(store, "第1章 唯一\n正文。")
    version = store.resegment(document["id"], first["id"], ["第N章"])
    for _ in range(5):
        preview = store.preview_resegment(document["id"], version["id"], ["第N章"])
        unchanged = store.resegment(document["id"], version["id"], ["第N章"], preview_token=preview["preview_token"],
                                    conflict_policy="use_new", require_confirmation=True)
        assert unchanged["id"] == version["id"]
    assert len(store.get_document(document["id"])["versions"]) == 2


def test_source_navigation_does_not_load_whole_books_or_original_blobs(store, monkeypatch):
    document, first = seed(store, "第1章 原版\n" + "大正文🌙。" * 20000)
    appended = store.append(document["id"], first["id"], "新增.txt", "第2章 新增\n后文。".encode(), preserve_text=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("source navigation must use only scalar metadata")
    monkeypatch.setattr(store, "get_version", forbidden)
    monkeypatch.setattr(store, "_document", forbidden)
    sources = store.list_sources(document["id"])
    assert [source["mode"] for source in sources] == ["new", "append"]
    assert all(source["active"] for source in sources)
    assert sources[1]["version_id"] == appended["id"]


def test_volume_navigation_save_and_prompt_use_bounded_chapter_heads(store, monkeypatch):
    document, first = seed(store, "第1章 原版\n" + "大正文🌙。" * 10000 + "\n第3章 尾章\n尾文。")
    volumes = VolumeStore(store.data_dir, store)
    def forbidden(*args, **kwargs):
        raise AssertionError("volume navigation must not deserialize the book")
    monkeypatch.setattr(store, "get_version", forbidden)
    state = volumes.get(document["id"], first["id"])
    assert state["chapter_count"] == 2  # virtual missing chapter is excluded
    assert state["volumes"] == [{"title": "全书", "start_chapter": 1, "end_chapter": 2}]
    saved = volumes.save(document["id"], first["id"], state["volumes"], 0)
    assert saved["revision"] == 1
    prompt = volumes.prompt(document["id"], first["id"])
    assert prompt["chapter_count"] == 2
    assert "#1: 原版\n#2: 尾章" in prompt["prompt"]
    assert "大正文" not in prompt["prompt"]
    assert len(store.chapter_previews(document["id"], first["id"])[0]["text"]) == 200


def test_lazy_legacy_source_metadata_also_materializes_receipts_before_pruning(store):
    document, first = seed(store, "第1章 首章\n原版。")
    raw = "第2章 接续\n接续正文。".encode()
    appended = store.append(document["id"], first["id"], "接续.txt", raw,
                            preserve_text=True, idempotency_key="old-receipt")
    version = store.append(document["id"], appended["id"], "再续.txt", "第3章 再续\n再续正文。".encode(), preserve_text=True)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE doc_sources SET mode='unknown'")
        connection.execute("UPDATE doc_ingestions SET source_id=NULL,mode=NULL,original_sha256=NULL")
    assert store.list_sources(document["id"])[1]["mode"] == "append"
    for index in range(14):
        version = store.rename_chapter(document["id"], version["id"], "chapter-1", f"更名{index}")
    assert appended["id"] not in {item["id"] for item in store.get_document(document["id"])["versions"]}
    assert store.append(document["id"], first["id"], "接续.txt", raw, preserve_text=True,
                         idempotency_key="old-receipt")["id"] == version["id"]
