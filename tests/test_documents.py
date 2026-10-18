"""Synthetic document/version integrity checks; no private texts or LLMs."""

import codecs
import hashlib
import io
import sqlite3
import zipfile

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore


@pytest.fixture
def store(tmp_path):
    return DocumentStore(tmp_path)


def archive_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files:
            archive.writestr(name, value)
    return buffer.getvalue()


def assert_complete(version):
    assert version["coverage"]["complete"]
    assert version["coverage"]["chunks_complete"]
    for key in ("chapters", "chunks"):
        assert "".join(piece["text"] for piece in version[key]) == version["text"]
        cursor = 0
        for piece in version[key]:
            assert piece["start"] == cursor
            assert piece["text"] == version["text"][piece["start"]:piece["end"]]
            cursor = piece["end"]
        assert cursor == len(version["text"])


@pytest.mark.parametrize("source", [
    "前置文本\n第一章 初见\n" + "正文。" * 12000 + "\n第二章 尾声\n全文尾标。",
    "没有标题。" * 1700 + "尾部不可丢失。",
    "1\n00:00:01,000 --> 00:00:03,000\n字幕原文。\n\n2\n00:00:04,000 --> 00:00:05,000\n尾标。",
], ids=["long_chapter", "no_headings", "subtitles"])
def test_upload_original_and_complete_segmentation_survive_reopen(store, source):
    raw = codecs.BOM_UTF8 + source.replace("\n", "\r\n").encode("utf-8")
    document = store.upload("完整.txt", raw, title="测试")
    assert document["original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert document["title"] == "测试"
    assert store.get_original(document["id"]) == (raw, "完整.txt")
    version = store.get_version(document["id"])
    assert version["text"] == source
    assert version["source_text"] == source.replace("\n", "\r\n")
    assert_complete(version)
    reopened = DocumentStore(store.data_dir)
    assert reopened.get_version(document["id"]) == version
    assert reopened.get_original(document["id"])[0] == raw
    assert reopened.list_documents()[0]["latest_version_id"] == version["id"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gb18030"])
def test_strict_auto_encodings(store, encoding):
    source = "第一章 测试\n中文正文与尾标。"
    document = store.upload("编码.md", source.encode(encoding))
    assert store.get_version(document["id"])["text"] == source


def test_ambiguous_or_invalid_encoding_creates_nothing(store):
    with pytest.raises(DataError) as caught:
        store.upload("模糊.txt", "正文 ABC".encode("utf-16-le"))
    assert caught.value.status_code == 422
    assert "utf-16-le" in caught.value.detail["encoding_choices"]
    with pytest.raises(DataError):
        store.upload("损坏.txt", b"\xff", encoding="utf-8")
    assert store.list_documents() == []
    document = store.upload("模糊.txt", "正文 ABC".encode("utf-16-le"), encoding="utf-16-le")
    assert store.get_version(document["id"])["text"] == "正文 ABC"


def test_bom_with_invalid_content_never_falls_back_to_gb18030(store):
    with pytest.raises(DataError) as caught:
        store.upload("损坏.txt", codecs.BOM_UTF8 + b"\xff")
    assert caught.value.status_code == 422
    assert store.list_documents() == []


def test_versioning_preserves_original_and_requires_current_base(store):
    source = '第一章 原文\r\n左3Abq5FQkXVhYkEy8u3y右&amp;对白“求月票”。锟斤拷。'
    raw = source.encode("utf-8")
    document = store.upload("原文.txt", raw)
    first = store.get_version(document["id"])
    assert "3Abq5FQkXVhYkEy8u3y" in first["text"]
    assert {diagnostic["code"] for diagnostic in first["diagnostics"]} >= {
        "random_id", "author_note", "mojibake_signature"}
    second = store.create_version(document["id"], first["id"], {"remove_random_ids": True, "chunk_chars": 5})
    assert "左右&" in second["text"]
    assert second["parent_version_id"] == first["id"]
    assert second["source_text"] == source
    assert second["sha256"] == hashlib.sha256(second["text"].encode()).hexdigest()
    assert len(second["id"]) == 64
    assert "confirmed_random_ids" in {change["stage"] for change in second["changes"]}
    assert second["diff"]
    assert_complete(second)
    with pytest.raises(DataError) as caught:
        store.create_version(document["id"], first["id"], {}, manual_text="过期编辑")
    assert caught.value.status_code == 409
    assert len(store.get_document(document["id"])["versions"]) == 2
    assert store.get_version(document["id"], first["id"]) == first
    assert store.get_original(document["id"]) == (raw, "原文.txt")
    third = store.create_version(document["id"], second["id"], {}, manual_text=second["text"] + "\n人工追加尾标。")
    assert third["text"].endswith("人工追加尾标。")
    assert third["changes"][0]["stage"] == "manual_edit"
    assert_complete(third)


def test_sqlite_guards_immutable_original_and_versions(store):
    document = store.upload("不可变.txt", "正文".encode())
    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE doc_documents SET original=? WHERE id=?", (b"changed", document["id"]))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE doc_versions SET payload_json='{}'")
    assert store.get_original(document["id"])[0] == "正文".encode()


@pytest.mark.parametrize("options", [{"remove_random_ids": "yes"}, {"chunk_chars": True},
                                     {"chunk_chars": 0}, {"delete_unknown": True}, []])
def test_bad_cleaning_options_are_rejected_without_new_version(store, options):
    document = store.upload("正文.txt", "正文".encode())
    with pytest.raises(DataError) as caught:
        store.create_version(document["id"], document["latest_version_id"], options)
    assert caught.value.status_code == 422
    assert len(store.get_document(document["id"])["versions"]) == 1


def test_docx_includes_paragraphs_tabs_breaks_and_table_cells(store):
    xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body><w:p><w:r><w:t>第一章 标题</w:t></w:r></w:p>
    <w:p><w:r><w:t>甲&amp;乙</w:t><w:tab/><w:t>后文</w:t><w:br/><w:t>续行</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格尾标。</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    raw = archive_bytes([("word/document.xml", xml)])
    document = store.upload("剧本.docx", raw)
    version = store.get_version(document["id"])
    assert "甲&乙\t后文\n续行" in version["text"]
    assert version["text"].endswith("表格尾标。")
    assert store.get_original(document["id"])[0] == raw
    assert_complete(version)


def epub_files():
    return [
        ("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>'),
        ("OEBPS/book.opf", '<package><manifest><item id="b" href="b.xhtml"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>'),
        ("OEBPS/b.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>隐藏标题</title></head><body><p>第二章 尾声</p><p>最终尾标。</p></body></html>'),
        ("OEBPS/a.xhtml", '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第一章 初见</h1><p>先出现。</p></body></html>'),
    ]


def test_epub_uses_spine_not_zip_or_manifest_order(store):
    raw = archive_bytes(epub_files())
    document = store.upload("顺序.epub", raw)
    version = store.get_version(document["id"])
    assert version["text"].index("第一章") < version["text"].index("第二章")
    assert "最终尾标。" in version["text"]
    assert "隐藏标题" not in version["text"]
    assert_complete(version)


@pytest.mark.parametrize("files", [
    [("word/document.xml", '<!DOCTYPE x [<!ENTITY y "danger">]><document>&y;</document>')],
    [("../escape", "bad"), ("word/document.xml", "<document/>")],
    [("word/document.xml", "<document/>"), ("word/document.xml", "<second/>")],
    [("word/document.xml", "x" * 2000000)],
])
def test_unsafe_archives_never_create_a_document(store, files):
    with pytest.raises(DataError) as caught:
        store.upload("不安全.docx", archive_bytes(files))
    assert caught.value.status_code == 422
    assert store.list_documents() == []


def test_epub_rejects_external_or_missing_spine_targets(store):
    files = epub_files()
    files[1] = (files[1][0], '<package><manifest><item id="a" href="https://example.com/book"/></manifest><spine><itemref idref="a"/></spine></package>')
    with pytest.raises(DataError) as caught:
        store.upload("外链.epub", archive_bytes(files))
    assert caught.value.status_code == 422
    assert store.list_documents() == []


def test_unsupported_format_and_cross_document_version_are_rejected(store):
    with pytest.raises(DataError) as caught:
        store.upload("原文.pdf", b"unsupported")
    assert caught.value.status_code == 422
    first = store.upload("甲.txt", "甲正文".encode())
    second = store.upload("乙.txt", "乙正文".encode())
    with pytest.raises(DataError) as caught:
        store.get_version(first["id"], second["latest_version_id"])
    assert caught.value.status_code == 404
