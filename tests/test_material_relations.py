"""Semantic associations never infer links from mere co-occurrence."""
from __future__ import annotations

import sqlite3

import pytest

from inkreference.database import DataError, DataStore


@pytest.fixture
def store(tmp_path):
    data = DataStore(tmp_path / "library.db")
    data.create_record("works", {"作品ID": "W1", "作品": "山海", "版本与原作关系": "小说"})
    data.create_record("works", {"作品ID": "W2", "作品": "山海", "版本与原作关系": "电视剧"})
    data.create_record("cliches", {"cliché内容": "C001｜重逢"})
    data.create_record("cliches", {"cliché内容": "C002｜误会"})
    data.create_record("characters", {"人物ID": "P1", "作品ID": "W1", "角色": "甲", "角色定位": "主角"})
    data.create_record("characters", {"人物ID": "P2", "作品ID": "W1", "角色": "乙", "角色定位": "配角"})
    data.update_record("works", "W1", {"典型桥段（剧透）": "C001：十年后在山中相遇。\nC002：因一封信产生误会。"}, data.get_record("works", "W1")["revision"])
    return data


def relation(**patch):
    return {"id": "R1", "source_table": "characters", "source_id": "P1", "target_table": "characters", "target_id": "P2", "relation_type": "师徒", "description": "甲教授乙剑术。", **patch}


def test_only_direct_relations_include_existing_semantic_detail(store):
    work = store.relations("works", "W1")
    assert len(work["characters"]) == 2
    assert work["characters"][0]["relationship"]["label"] == "登场人物 · 主角"
    assert work["cliches"][0]["relationship"]["description"] == "C001：十年后在山中相遇。\n"
    assert work["cliches"][0]["relationship"]["direction"] == "outgoing"
    assert store.relations("works", "W2") == {"works": [], "characters": [], "cliches": [], "edges": []}
    character = store.relations("characters", "P1")
    assert [item["id"] for item in character["works"]] == ["W1"]
    assert character["characters"] == character["cliches"] == []
    cliche = store.relations("cliches", "C001")
    assert cliche["cliches"] == cliche["characters"] == []
    assert cliche["works"][0]["relationship"]["label"] == "出现于"
    assert "十年后" in cliche["works"][0]["relationship"]["description"]


def test_explicit_relationships_show_type_description_and_correct_direction(store):
    created = store.create_material_relation(relation())
    assert created["revision"] == 1
    outward = store.relations("characters", "P1")["characters"][0]
    inward = store.relations("characters", "P2")["characters"][0]
    assert outward["id"] == "P2"
    assert inward["id"] == "P1"
    assert outward["relationship"]["label"] == "师徒"
    assert outward["relationship"]["description"] == "甲教授乙剑术。"
    assert outward["relationship"]["direction"] == "outgoing"
    assert inward["relationship"]["direction"] == "incoming"
    store.create_material_relation(relation(id="R2", relation_type="同乡", description="出生于同一村落。"))
    linked = store.relations("characters", "P1")["characters"]
    assert len(linked) == 1
    assert [item["type"] for item in linked[0]["relationships"]] == ["师徒", "同乡"]


def test_identical_meaning_cannot_be_duplicated_under_another_id(store):
    store.create_material_relation(relation())
    with pytest.raises(DataError, match="R2 与 R1"):
        store.create_material_relation(relation(id="R2"))
    assert len(store.list_material_relations()) == 1


@pytest.mark.parametrize("patch,message", [
    ({"source_table": "cliches", "source_id": "C001", "target_table": "cliches", "target_id": "C002"}, "桥段不能关联桥段"),
    ({"target_id": "P1"}, "自身"),
    ({"target_id": "MISSING"}, "人物不存在"),
    ({"target_table": "else"}, "target_table"),
    ({"relation_type": " "}, "关系类型"),
    ({"id": "R/1"}, "关联 ID"),
    ({"metadata_json": "[broken"}, "metadata_json"),
])
def test_invalid_association_is_rejected_before_writing(store, patch, message):
    with pytest.raises(DataError, match=message):
        store.create_material_relation(relation(**patch))
    assert store.list_material_relations() == []


def test_future_relationship_fields_are_preserved_and_revisions_prevent_stale_edits(store):
    created = store.create_material_relation(relation(metadata_json='{"年代":"未知"}', evidence="第一章\n雪夜传剑。", confidence="待核实"))
    assert created["metadata"] == {"年代": "未知", "evidence": "第一章\n雪夜传剑。", "confidence": "待核实"}
    updated = store.update_material_relation("R1", {"description": "补充说明"}, created["revision"])
    assert updated["metadata"] == created["metadata"]
    assert updated["revision"] == 2
    with pytest.raises(DataError, match="已被修改"):
        store.update_material_relation("R1", {"description": "过时"}, created["revision"])
    with pytest.raises(DataError, match="已被修改"):
        store.delete_material_relation("R1", created["revision"])
    store.delete_material_relation("R1", updated["revision"])
    recreated = store.create_material_relation(relation())
    assert recreated["revision"] > updated["revision"]


def test_transaction_import_helper_preserves_unknown_fields_and_is_idempotent(store):
    with store.transaction() as connection:
        first = store._upsert_material_relation(connection, relation(证据="逐字原文"))
        store._upsert_material_relation(connection, relation(证据="逐字原文"))
    assert first == "R1"
    assert store.list_material_relations()[0]["revision"] == 1
    assert store.list_material_relations()[0]["metadata"] == {"证据": "逐字原文"}
    with pytest.raises(DataError):
        with store.transaction() as connection:
            store._upsert_material_relation(connection, relation(id="R2", relation_type="同乡"))
            store._upsert_material_relation(connection, relation(id="R3", target_id="NOPE"))
    assert [item["id"] for item in store.list_material_relations()] == ["R1"]
    with store.transaction() as connection:
        store._upsert_material_relation(connection, relation(description="skip"), conflict_policy="skip")
    assert store.list_material_relations()[0]["description"] == "甲教授乙剑术。"


def test_deletion_requires_confirmation_and_cascades_polymorphic_endpoints(store):
    store.create_material_relation(relation())
    old = store.get_record("characters", "P2")
    with pytest.raises(DataError, match="关联"):
        store.delete_record("characters", "P2", old["revision"])
    store.delete_record("characters", "P2", old["revision"], cascade=True)
    assert store.list_material_relations() == []
    # A work deletion also cascades explicit edges belonging to its children.
    store.create_material_relation(relation(id="R3", target_table="works", target_id="W2"))
    store.delete_record("works", "W1", store.get_record("works", "W1")["revision"], cascade=True)
    assert store.list_material_relations() == []


def test_sqlite_triggers_reject_missing_endpoints_and_cliche_pairs(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as connection:
            connection.execute("INSERT INTO material_relations (id,source_table,source_id,target_table,target_id,relation_type) VALUES ('bad','characters','P1','characters','missing','师徒')")
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as connection:
            connection.execute("INSERT INTO material_relations (id,source_table,source_id,target_table,target_id,relation_type) VALUES ('bad','cliches','C001','cliches','C002','相似')")
    assert store.list_material_relations() == []


def test_schema_two_upgrade_retains_values_and_authoritative_v8_links(store):
    before = {table: store.all_records(table) for table in ("works", "characters", "cliches")}
    stats = store.stats()
    with store.transaction() as connection:
        for trigger in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            connection.execute(f'DROP TRIGGER "{trigger[0]}"')
        connection.execute("DROP TABLE material_relations")
        connection.execute("DELETE FROM schema_migrations WHERE version=3")
    upgraded = DataStore(store.db_path)
    assert upgraded.stats() == stats
    assert {table: upgraded.all_records(table) for table in before} == before
    assert upgraded.list_material_relations() == []
    with upgraded.connect() as connection:
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5, 6, 7]
