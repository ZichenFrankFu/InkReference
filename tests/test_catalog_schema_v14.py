"""Schema 7 upgrades and portable schema 6 compatibility use invented text."""
from __future__ import annotations

import json

import pytest

from inkreference import catalog_schema_v11 as schema
from inkreference.database import DataStore
from inkreference.library_exchange import (FORMAT, FORMAT_VERSION, _digest, _export_csv, _export_db,
    _export_xlsx, commit_library_import, export_library, parse_library_files, preview_library_import)


def test_schema6_database_upgrade_archives_exact_values_and_keeps_revisions(tmp_path):
    path = tmp_path / "upgrade.db"
    store = DataStore(path)
    values = {column: schema.JSON_DEFAULTS.get(column, "") for column in schema.SCHEMA6_COLUMNS["works"]}
    values.update({"作品ID": "W-external-001", "作品": "同名合成作品", "作者": "  原样作者  ", "发布日期": "2025-07", "剧情梗概": "第一行\r\n第二行\n", "讨论热度": '{"min":2,"max":4,"rationale":"未分类说明"}'})
    store.create_record("works", values)
    original_json = json.dumps(values, ensure_ascii=False, indent=2)
    with store.transaction() as db:
        db.execute("UPDATE works SET values_json=?,revision=17 WHERE id=?", (original_json, values["作品ID"]))
        db.execute("UPDATE record_versions SET revision=17 WHERE table_key='works' AND record_id=?", (values["作品ID"],))
        db.execute("DELETE FROM schema_migrations WHERE version=7")
    upgraded = DataStore(path)
    record = upgraded.get_record("works", values["作品ID"])
    assert record["revision"] == 17
    assert {key: record["values"][key] for key in values} == values
    assert record["values"]["作品题材"] == record["values"]["作品风格"] == ""
    with upgraded.connect() as db:
        archived = db.execute("SELECT values_json FROM catalog_field_archive WHERE table_key='works' AND record_id=? AND source_schema='6'", (values["作品ID"],)).fetchone()[0]
        assert archived == original_json
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 7
    assert DataStore(path).get_record("works", values["作品ID"]) == record


def v6_rows():
    values = {column: schema.JSON_DEFAULTS.get(column, "") for column in schema.SCHEMA6_COLUMNS["works"]}
    values.update({"作品ID": "W-origin-002", "作品": "合成作品", "发布日期": "2024", "剧情梗概": "=文本\r\n第二行\n", "热度权重": "未知原文"})
    rows = {"works": [values], "characters": [], "cliches": []}
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "schema_version": 6, "bundle_id": "synthetic-six", "tables": {
        table: {"columns": columns, "rows": len(rows[table]), "sha256": _digest(rows[table])} for table, columns in schema.SCHEMA6_COLUMNS.items()}}
    return rows, manifest


@pytest.mark.parametrize("format,writer,extension", [("db", _export_db, "db"), ("csv", _export_csv, "zip"), ("xlsx", _export_xlsx, "xlsx")])
def test_schema6_portable_import_then_schema7_roundtrip_keeps_original_fields(tmp_path, format, writer, extension):
    rows, manifest = v6_rows()
    batch = parse_library_files([("six." + extension, writer(rows, manifest))])
    assert not batch.errors and not batch.warnings, (batch.errors, batch.warnings)
    target = DataStore(tmp_path / "target.db")
    assert not preview_library_import(target, batch)["errors"]
    commit_library_import(target, batch)
    record = target.get_record("works", "W-origin-002")
    assert {key: record["values"][key] for key in rows["works"][0]} == rows["works"][0]
    assert record["values"]["作品题材"] == ""
    with target.connect() as db:
        original = db.execute("SELECT values_json FROM catalog_field_archive WHERE record_id='W-origin-002' AND source_schema='6'").fetchone()[0]
        assert json.loads(original) == rows["works"][0]
    target.update_record("works", record["id"], {"作品题材": "未知题材\n保留原文", "作品风格": "赛博朋克"}, record["revision"])
    second = DataStore(tmp_path / "second.db")
    seven = parse_library_files([("seven." + extension, export_library(target, format))])
    assert seven.manifest["schema_version"] == 7
    assert not preview_library_import(second, seven)["errors"]
    commit_library_import(second, seven)
    assert second.get_record("works", record["id"])["values"] == target.get_record("works", record["id"])["values"]
    with second.connect() as db:
        assert json.loads(db.execute("SELECT values_json FROM catalog_field_archive WHERE record_id='W-origin-002' AND source_schema='6'").fetchone()[0]) == rows["works"][0]
