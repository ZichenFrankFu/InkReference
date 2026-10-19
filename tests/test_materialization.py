"""Reviewed candidate commits use the real transactional material library."""

from __future__ import annotations

import hashlib
import json
import copy

import pytest

from inkreference.database import DataError, DataStore
from inkreference.documents import DocumentStore
from inkreference.materialization import Materializer


@pytest.fixture
def services(tmp_path):
    store = DataStore(tmp_path / "inkreference.sqlite3")
    documents = DocumentStore(tmp_path)
    materializer = Materializer(store, documents)
    return store, documents, materializer


def analysis(documents, *, work_id=None, run_id="synthetic-reviewed-run"):
    source = "第一章 钥匙\n林舟在门前发现铜钥匙。\n第二章 身份\n林舟坦白自己是守门人。"
    document = documents.upload("合成样本.txt", source.encode(), title="合成样本", work_id=work_id)
    return {
        "id": run_id, "document_id": document["id"], "version_id": document["latest_version_id"],
        "status": "review", "results": {
            "summary": "林舟发现钥匙，随后披露自己的身份。",
            "characters": [{"name": "林舟", "description": "自称守门人。", "evidence": "林舟坦白自己是守门人。"}],
            "events": [{"name": "发现钥匙", "description": "林舟在门前发现铜钥匙。", "evidence": "林舟在门前发现铜钥匙。"}],
            "cliches": [{"name": "身份揭露", "description": "林舟向读者披露守门人身份。", "evidence": "林舟坦白自己是守门人。"}],
        },
    }


def library_snapshot(store):
    with store.connect() as connection:
        return {table: [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY 1')]
                for table in ("works", "characters", "cliches", "work_cliches", "record_versions", "analysis_commits")}


def test_materialization_commits_relationships_and_exact_evidence_once(services):
    store, documents, materializer = services
    run = analysis(documents)
    result = materializer(run)
    assert result["created_work"]
    assert store.stats() == {"works": 1, "characters": 1, "cliches": 1, "work_cliches": 1}
    work = store.get_record("works", result["work_id"])
    assert work["values"]["作品"] == "合成样本"
    assert work["values"]["剧情梗概"] == run["results"]["summary"]
    assert "剧情补充说明" not in work["values"]
    assert documents.get_document(run["document_id"])["work_id"] == result["work_id"]
    character = store.get_record("characters", result["character_ids"][0])
    assert json.loads(character["values"]["关联作品"])[0]["作品ID"] == result["work_id"]
    assert character["values"]["角色名"] == "林舟"
    with store.connect() as db:
        archived=json.loads(db.execute("SELECT values_json FROM catalog_field_archive WHERE record_id=? AND source_schema='legacy-extraction'",(character["id"],)).fetchone()[0])
    assert archived == run["results"]["characters"][0]
    cliche = store.get_record("cliches", result["cliche_ids"][0])
    assert result["cliche_ids"][0] in work["values"]["关联cliché"]
    association = json.loads(cliche["values"]["关联作品"])[0]
    assert association == {"作品ID": result["work_id"], "作品名": "合成样本", "关联内容": run["results"]["cliches"][0]["description"]}
    assert "版本与原作关系" not in work["values"]
    with store.connect() as connection:
        ledger = connection.execute("SELECT * FROM analysis_commits WHERE run_id=?", (run["id"],)).fetchone()
        assert json.loads(ledger["result_json"]) == result
        evidence = json.loads(ledger["evidence_json"])
        assert evidence["document_id"] == run["document_id"]
        assert evidence["version_id"] == run["version_id"]
        assert evidence["results"] == run["results"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    original = documents.get_version(evidence["document_id"], evidence["version_id"])["text"]
    for category in ("characters", "events", "cliches"):
        assert all(item["evidence"] in original for item in evidence["results"][category])
    before = library_snapshot(store)
    assert materializer(run) == result
    assert library_snapshot(store) == before


def test_linked_work_and_existing_human_character_fields_survive(services):
    store, documents, materializer = services
    manual = store.create_record("works", {
        "作品ID": "HUMAN-WORK", "作品": "人工确定的书名", "类别": "推理小说", "作者": "合成作者",
        "发布日期": "2024-06", "版本与原作关系": "精校版；原创", "热度": "不确定",
        "两句话概括": "人工维护的简介。", "剧情补充说明": "人工维护的剧情。",
        "典型桥段（剧透）": "人工自由文本保持原样。",
    })
    human_character = store.create_record("characters", {
        "人物ID": "HUMAN-CHAR", "作品ID": manual["id"], "作品": manual["values"]["作品"],
        "版本": "精校版", "角色": "林舟", "性格": "人工确认的谨慎", "人物补充说明": "手动注释保留。",
    })
    run = analysis(documents, work_id=manual["id"])
    result = materializer(run)
    assert result["work_id"] == manual["id"] and result["created_work"] is False
    now = store.get_record("works", manual["id"])
    for key, value in manual["values"].items():
        if key != "典型桥段（剧透）":
            assert now["values"][key] == value
    assert "人工自由文本保持原样。" in now["values"]["典型桥段（剧透）"]
    assert store.get_record("characters", human_character["id"]) == human_character
    extracted = store.get_record("characters", result["character_ids"][0])
    assert extracted["values"]["版本"] == "精校版"
    assert store.stats() == {"works": 1, "characters": 2, "cliches": 1, "work_cliches": 1}
    before = library_snapshot(store)
    assert materializer(run) == result
    assert library_snapshot(store) == before


def test_modern_link_preserves_manual_work_and_distinct_character_ids(services):
    store, documents, materializer = services
    manual = store.create_record("works", {"作品ID": "CURRENT-WORK", "作品": "人工书名", "两句话概括": "人工简介", "热度权重": "7"})
    person = store.create_record("characters", {"人物ID": "CURRENT-PERSON", "角色": "林舟", "性格": "人工事实", "关联作品": json.dumps([{"作品ID": manual["id"]}])})
    run = analysis(documents, work_id=manual["id"])
    result = materializer(run)
    after = store.get_record("works", manual["id"])
    assert after["values"]["两句话概括"] == "人工简介"
    assert after["values"]["热度权重"] == "7"
    assert store.get_record("characters", person["id"])["values"] == person["values"]
    assert person["id"] not in result["character_ids"]
    extracted = store.get_record("characters", result["character_ids"][0])
    assert json.loads(extracted["values"]["关联作品"])[0]["作品ID"] == manual["id"]
    assert len(store.relations("works", manual["id"])["characters"]) == 2
    assert "剧情补充说明" not in after["values"]
    store.update_record("works", manual["id"], {"作品": "更正书名"}, after["revision"])
    association = json.loads(store.get_record("cliches", result["cliche_ids"][0])["values"]["关联作品"])[0]
    assert association["作品名"] == "更正书名" and "作品" not in association


def test_mid_commit_conflict_rolls_back_new_work_characters_relations_and_ledger(services):
    store, documents, materializer = services
    run = analysis(documents, run_id="rollback-synthetic-run")
    run["results"]["cliches"].append({"name": "钥匙线索", "description": "铜钥匙作为线索。", "evidence": "发现铜钥匙"})
    digest = hashlib.sha256(f"{run['id']}:cliche:1".encode()).hexdigest()[:16]
    collision_id = "C" + str(int(digest, 16))
    store.create_record("cliches", {"cliché内容": collision_id + "｜原有人工桥段", "cliché程度": "人工评分"})
    before = library_snapshot(store)
    with pytest.raises(DataError) as caught:
        materializer(run)
    assert caught.value.status_code == 409
    assert library_snapshot(store) == before
    assert documents.get_document(run["document_id"])["work_id"] is None
    assert store.stats() == {"works": 0, "characters": 0, "cliches": 1, "work_cliches": 0}
    # The failed attempt remains retryable once the collision is resolved.
    collision = store.get_record("cliches", collision_id)
    store.delete_record("cliches", collision_id, collision["revision"])
    result = materializer(run)
    assert result["created_work"]
    assert store.stats() == {"works": 1, "characters": 1, "cliches": 2, "work_cliches": 2}


def test_missing_linked_work_is_reviewable_and_creates_no_fallback_work(services):
    store, documents, materializer = services
    run = analysis(documents, work_id="MISSING-WORK")
    before = library_snapshot(store)
    with pytest.raises(DataError) as caught:
        materializer(run)
    assert caught.value.status_code == 404
    assert library_snapshot(store) == before


def test_lookup_returns_committed_results_and_changed_replay_fails_closed(services):
    store, documents, materializer = services
    run = analysis(documents)
    assert materializer.lookup(run["id"]) is None
    mapping = materializer(run)
    committed = materializer.lookup(run["id"])
    assert committed["materialization"] == mapping
    assert committed["results"] == run["results"]
    assert committed["document_id"] == run["document_id"]
    assert committed["version_id"] == run["version_id"]
    before = library_snapshot(store)
    changed = copy.deepcopy(run)
    changed["results"]["summary"] = "未实际写入主库的新摘要"
    with pytest.raises(DataError) as rejected:
        materializer(changed)
    assert rejected.value.status_code == 409
    assert "另一审核版本" in str(rejected.value)
    assert library_snapshot(store) == before
    # Identity is part of the commit; a copied run ID cannot target a new version.
    changed = copy.deepcopy(run)
    changed["version_id"] = "another-version"
    with pytest.raises(DataError) as rejected:
        materializer(changed)
    assert rejected.value.status_code == 409
    assert library_snapshot(store) == before
