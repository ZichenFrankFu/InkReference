"""Synthetic evidence for reviewed, atomic literal body replacement."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from inkreference.database import DataError
from inkreference.document_replace import apply_replace, create_router, preview_replace
from inkreference.documents import DocumentStore


@pytest.fixture
def store(tmp_path):
    return DocumentStore(tmp_path / "data")


def seed(store, text):
    document = store.upload("替换合成样本.txt", text.encode("utf-8"), preserve_text=True)
    return document["id"], store.get_version(document["id"])


def preview(store, document_id, version, search, replacement, **kwargs):
    return preview_replace(store, document_id, version["id"], search, replacement, **kwargs)


def apply(store, document_id, version, search, replacement, **kwargs):
    reviewed = preview(store, document_id, version, search, replacement, **kwargs)
    return apply_replace(store, document_id, version["id"], search, replacement,
                         preview_id=reviewed["preview_id"], **kwargs)


def test_preview_counts_exact_literals_in_bodies_and_writes_no_version(store):
    source = "第1章 [.*]标题\r\n[.*]正文。[.*]\r\n第2章 尾声\r\n[.*]和正则点。"
    document_id, original = seed(store, source)
    result = preview(store, document_id, original, "[.*]", "新🌙")
    assert result["match_count"] == 3
    assert result["affected_chapter_count"] == 2
    assert [row["match_count"] for row in result["chapters"]] == [2, 1]
    assert result["output_characters"] == len(source) + 3 * (len("新🌙") - len("[.*]"))
    assert result["body_only"] is True
    assert len(store.get_document(document_id)["versions"]) == 1
    assert store.get_version(document_id)["text"] == source


def test_apply_is_single_version_preserving_headings_crlf_original_and_history(store):
    source = "第1章 猫标题\r\n猫🌟。\r\n\r\n猫尾。\r\n第2章 后续\r\n猫结束。\r\n"
    document_id, original = seed(store, source)
    result = apply(store, document_id, original, "猫", "犬")
    saved = store.get_version(document_id)
    assert result["id"] == saved["id"]
    assert result["replacement_preview"]["match_count"] == 3
    assert "text" not in result and "chapters" not in result
    assert saved["text"] == source.replace("猫", "犬").replace("犬标题", "猫标题")
    assert saved["text"].count("\r\n") == source.count("\r\n")
    assert saved["version_number"] == original["version_number"] + 1
    assert saved["parent_version_id"] == original["id"]
    assert len(store.get_document(document_id)["versions"]) == 2
    assert store.get_version(document_id, original["id"])["text"] == source
    assert store.get_original(document_id)[0] == source.encode("utf-8")
    assert saved["source_text"] == original["source_text"]
    assert saved["active_source_ids"] == original["active_source_ids"]
    assert "".join(item["text"] for item in saved["chapters"]) == saved["text"]
    assert "".join(item["text"] for item in saved["chunks"]) == saved["text"]


def test_chapter_scope_deletes_only_selected_body_without_extra_newlines(store):
    source = "第1章 广告标题\n广告\n正文广告。\n第2章 后续\n广告正文。"
    document_id, original = seed(store, source)
    result = apply(store, document_id, original, "广告", "", scope="chapter", chapter_id="chapter-1")
    assert result["replacement_preview"]["affected_chapter_count"] == 1
    assert store.get_version(document_id)["text"] == "第1章 广告标题\n\n正文。\n第2章 后续\n广告正文。"
    assert store.get_version(document_id)["chapters"][1]["text"] == original["chapters"][1]["text"]


def test_new_heading_like_replacement_stays_in_body_and_directory_is_pinned(store):
    document_id, original = seed(store, "第1章 起始\n目标。\n第2章 尾声\n结束。")
    replacement = "第99章 正文引文\n🌟"
    apply(store, document_id, original, "目标", replacement)
    saved = store.get_version(document_id)
    assert len(saved["chapters"]) == 2
    assert [chapter["number"] for chapter in saved["chapters"]] == [1, 2]
    assert saved["chapters"][0]["raw_marker"] == original["chapters"][0]["raw_marker"]
    assert replacement in saved["chapters"][0]["content"]
    assert saved["chapters"][1]["start"] == original["chapters"][1]["start"] + len(replacement) - len("目标")


def test_missing_placeholder_is_skipped_and_never_materialized(store):
    document_id, original = seed(store, "第1章 开始\n猫。\n第3章 后续\n猫。")
    missing = next(chapter for chapter in store.chapter_index(document_id)["chapters"] if chapter.get("placeholder"))
    result = preview(store, document_id, original, "猫", "犬", scope="chapter", chapter_id=missing["id"])
    assert result["match_count"] == result["affected_chapter_count"] == 0
    with pytest.raises(DataError) as error:
        apply_replace(store, document_id, original["id"], "猫", "犬", scope="chapter",
                      chapter_id=missing["id"], preview_id=result["preview_id"])
    assert error.value.status_code == 422
    apply(store, document_id, original, "猫", "犬")
    saved = store.get_version(document_id)
    assert len(saved["chapters"]) == 2
    assert [chapter["number"] for chapter in saved["chapters"]] == [1, 3]


def test_changed_preview_parameters_and_stale_versions_reject_without_writing(store):
    document_id, original = seed(store, "第1章 开始\n猫。\n第2章 后续\n猫。")
    result = preview(store, document_id, original, "猫", "犬")
    with pytest.raises(DataError) as wrong_parameters:
        apply_replace(store, document_id, original["id"], "猫", "兔", preview_id=result["preview_id"])
    assert wrong_parameters.value.status_code == 409
    assert len(store.get_document(document_id)["versions"]) == 1
    saved = apply(store, document_id, original, "猫", "犬")
    with pytest.raises(DataError) as stale:
        apply_replace(store, document_id, original["id"], "猫", "犬", preview_id=result["preview_id"])
    assert stale.value.status_code == 409
    assert store.get_version(document_id)["id"] == saved["id"]


def test_concurrent_change_after_candidate_is_caught_by_transactional_cas(store, monkeypatch):
    document_id, original = seed(store, "第1章 开始\n猫。\n第2章 后续\n猫。")
    result = preview(store, document_id, original, "猫", "犬")
    commit = store._commit_prepared
    current = None

    def race(*args, **kwargs):
        nonlocal current
        monkeypatch.setattr(store, "_commit_prepared", commit)
        current = store.edit_chapter(document_id, original["id"], "chapter-2", "新正文。")
        return commit(*args, **kwargs)

    monkeypatch.setattr(store, "_commit_prepared", race)
    with pytest.raises(DataError) as stale:
        apply_replace(store, document_id, original["id"], "猫", "犬", preview_id=result["preview_id"])
    assert stale.value.status_code == 409
    assert store.get_version(document_id)["id"] == current["id"]
    assert len(store.get_document(document_id)["versions"]) == 2
    assert "犬" not in current["text"]


def test_mid_commit_failure_rolls_back_entire_batch(store, monkeypatch):
    document_id, original = seed(store, "第1章 开始\n猫。\n第2章 后续\n猫。")
    result = preview(store, document_id, original, "猫", "犬")
    writer = store._write_views

    def fail(*args, **kwargs):
        writer(*args, **kwargs)
        raise RuntimeError("synthetic interrupted batch")

    monkeypatch.setattr(store, "_write_views", fail)
    # _insert_version calls the class-level writer.
    monkeypatch.setattr(DocumentStore, "_write_views", staticmethod(fail))
    with pytest.raises(RuntimeError, match="synthetic interrupted batch"):
        apply_replace(store, document_id, original["id"], "猫", "犬", preview_id=result["preview_id"])
    assert store.get_version(document_id)["id"] == original["id"]
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM doc_versions WHERE document_id=?", (document_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("kwargs", [{"search": ""}, {"scope": "invalid"}, {"scope": "chapter"},
                                   {"scope": "all", "chapter_id": "chapter-1"}, {"body_only": False},
                                   {"replacement": "\ud800"}])
def test_invalid_replacement_request_rejects_without_version(store, kwargs):
    document_id, original = seed(store, "第1章 开始\n猫。")
    parameters = {"search": "猫", "replacement": "犬", **kwargs}
    with pytest.raises(DataError) as error:
        preview_replace(store, document_id, original["id"], **parameters)
    assert error.value.status_code == 422
    assert len(store.get_document(document_id)["versions"]) == 1


def test_no_match_and_identical_replacement_do_not_create_versions(store):
    document_id, original = seed(store, "第1章 开始\n猫。")
    for search, replacement in [("不存在", "犬"), ("猫", "猫")]:
        with pytest.raises(DataError) as error:
            apply(store, document_id, original, search, replacement)
        assert error.value.status_code == 422
    assert len(store.get_document(document_id)["versions"]) == 1


def test_http_preview_then_apply_requires_review_and_rejects_regex_and_heading_mode(store):
    document_id, original = seed(store, "第1章 [.*]标题\n[.*]正文。\n第2章 后续\n[.*]末尾。")
    app = FastAPI()

    @app.exception_handler(DataError)
    async def data_error(_, error):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    app.include_router(create_router(lambda: store))
    with TestClient(app) as client:
        prefix = f"/api/studio/documents/{document_id}/replace"
        body = {"base_version_id": original["id"], "search": "[.*]", "replacement": "新"}
        response = client.post(prefix + "/preview", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["match_count"] == 2
        assert client.post(prefix + "/apply", json=body).status_code == 422
        assert client.post(prefix + "/preview", json={**body, "regex": True}).status_code == 422
        assert client.post(prefix + "/preview", json={**body, "body_only": False}).status_code == 422
        response = client.post(prefix + "/apply", json={**body, "preview_id": response.json()["preview_id"]})
        assert response.status_code == 201, response.text
        assert response.json()["replacement_preview"]["match_count"] == 2
        assert store.get_version(document_id)["text"].startswith("第1章 [.*]标题\n新正文。")
