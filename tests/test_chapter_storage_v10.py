"""Synthetic chapter filesystem consistency, recovery and bounded-edit checks."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import zipfile

import pytest

from inkreference import chapter_storage
from inkreference.database import DataError
from inkreference.documents import DocumentStore

SOURCE = "书名：星舟\n作者：合成作者\n最早更新时间：2020-03\n第一章 出发\n第一段。\n\n第二段。\n第三章 归来\n尾标。"


def uploaded(tmp_path, source=SOURCE, **options):
    store = DocumentStore(tmp_path)
    document = store.upload("合成.txt", source.encode(), preserve_text=True, **options)
    return store, document, store.get_version(document["id"])


def manifest(store, document):
    metadata = store.get_book_metadata(document["id"])
    folder = store.books_dir / metadata["storage_folder"]
    return folder, json.loads((folder / "metadata.json").read_bytes())


def test_book_folder_contains_metadata_and_one_txt_per_display_chapter(tmp_path):
    store, document, version = uploaded(tmp_path)
    folder, value = manifest(store, document)
    assert folder.name == "星舟"
    assert value["book_title"] == "星舟"
    assert value["author"] == "合成作者"
    assert value["earliest_update_at"] == "2020-03"  # no invented day/time
    assert value["version_id"] == version["id"]
    assert value["chapter_count"] == 4  # frontmatter, 1, blank 2, 3
    assert len(list(folder.glob("*.txt"))) == 4
    text = "".join((folder / entry["filename"]).read_bytes().decode() for entry in value["chapters"])
    assert text == store.formatted_export(document["id"])
    assert "第一段。\n第二段。" in text
    assert store.storage_status(document["id"], verify=True)["state"] == "ready"
    assert store.get_original(document["id"])[0] == SOURCE.encode()
    listing = store.list_documents()[0]
    assert listing["book_title"] == "星舟" and listing["author"] == "合成作者"
    assert listing["storage_state"] == "ready"


def test_unknown_source_date_is_not_import_date_and_initial_metadata_is_atomic(tmp_path):
    store, document, _ = uploaded(tmp_path, "第一章 出发\n正文。", title="指定作品",
                                  book_metadata={"author": "作者甲", "book_type": "中文小说"})
    folder, value = manifest(store, document)
    assert folder.name == "指定作品"
    assert value["earliest_update_at"] is None
    assert value["imported_at"]
    assert value["author"] == "作者甲" and value["book_type"] == "中文小说"
    with pytest.raises(DataError):
        store.upload("错误.txt", b"text", book_metadata={"earliest_update_at": "2020-02-31"})
    assert len(store.list_documents()) == 1


def test_duplicate_titles_and_unsafe_filename_components_never_merge(tmp_path):
    store, one, _ = uploaded(tmp_path, "第1章 标题\n正文甲", title="CON:../同名")
    two = store.upload("第二本.txt", "第1章 标题\n正文乙".encode(), title="CON:../同名")
    folder_one, first = manifest(store, one)
    folder_two, second = manifest(store, two)
    assert folder_one != folder_two
    assert folder_two.name.endswith(two["id"][:12])
    assert all(path.parent == store.books_dir for path in [folder_one, folder_two])
    assert first["document_id"] != second["document_id"]
    assert "正文甲" in "".join(path.read_text(encoding="utf-8") for path in folder_one.glob("*.txt"))
    assert "正文乙" in "".join(path.read_text(encoding="utf-8") for path in folder_two.glob("*.txt"))
    assert chapter_storage.safe_name("NUL") == "_NUL"
    assert chapter_storage.safe_name("..") not in {".", ".."}


def test_small_edit_fetches_and_encodes_only_changed_chapter(tmp_path, monkeypatch):
    source = "".join(f"第{i}章 航行\n正文。\n" * 1 for i in range(1, 101))
    store, document, old = uploaded(tmp_path, source)
    folder, before = manifest(store, document)
    untouched = {entry["id"]: (entry["filename"], (folder / entry["filename"]).stat().st_mtime_ns)
                 for entry in before["chapters"] if entry["id"] != "chapter-50"}
    calls = []
    original = chapter_storage.chapter_text
    def tracked(chapter, text):
        calls.append(chapter["id"])
        return original(chapter, text)
    monkeypatch.setattr(chapter_storage, "chapter_text", tracked)
    changed = store.edit_chapter(document["id"], old["id"], "chapter-50", "修改正文。")
    assert calls == ["chapter-50"]
    folder, after = manifest(store, document)
    assert after["version_id"] == changed["id"]
    for entry in after["chapters"]:
        if entry["id"] in untouched:
            assert (entry["filename"], (folder / entry["filename"]).stat().st_mtime_ns) == untouched[entry["id"]]
    assert store.get_version(document["id"], old["id"])["text"] == source


def test_append_delete_restore_publish_current_and_preserve_user_files(tmp_path):
    store, document, old = uploaded(tmp_path, "第1章 原章\n正文。\n第2章 后章\n后文。")
    folder, _ = manifest(store, document)
    user_file = folder / "私人笔记.txt"
    user_file.write_text("保留笔记", encoding="utf-8")
    appended = store.append(document["id"], old["id"], "补充.txt", "第3章 补充\n末尾。".encode())
    assert manifest(store, document)[1]["version_id"] == appended["id"]
    deleted = store.delete_chapters(document["id"], appended["id"], ["chapter-2"])
    assert manifest(store, document)[1]["version_id"] == deleted["id"]
    restored = store.restore_version(document["id"], deleted["id"], old["id"])
    _, current = manifest(store, document)
    assert current["version_id"] == restored["id"]
    assert len(list(folder.glob("*.txt"))) == len(current["chapters"]) + 1
    assert user_file.read_text(encoding="utf-8") == "保留笔记"
    assert store.get_original(document["id"])[0] == old["source_text"].encode()


def test_publish_failure_keeps_old_manifest_and_recoverable_committed_version(tmp_path, monkeypatch):
    store, document, old = uploaded(tmp_path, "第1章 原章\n旧正文。\n第2章 后章\n后文。")
    folder, before = manifest(store, document)
    before_bytes = (folder / "metadata.json").read_bytes()
    replace = os.replace
    def fail_manifest(source, target):
        if str(target).endswith("metadata.json"):
            raise OSError("合成磁盘错误")
        return replace(source, target)
    with monkeypatch.context() as context:
        context.setattr(chapter_storage.os, "replace", fail_manifest)
        changed = store.edit_chapter(document["id"], old["id"], "chapter-1", "新正文。")
    assert store.get_document(document["id"])["latest_version_id"] == changed["id"]
    assert (folder / "metadata.json").read_bytes() == before_bytes
    assert all((folder / entry["filename"]).is_file() for entry in before["chapters"])
    assert store.storage_status(document["id"])["state"] == "pending"
    assert store.get_book_metadata(document["id"])["storage_error"] == "合成磁盘错误"
    assert store.materialize_chapters(document["id"])["state"] == "ready"
    assert manifest(store, document)[1]["version_id"] == changed["id"]
    assert store.get_original(document["id"])[0] == old["source_text"].encode()


def test_explicit_verify_and_rebuild_recover_same_size_corruption(tmp_path):
    store, document, _ = uploaded(tmp_path, "第1章 原章\n甲乙丙。")
    folder, value = manifest(store, document)
    target = folder / value["chapters"][0]["filename"]
    expected = target.read_bytes()
    target.write_bytes(b"x" * len(expected))
    assert store.storage_status(document["id"], verify=True)["state"] == "error"
    assert store.materialize_chapters(document["id"])["state"] == "ready"
    assert target.read_bytes() == expected


def test_database_only_restore_reconstructs_files_without_new_history(tmp_path):
    store, document, old = uploaded(tmp_path / "source")
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    with sqlite3.connect(store.db_path) as connection, sqlite3.connect(restored_dir / "workbench.sqlite3") as destination:
        connection.backup(destination)
    restored = DocumentStore(restored_dir)
    # An old backup may say ready even though derived files are absent.
    assert restored.storage_status(document["id"], verify=True)["state"] == "error"
    assert restored.materialize_all_chapters()[0]["state"] == "ready"
    assert manifest(restored, document)[1]["version_id"] == old["id"]
    assert len(restored.get_document(document["id"])["versions"]) == 1
    assert restored.get_original(document["id"])[0] == SOURCE.encode()


def test_metadata_revision_and_version_guard_title_folder_and_source_precision(tmp_path):
    store, document, old = uploaded(tmp_path)
    before = store.get_book_metadata(document["id"])
    old_folder, _ = manifest(store, document)
    (old_folder / "自写笔记.txt").write_text("保留", encoding="utf-8")
    changed = store.update_book_metadata(document["id"], {"book_title": "改名星舟", "author": "新作者", "earliest_update_at": "2019", "book_type": "中文小说"},
                                         expected_version_id=old["id"], expected_revision=before["metadata_revision"])
    folder, value = manifest(store, document)
    assert folder.name == "改名星舟" and value["earliest_update_at"] == "2019"
    assert changed["metadata_revision"] == before["metadata_revision"] + 1
    assert changed["version_id"] == old["id"]  # metadata isn't a text history version
    assert (old_folder / "自写笔记.txt").is_file()
    assert not list(old_folder.glob("*--*.txt"))
    with pytest.raises(DataError) as conflict:
        store.update_book_metadata(document["id"], {"author": "过期修改"}, expected_version_id=old["id"], expected_revision=before["metadata_revision"])
    assert conflict.value.status_code == 409
    newer = store.edit_chapter(document["id"], old["id"], "chapter-2", "更新正文。")
    with pytest.raises(DataError) as stale:
        store.update_book_metadata(document["id"], {"author": "过期正文"}, expected_version_id=old["id"])
    assert stale.value.status_code == 409
    assert store.get_book_metadata(document["id"])["version_id"] == newer["id"]


def test_chapter_archive_has_title_folder_metadata_and_exact_merge(tmp_path):
    store, document, old = uploaded(tmp_path)
    raw = store.chapter_archive(document["id"], old["id"])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        metadata = json.loads(archive.read("星舟/metadata.json"))
        assert metadata["version_id"] == old["id"]
        merged = "".join(archive.read("星舟/" + item["filename"]).decode() for item in metadata["chapters"])
        assert merged == store.formatted_export(document["id"], old["id"])
        assert all(not name.startswith("/") and ".." not in name.split("/") for name in archive.namelist())


def test_book_root_symlink_never_receives_writes(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    try:
        (data / "books").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this environment")
    store, document, _ = uploaded(data)
    assert store.get_book_metadata(document["id"])["storage_state"] == "error"
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("date", ["2020-99-99", "2021-02-29", "0000", "2020-13", "2020-01-01 25:00"])
def test_invalid_source_calendar_date_remains_unknown(tmp_path, date):
    store, document, _ = uploaded(tmp_path, f"书名：测试日期\n最早更新时间：{date}\n第1章 开始\n正文。")
    assert store.get_book_metadata(document["id"])["earliest_update_at"] is None
    assert manifest(store, document)[1]["earliest_update_at"] is None
    assert date in store.get_original(document["id"])[0].decode("utf-8")


@pytest.mark.parametrize("corruption", ["array", "null", "number", "chapters_object", "chapters_null", "chapter_string", "chapter_missing", "chapter_invalid_filename", "chapter_unhashable_id"])
def test_malformed_manifest_never_breaks_save_and_can_be_rebuilt(tmp_path, corruption):
    store, document, original = uploaded(tmp_path, "第1章 起点\n原正文。\n第2章 后续\n末正文。")
    folder, value = manifest(store, document)
    malformed = value.copy()
    if corruption == "array":
        malformed = []
    elif corruption == "null":
        malformed = None
    elif corruption == "number":
        malformed = 3
    elif corruption == "chapters_object":
        malformed["chapters"] = {}
    elif corruption == "chapters_null":
        malformed["chapters"] = None
    else:
        malformed["chapters"] = list(value["chapters"])
        first = dict(malformed["chapters"][0])
        if corruption == "chapter_string":
            first = "invalid"
        elif corruption == "chapter_missing":
            first = {}
        elif corruption == "chapter_invalid_filename":
            first["filename"] = "../outside.txt"
        elif corruption == "chapter_unhashable_id":
            first["id"] = []
        malformed["chapters"][0] = first
    (folder / "metadata.json").write_bytes(json.dumps(malformed).encode())
    assert store.storage_status(document["id"])["state"] == "pending"
    assert store.storage_status(document["id"], verify=True)["state"] == "error"
    assert chapter_storage.manifest_owner(store.books_dir, folder.name) in {None, document["id"]}
    # A committed edit must not fail because a derived manifest is corrupt.
    changed = store.edit_chapter(document["id"], original["id"], "chapter-1", "修改正文。")
    assert store.get_document(document["id"])["latest_version_id"] == changed["id"]
    assert store.materialize_chapters(document["id"])["state"] == "ready"
    assert manifest(store, document)[1]["version_id"] == changed["id"]
    assert store.get_original(document["id"])[0] == original["source_text"].encode()
