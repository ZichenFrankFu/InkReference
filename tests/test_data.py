"""Data contract tests use invented works; no uploaded user data is committed."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from inkreference.csvio import (
    COMPATIBLE_NAMES, commit_import, export_table, export_zip, parse_files,
    preview_import,
)
from inkreference.database import (
    TABLES, WORK_COLUMNS, DataError, DataStore, split_occurrences,
)

FIXTURES = Path(__file__).parent / "fixtures"


def csv_bytes(columns, rows, encoding="utf-8-sig"):
    target = io.StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return target.getvalue().encode(encoding)


def rows_from_bytes(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))


def fixture_batch():
    # Deliberately reverse dependency order: characters must not need file order.
    return parse_files([(f"{table}.csv", (FIXTURES / f"{table}.csv").read_bytes()) for table in ("characters", "cliches", "works")])


@pytest.fixture
def store(tmp_path):
    return DataStore(tmp_path / "library.sqlite3")


@pytest.fixture
def populated(store):
    batch = fixture_batch()
    assert preview_import(store, batch)["errors"] == []
    commit_import(store, batch, "skip")
    return store


def test_semantic_csv_roundtrip_preserves_all_fields_and_gaps(populated):
    assert populated.stats() == {"works": 3, "characters": 3, "cliches": 2, "work_cliches": 3}
    for table in TABLES:
        expected = rows_from_bytes((FIXTURES / f"{table}.csv").read_bytes())
        exported = export_table(populated, table)
        assert exported.startswith(b"\xef\xbb\xbf")
        assert rows_from_bytes(exported) == expected
    assert populated.get_record("works", "W950")["values"]["典型桥段（剧透）"] == "这是一段没有 C 编号的自由桥段。"
    assert [record["id"] for record in populated.all_records("works")] == ["W900", "W902", "W950"]


def test_import_is_atomic_and_can_resolve_new_parent_and_child(store):
    batch = fixture_batch()
    batch.tables[0].rows[0]["作品ID"] = "MISSING"
    assert preview_import(store, batch)["errors"]
    with pytest.raises(DataError):
        commit_import(store, batch, "skip")
    assert store.stats() == {"works": 0, "characters": 0, "cliches": 0, "work_cliches": 0}
    assert preview_import(store, fixture_batch())["errors"] == []


def test_conflict_policies_and_noop_update_are_idempotent(populated):
    batch = fixture_batch()
    preview_import(populated, batch)
    with pytest.raises(DataError) as rejected:
        commit_import(populated, batch, "error")
    assert rejected.value.status_code == 409
    before = populated.get_record("works", "W900")["revision"]
    result = commit_import(populated, batch, "update")
    assert (result["imported"], result["updated"], result["skipped"]) == (0, 0, 8)
    assert populated.get_record("works", "W900")["revision"] == before
    batch = fixture_batch()
    batch.tables[-1].rows[0]["热度"] = "99"
    preview_import(populated, batch)
    assert commit_import(populated, batch, "skip")["skipped"] == 8
    assert populated.get_record("works", "W900")["values"]["热度"] == "baseline"
    preview_import(populated, batch)
    assert commit_import(populated, batch, "update")["updated"] == 1
    assert populated.get_record("works", "W900")["values"]["热度"] == "99"


def test_import_rejects_changes_since_preview(populated):
    batch = fixture_batch()
    preview_import(populated, batch)
    item = populated.get_record("works", "W900")
    populated.update_record("works", "W900", {"热度": "42"}, item["revision"])
    with pytest.raises(DataError) as rejected:
        commit_import(populated, batch, "update")
    assert rejected.value.status_code == 409
    assert populated.get_record("works", "W900")["values"]["热度"] == "42"


def test_inconsistent_bidirectional_claims_do_not_write(store):
    batch = fixture_batch()
    batch.tables[1].rows[0]["出现作品"] = ""
    assert "双向桥段关联不一致" in " ".join(preview_import(store, batch)["errors"])
    with pytest.raises(DataError):
        commit_import(store, batch, "update")
    assert store.stats()["works"] == 0


def test_work_title_and_edition_updates_propagate_and_revision_protects(populated):
    old = populated.get_record("works", "W900")
    character = populated.get_record("characters", "CH-W900-01")
    bridge = populated.get_record("cliches", "C007")
    current = populated.update_record("works", "W900", {"作品": "夜航新版", "版本与原作关系": "2024年修订；原创"}, old["revision"])
    assert current["revision"] == old["revision"] + 1
    child = populated.get_record("characters", character["id"])
    assert (child["values"]["作品"], child["values"]["版本"]) == ("夜航新版", "2024年修订")
    assert child["revision"] > character["revision"]
    bridge_now = populated.get_record("cliches", "C007")
    assert bridge_now["values"]["出现作品"].startswith("夜航新版〔2024年修订；原创〕")
    assert bridge_now["revision"] > bridge["revision"]
    with pytest.raises(DataError) as rejected:
        populated.update_record("works", "W900", {"热度": "8"}, old["revision"])
    assert rejected.value.status_code == 409


def test_relationship_edits_update_both_views(populated):
    work = populated.get_record("works", "W900")
    populated.update_record("works", "W900", {"典型桥段（剧透）": "C007：留下一个实例。"}, work["revision"])
    assert populated.get_record("cliches", "C019")["values"]["出现作品"] == ""
    bridge = populated.get_record("cliches", "C007")
    populated.update_record("cliches", "C007", {"出现作品": "夜航〔2021年电影；改编自小说《夜航》〕"}, bridge["revision"])
    assert populated.get_record("works", "W900")["values"]["典型桥段（剧透）"] == ""
    assert populated.stats()["work_cliches"] == 1
    assert rows_from_bytes(export_table(populated, "cliches"))[1]["出现作品"].startswith("夜航〔2021年")


def test_character_unknown_parent_and_identifier_change_are_blocked(populated):
    with pytest.raises(DataError):
        populated.create_record("characters", {"人物ID": "NEW", "作品ID": "UNKNOWN"})
    record = populated.get_record("works", "W900")
    with pytest.raises(DataError):
        populated.update_record("works", "W900", {"作品ID": "OTHER"}, record["revision"])
    assert populated.get_record("works", "W900")["revision"] == record["revision"]


def test_cascade_deletion_keeps_unrelated_records_and_no_dangling_views(populated):
    work = populated.get_record("works", "W900")
    with pytest.raises(DataError) as rejected:
        populated.delete_record("works", "W900", work["revision"])
    assert rejected.value.status_code == 409
    assert populated.delete_record("works", "W900", work["revision"], cascade=True) == {"deleted": 2}
    assert populated.stats() == {"works": 2, "characters": 2, "cliches": 2, "work_cliches": 1}
    assert populated.get_record("cliches", "C019")["values"]["出现作品"] == ""
    assert "2020年" not in populated.get_record("cliches", "C007")["values"]["出现作品"]
    bridge = populated.get_record("cliches", "C007")
    populated.delete_record("cliches", "C007", bridge["revision"], cascade=True)
    assert populated.stats()["works"] == 2
    assert populated.get_record("works", "W902")["values"]["典型桥段（剧透）"] == ""


def test_strict_encodings_multiline_and_bracket_semicolons(store):
    values = {column: "" for column in WORK_COLUMNS}
    values.update({"作品ID": "GB1", "作品": "编码样本", "两句话概括": "第一行\n第二行,含逗号", "热度": "不确定"})
    batch = parse_files([("gb.csv", csv_bytes(WORK_COLUMNS, [values], "gb18030"))])
    assert batch.warnings
    assert not preview_import(store, batch)["errors"]
    commit_import(store, batch)
    assert rows_from_bytes(export_table(store, "works")) == [values]
    assert split_occurrences("夜航〔第一部；原创〕；夜航〔第二部；改编自《书》〕") == ["夜航〔第一部；原创〕", "夜航〔第二部；改编自《书》〕"]
    assert parse_files([("broken.csv", b"\xff\xff\xff")]).errors
    assert parse_files([("nul.csv", b"a\0b")]).errors


@pytest.mark.parametrize("files", [[], [("empty.csv", b"")], [("duplicate.csv", "作品ID,作品ID\n1,2".encode())], [("unknown.csv", "unknown\nx".encode())]])
def test_empty_and_unknown_or_duplicate_headers_are_reviewable_errors(store, files):
    batch = parse_files(files)
    assert preview_import(store, batch)["errors"]


def test_duplicate_ids_across_files_and_extra_cells_are_rejected():
    fixture = (FIXTURES / "works.csv").read_bytes()
    assert parse_files([("a.csv", fixture), ("b.csv", fixture)]).errors
    header = ",".join(WORK_COLUMNS)
    malformed = (header + "\n" + ",".join(["x"] * (len(WORK_COLUMNS) + 1))).encode()
    assert parse_files([("extra.csv", malformed)]).errors


def test_export_archives_and_migration_reopen(populated):
    with zipfile.ZipFile(io.BytesIO(export_zip(populated))) as archive:
        assert set(archive.namelist()) == set(COMPATIBLE_NAMES.values())
        restored = DataStore(Path(populated.db_path).with_name("restore.sqlite3"))
        batch = parse_files([(name, archive.read(name)) for name in archive.namelist()])
        assert not preview_import(restored, batch)["errors"]
        commit_import(restored, batch)
        assert restored.stats() == populated.stats()
    with zipfile.ZipFile(io.BytesIO(export_zip(populated, "full"))) as archive:
        assert set(archive.namelist()) == {"works.csv", "characters.csv", "cliches.csv", "work_cliches.csv", "material_relations.csv", "manifest.json"}
        assert json.loads(archive.read("manifest.json"))["stats"] == populated.stats()
        assert len(rows_from_bytes(archive.read("work_cliches.csv"))) == 3
    reopened = DataStore(populated.db_path)
    assert reopened.stats() == populated.stats()
    with sqlite3.connect(populated.db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,)]


def test_filter_search_and_relations_respect_editions(populated):
    assert populated.list_records("works", q="夜航")["total"] == 2
    assert populated.list_records("characters", work_id="W902")["items"][0]["id"] == "CH-W902-01"
    assert populated.list_records("works", cliche_id="C007")["total"] == 2
    relations = populated.relations("works", "W900")
    assert len(relations["characters"]) == 1
    assert {item["id"] for item in relations["cliches"]} == {"C007", "C019"}
    assert populated.list_records("works", offset=1, limit=1)["items"][0]["id"] == "W902"
    with pytest.raises(DataError):
        populated.list_records("works", limit=0)


def test_empty_and_partial_archives_roundtrip(store, tmp_path):
    for populated in (False, True):
        if populated:
            store.create_record("works", {"作品ID": "ONLY", "作品": "单表", "热度": "未知"})
        restored = DataStore(tmp_path / f"restore-{populated}.sqlite3")
        with zipfile.ZipFile(io.BytesIO(export_zip(store))) as archive:
            batch = parse_files([(name, archive.read(name)) for name in archive.namelist()])
        assert preview_import(restored, batch)["errors"] == []
        result = commit_import(restored, batch)
        assert result["stats"] == store.stats()
        assert result["imported"] == int(populated)


def test_duplicate_exact_editions_keep_ids_and_qualified_csv_roundtrip(populated, tmp_path):
    bridge_before = populated.get_record("cliches", "C007")
    duplicate = populated.get_record("works", "W900")["values"].copy()
    duplicate.update({"作品ID": "W901", "典型桥段（剧透）": ""})
    populated.create_record("works", duplicate)
    bridge = populated.get_record("cliches", "C007")
    assert bridge["revision"] > bridge_before["revision"]
    assert "夜航〔2020年剧集；原创〕 ⟦ID:W900⟧" in bridge["values"]["出现作品"]
    fresh = DataStore(tmp_path / "duplicate-roundtrip.sqlite3")
    with zipfile.ZipFile(io.BytesIO(export_zip(populated))) as archive:
        batch = parse_files([(name, archive.read(name)) for name in archive.namelist()])
    assert preview_import(fresh, batch)["errors"] == []
    commit_import(fresh, batch)
    assert fresh.stats() == populated.stats()
    assert fresh.list_records("cliches", work_id="W901")["total"] == 0
    assert fresh.list_records("cliches", work_id="W900")["total"] == 2
    wrong = fresh.get_record("cliches", "C007")
    with pytest.raises(DataError):
        fresh.update_record("cliches", "C007", {"出现作品": "错误〔2020年剧集；原创〕 ⟦ID:W900⟧"}, wrong["revision"])


def test_edit_can_create_label_collision_and_delete_can_resolve_it(populated):
    current = populated.get_record("works", "W950")
    populated.update_record("works", "W950", {"作品": "夜航", "版本与原作关系": "2020年剧集；原创"}, current["revision"])
    bridge = populated.get_record("cliches", "C007")
    assert "⟦ID:W900⟧" in bridge["values"]["出现作品"]
    current = populated.get_record("works", "W950")
    populated.delete_record("works", "W950", current["revision"], cascade=True)
    bridge_after = populated.get_record("cliches", "C007")
    assert "⟦ID:" not in bridge_after["values"]["出现作品"]
    assert bridge_after["revision"] > bridge["revision"]


def test_character_can_change_parent_with_only_work_id_patch(populated):
    current = populated.get_record("characters", "CH-W900-01")
    updated = populated.update_record("characters", current["id"], {"作品ID": "W950"}, current["revision"])
    assert updated["values"]["作品"] == "白昼"
    assert updated["values"]["版本"] == "完整小说"


def test_same_id_recreation_rejects_stale_edit_and_import_preview(store):
    old = store.create_record("works", {"作品ID": "REUSED", "作品": "旧记录"})
    batch = parse_files([("works.csv", export_table(store, "works"))])
    batch.tables[0].rows[0]["作品"] = "旧批次想写入的标题"
    preview_import(store, batch)
    store.delete_record("works", "REUSED", old["revision"])
    new = store.create_record("works", {"作品ID": "REUSED", "作品": "新记录"})
    assert new["revision"] > old["revision"]
    with pytest.raises(DataError) as rejected:
        store.update_record("works", "REUSED", {"作品": "过期编辑"}, old["revision"])
    assert rejected.value.status_code == 409
    with pytest.raises(DataError) as rejected:
        commit_import(store, batch, "update")
    assert rejected.value.status_code == 409
    assert store.get_record("works", "REUSED")["values"]["作品"] == "新记录"


def test_version_one_database_upgrades_without_changing_records(populated):
    before = populated.get_record("works", "W900")
    with sqlite3.connect(populated.db_path) as connection:
        connection.execute("DROP TABLE record_versions")
        connection.execute("DELETE FROM schema_migrations WHERE version = 2")
    upgraded = DataStore(populated.db_path)
    assert upgraded.get_record("works", "W900") == before
    upgraded.delete_record("works", "W900", before["revision"], cascade=True)
    recreated = upgraded.create_record("works", {"作品ID": "W900", "作品": "升级后重建"})
    assert recreated["revision"] > before["revision"]


@pytest.mark.parametrize("identifier", ["W/1", "W\\1", "W\x00A", "W\x1fA", "W\x7fA", "W⟦1", "W⟧1", ".", ".."])
def test_identifiers_must_be_addressable_url_segments(store, identifier):
    parent = store.create_record("works", {"作品ID": "PARENT", "作品": "合法父作品"})
    for table, values in (
        ("works", {"作品ID": identifier, "作品": "无效编号"}),
        ("characters", {"人物ID": identifier, "作品ID": parent["id"]}),
    ):
        with pytest.raises(DataError) as rejected:
            store.create_record(table, values)
        assert rejected.value.status_code == 422
        assert "ID" in str(rejected.value)
    assert store.stats() == {"works": 1, "characters": 0, "cliches": 0, "work_cliches": 0}
