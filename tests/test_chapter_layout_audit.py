"""Independent regressions for save/directory defects found in review."""
from __future__ import annotations

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore


@pytest.mark.parametrize("title", ["归途。", "长" * 81])
def test_missing_chapter_title_never_commits_an_unrecognized_heading(tmp_path, title):
    store = DocumentStore(tmp_path)
    source = "第11章 之前\n旧正文。\n第17章 之后\n尾正文。"
    document = store.upload("合成.txt", source.encode(), preserve_text=True)
    original = store.get_version(document["id"])
    missing = next(chapter for chapter in store.chapter_index(document["id"])["chapters"]
                   if chapter.get("placeholder") and chapter["number"] == 12)
    try:
        saved = store.edit_chapter(document["id"], original["id"], missing["id"], "补写正文🌟。", title=title)
    except DataError as error:
        assert error.status_code == 422
        assert store.get_document(document["id"])["latest_version_id"] == original["id"]
        assert store.get_version(document["id"])["text"] == source
    else:
        filled = next(chapter for chapter in saved["chapters"] if chapter["number"] == 12)
        assert filled["title"] == title
        assert "补写正文🌟。" in filled["content"]
        assert not any(chapter.get("placeholder") and chapter.get("number") == 12
                       for chapter in store.chapter_index(document["id"])["chapters"])
    assert store.get_original(document["id"])[0] == source.encode()


@pytest.mark.parametrize("title", ["改名。", "长" * 81])
def test_rename_cannot_silently_turn_a_chapter_into_preface_or_previous_body(tmp_path, title):
    store = DocumentStore(tmp_path)
    source = "第1章 之前\r\n旧正文🌟。\r\n第2章 之后\r\n尾正文。"
    document = store.upload("合成.txt", source.encode(), preserve_text=True)
    original = store.get_version(document["id"])
    try:
        saved = store.rename_chapter(document["id"], original["id"], "chapter-1", title)
    except DataError as error:
        assert error.status_code == 422
        assert store.get_document(document["id"])["latest_version_id"] == original["id"]
    else:
        first = saved["chapters"][0]
        assert first["kind"] == "chapter" and first["number"] == 1
        assert first["title"] == title
        assert "旧正文🌟。" in first["content"]
        assert saved["chapters"][1]["text"] == original["chapters"][1]["text"]
    assert store.get_original(document["id"])[0] == source.encode()


def test_missing_items_are_counted_once_without_counting_the_following_valid_chapter(tmp_path):
    store = DocumentStore(tmp_path)
    document = store.upload("合成.txt", "第11章 之前\n正文。\n第17章 之后\n正文。".encode(), preserve_text=True)
    index = store.chapter_index(document["id"])
    assert index["placeholder_count"] == 5
    assert index["issue_chapter_count"] == 5
    assert not next(chapter for chapter in index["chapters"] if chapter["number"] == 17)["issues"]
    summary = store.get_version_summary(document["id"])
    assert summary["placeholder_count"] == 5
    assert summary["issue_chapter_count"] == 5


def test_final_txt_uses_only_single_newlines_even_between_chapters_and_placeholders(tmp_path):
    store = DocumentStore(tmp_path)
    source = "书名\r\n\r\n作者\r\n第11章 之前\r\n正文。\r\n\r\n第17章 之后\r\n正文。\r\n"
    document = store.upload("合成.txt", source.encode(), preserve_text=True)
    cleaned = store.formatted_export(document["id"])
    lines = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    assert "\n\n" not in lines
    assert "第12章\n第13章\n第14章\n第15章\n第16章\n第17章 之后" in lines
    assert "书名\n作者" in lines
    assert store.get_original(document["id"])[0] == source.encode()
