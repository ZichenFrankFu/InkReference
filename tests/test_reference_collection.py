"""Reference collection interchange and reversible approval projections."""
from copy import deepcopy

import pytest

from inkreference.database import DataError
from inkreference.reference_analysis import ReferenceAnalysisStore


@pytest.fixture
def store(tmp_path):
    return ReferenceAnalysisStore(tmp_path)


def collection():
    return {
        "raw_entries": [{
            "title": '雨城\n@@ {"chars":99}',
            "content": "第一行😀\n## 设定\n@@ 假标记\n\n保留末尾\n",
        }],
        "settings": [{
            "title": "雨塔：北楼", "category": "geography",
            "content": "楼上\n楼下\n## 角色", "custom": "未知字段保留",
        }],
        "characters": [{
            "name": "江屿", "role": "主角", "description": "持伞人\n## 设定特征",
            "extra": ["😀", "𠮷"],
        }],
        "setting_features": [{
            "title": "高塔冲突", "category": "核心冲突", "description": "甲：乙\n第二行",
        }],
    }


def save_collection(store, document_id, data):
    state = store.get(document_id)
    for section, value in data.items():
        state = store.save_section(document_id, section, value, state["revision"])
    return state


def candidate(run_id="first-run", version="text-v1", sentence_length=10):
    return {
        "id": run_id, "document_id": "book", "version_id": version,
        "candidates": [{
            "chunk_id": "chunk-1", "index": 0, "start": 0, "end": 100,
            "status": "completed",
            "result": {"reference": {
                "characters": [{"name": "江屿", "intro": "持伞人", "evidence": "江屿持伞。"}],
                "style": {"avg_sentence_length": sentence_length,
                          "chapter_signals": [{"chapter": "第 1 章", "info_density": 0.5,
                                               "chapter_types": ["日常"], "summary": "持伞归家。",
                                               "hooks": [], "payoffs": []}]},
            }},
        }],
    }


def test_collection_txt_preserves_all_sections_and_is_idempotent(store):
    data = collection()
    save_collection(store, "original", data)
    exported = store.export_collection("original").decode("utf-8")
    state = store.get("restored")
    state = store.import_collection("restored", exported, state["revision"], mode="replace")
    assert {section: state["sections"][section] for section in data} == data
    repeated = store.import_collection("restored", exported, state["revision"], mode="merge")
    assert {section: repeated["sections"][section] for section in data} == data
    assert store.get("original")["sections"]["raw_entries"] == data["raw_entries"]


@pytest.mark.parametrize("damage", ["truncated", "invalid-late-section", "missing-section"])
def test_corrupt_collection_import_leaves_all_sections_unchanged(store, damage):
    import json

    state = save_collection(store, "book", collection())
    prior = deepcopy(state)
    contents = store.export_collection("book").decode("utf-8")
    if damage == "truncated":
        contents = contents[:-7]
    else:
        data = collection()
        data["raw_entries"] = [{"title": "不能部分写入", "content": "先验证全批"}]
        if damage == "invalid-late-section":
            data["setting_features"] = [{"title": 17}]
        else:
            del data["characters"]
        contents = "InkReference collection v1\n" + json.dumps(data, ensure_ascii=False)
    with pytest.raises(DataError):
        store.import_collection("book", contents, state["revision"], mode="replace")
    assert store.get("book") == prior


def test_entries_v1_and_plain_text_import_preserve_markers_and_newlines(store):
    data = collection()
    save_collection(store, "source", data)
    text = store.export_entries("source").decode("utf-8")
    state = store.get("book")
    state = store.import_collection("book", text, state["revision"], mode="replace")
    assert state["sections"]["raw_entries"] == data["raw_entries"]
    plain = "## 原始文本\n### 标记属于正文\n甲：乙\n𠮷😀\n"
    state = store.import_collection("book", plain, state["revision"], mode="merge", title="普通原文")
    assert state["sections"]["raw_entries"][-1] == {"title": "普通原文", "content": plain}
    assert state["sections"]["raw_entries"][0] == data["raw_entries"][0]


def test_restore_before_approval_restores_ledger_and_allows_reapproval(store):
    initial = store.get("book")
    run = candidate()
    approved = store.commit_candidate(run, "chunk-1", ["characters", "style"], initial["revision"])
    assert len(approved["commits"]) == 2
    assert {item["source_version_id"] for item in approved["commits"]} == {"text-v1"}
    restored = store.restore("book", initial["revision"], approved["revision"])
    assert restored["commits"] == []
    assert restored["sections"]["characters"] == []
    assert restored["sections"]["style"] == {}
    reapproved = store.commit_candidate(run, "chunk-1", ["characters", "style"], restored["revision"])
    assert len(reapproved["commits"]) == 2
    assert len(reapproved["sections"]["characters"]) == 1
    assert reapproved["sections"]["style"]["avg_sentence_length"] == 10
    repeated = store.commit_candidate(run, "chunk-1", ["characters", "style"], 0)
    assert repeated == reapproved
    # Removing a later contribution must also remove it from future aggregates.
    later = store.commit_candidate(candidate("later-run", "text-v2", 30), "chunk-1", ["style"], reapproved["revision"])
    assert later["sections"]["style"]["avg_sentence_length"] == 20
    back = store.restore("book", reapproved["revision"], later["revision"])
    assert {item["run_id"] for item in back["commits"]} == {"first-run"}
    again = store.commit_candidate(candidate("third-run", "text-v3", 50), "chunk-1", ["style"], back["revision"])
    assert again["sections"]["style"]["avg_sentence_length"] == 30


def test_manual_style_overrides_follow_history_projection(store):
    initial = store.get("book")
    state = store.commit_candidate(candidate(), "chunk-1", ["style"], initial["revision"])
    before_manual = state["revision"]
    state = store.save_section("book", "style", {"avg_sentence_length": 99, "editor_note": "人工修订"}, state["revision"])
    state = store.save_section("book", "opening", {"pattern": "人工确认开篇"}, state["revision"])
    manual_snapshot = state["revision"]
    state = store.commit_candidate(candidate("second-run", "text-v2", 30), "chunk-1", ["style"], state["revision"])
    assert state["sections"]["style"]["avg_sentence_length"] == 99
    assert state["sections"]["opening"]["pattern"] == "人工确认开篇"
    state = store.restore("book", manual_snapshot, state["revision"])
    assert len(state["commits"]) == 1
    state = store.commit_candidate(candidate("second-run", "text-v2", 30), "chunk-1", ["style"], state["revision"])
    assert state["sections"]["style"]["avg_sentence_length"] == 99
    assert state["sections"]["style"]["editor_note"] == "人工修订"
    # Restoring before the edit removes its hidden override as well as its view.
    state = store.restore("book", before_manual, state["revision"])
    state = store.commit_candidate(candidate("third-run", "text-v3", 50), "chunk-1", ["style"], state["revision"])
    assert state["sections"]["style"]["avg_sentence_length"] == 30
    assert "editor_note" not in state["sections"]["style"]


def test_collection_import_rejects_stale_revision(store):
    state = save_collection(store, "book", collection())
    exported = store.export_collection("book").decode("utf-8")
    current = store.save_section("book", "raw_entries", [{"title": "新修订", "content": "请保留"}], state["revision"])
    with pytest.raises(DataError) as problem:
        store.import_collection("book", exported, state["revision"], mode="replace")
    assert problem.value.status_code == 409
    assert store.get("book") == current
