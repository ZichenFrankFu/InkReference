"""Strict legacy CSV parsing, reviewable imports, and BOM CSV exports."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass, field
from typing import Any

from .database import (
    TABLES, LEGACY_TABLES, SCHEMA4_COLUMNS, SCHEMA5_COLUMNS, modern_values, WORK_RELATION_FIELD, DataError, DataStore, parse_work_relations,
    record_id, require_table, validate_values,
)

# Long synopses are valid CSV fields; the stdlib default is only 128 KiB.
csv.field_size_limit(32 * 1024 * 1024)

COMPATIBLE_NAMES = {
    "works": "works_library_v8.csv",
    "characters": "character_database_v8.csv",
    "cliches": "cliche_database_v8.csv",
}


@dataclass
class ImportTable:
    table: str
    filename: str
    rows: list[dict[str, str]]


@dataclass
class ImportBatch:
    tables: list[ImportTable] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    snapshot: dict[str, int] | None = None


def _decode(data: bytes) -> tuple[str, str]:
    if b"\x00" in data:
        raise DataError(422, "检测到 NUL 字节；请将 CSV 另存为 UTF-8 或 GB18030")
    try:
        return data.decode("utf-8-sig", errors="strict"), "UTF-8"
    except UnicodeDecodeError:
        try:
            return data.decode("gb18030", errors="strict"), "GB18030"
        except UnicodeDecodeError as exc:
            raise DataError(422, "无法严格按 UTF-8 或 GB18030 解码；没有替换或删除任何字节") from exc


def parse_files(files: list[tuple[str, bytes]]) -> ImportBatch:
    batch = ImportBatch()
    if not files:
        batch.errors.append("请选择至少一个 CSV 文件")
        return batch
    seen: dict[tuple[str, str], str] = {}
    for filename, data in files:
        try:
            text, encoding = _decode(data)
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            header = next(reader, None)
            if not header:
                raise DataError(422, "文件为空或缺少表头")
            if len(header) != len(set(header)):
                raise DataError(422, "CSV 存在重复表头")
            matches = [table for table in TABLES if set(header) in (set(TABLES[table]["columns"]),set(SCHEMA4_COLUMNS[table]),set(SCHEMA5_COLUMNS[table]),set(LEGACY_TABLES[table]["columns"]))]
            if len(matches) != 1:
                raise DataError(422, "表头无法识别：必须包含作品、角色或桥段表的全部字段，且不能含未知字段")
            table = matches[0]
            rows = []
            for row in reader:
                if not row:
                    continue
                if len(row) != len(header):
                    raise DataError(422, f"第 {reader.line_num} 行字段数为 {len(row)}，应为 {len(header)}")
                values = validate_values(table, dict(zip(header, row)))
                identifier = record_id(table, values)
                key = (table, identifier)
                if key in seen:
                    raise DataError(422, f"重复 ID {identifier}（首次出现在 {seen[key]}）")
                seen[key] = filename
                rows.append(values)
            batch.tables.append(ImportTable(table, filename, rows))
            if encoding != "UTF-8":
                batch.warnings.append(f"{filename}：已使用 GB18030 严格解码；导出统一为 UTF-8 BOM")
        except (DataError, csv.Error) as exc:
            batch.errors.append(f"{filename}：{exc}")
    if batch.tables and not any(table.rows for table in batch.tables):
        batch.warnings.append("所有 CSV 均只有表头：导入不会添加、修改或删除记录")
    return batch


def _snapshot(connection: sqlite3.Connection) -> dict[str, int]:
    return {f"{table}/{row['id']}": row["revision"] for table in TABLES for row in connection.execute(f"SELECT id,revision FROM {table}")}


def _apply_import(store: DataStore, connection: sqlite3.Connection, batch: ImportBatch, policy: str) -> dict[str, int]:
    if policy not in {"skip", "update", "error"}:
        raise DataError(422, "导入冲突策略必须为 skip、update 或 error")
    if batch.errors:
        raise DataError(422, {"errors": batch.errors})
    if not batch.tables:
        raise DataError(422, "导入批次为空")
    selected: dict[str, dict[str, dict[str, str]]] = {table: {} for table in TABLES}
    actions: dict[tuple[str, str], str] = {}
    counts = {"imported": 0, "updated": 0, "skipped": 0}
    for source in batch.tables:
        for original in source.rows:
            values = dict(original)
            identifier = record_id(source.table, values)
            existing = connection.execute(f"SELECT * FROM {source.table} WHERE id = ?", (identifier,)).fetchone()
            if existing is not None:
                if policy == "error":
                    raise DataError(409, f"ID 已存在：{identifier}；请选择跳过或显式更新")
                current_values=store._render(connection, source.table, existing)["values"]
                if source.table=="cliches" and "cliché评分" in current_values and "cliché评分" in values:
                    same={k:v for k,v in current_values.items() if k!="cliché评分"}=={k:v for k,v in values.items() if k!="cliché评分"}
                else:same=current_values==values
                if policy == "skip" or same:
                    counts["skipped"] += 1
                    continue
                action = "updated"
            else:
                action = "imported"
            counts[action] += 1
            selected[source.table][identifier] = values
            actions[(source.table, identifier)] = action

    if all(modern_values(table,values) for table,rows in selected.items() for values in rows.values()):
        for table in ("works","cliches","characters"):
            for identifier,values in selected[table].items():
                if actions[(table,identifier)] == "imported":
                    store._insert_raw(connection,table,values)
                else:
                    revision = store._row(connection,table,identifier)["revision"]
                    store._update_raw(connection,table,identifier,values,revision)
        # A complete batch supplies both views. Validate before materialization;
        # don't silently union conflicting claims from different file versions.
        store._rebuild_library_links(connection)
        return counts

    # Parents and cliches exist before relation validation, independent of file order.
    for table in ("works", "cliches"):
        for identifier, values in selected[table].items():
            if actions[(table, identifier)] == "imported":
                store._insert_raw(connection, table, values)
            else:
                revision = store._row(connection, table, identifier)["revision"]
                store._update_raw(connection, table, identifier, values, revision)

    work_links = {identifier: parse_work_relations(values[WORK_RELATION_FIELD])[1] for identifier, values in selected["works"].items()}
    cliche_links = {identifier: store._appearance_ids(connection, values["出现作品"]) for identifier, values in selected["cliches"].items()}
    # If both CSV views were explicitly selected, inconsistent claims are errors.
    for wid, links in work_links.items():
        for cid, appearances in cliche_links.items():
            if (cid in links) != (wid in appearances):
                raise DataError(422, f"双向桥段关联不一致：作品 {wid} 与桥段 {cid}")
    for identifier, values in selected["works"].items():
        store._sync_work(connection, identifier, values[WORK_RELATION_FIELD])
    for identifier, values in selected["cliches"].items():
        store._sync_cliche(connection, identifier, values["出现作品"])
    for identifier, values in selected["characters"].items():
        if actions[("characters", identifier)] == "imported":
            store._insert_raw(connection, "characters", values)
        else:
            revision = store._row(connection, "characters", identifier)["revision"]
            store._update_raw(connection, "characters", identifier, values, revision)
    return counts


def preview_import(store: DataStore, batch: ImportBatch) -> dict[str, Any]:
    """Validate a prospective update on an in-memory database; original is untouched."""
    errors = list(batch.errors)
    tables = []
    with store.connect() as source:
        source.execute("BEGIN")
        batch.snapshot = _snapshot(source)
        initial_stats = store._stats(source)
        for item in batch.tables:
            existing = sum(source.execute(f"SELECT 1 FROM {item.table} WHERE id = ?", (record_id(item.table, values),)).fetchone() is not None for values in item.rows)
            tables.append({"table": item.table, "filename": item.filename, "rows": len(item.rows), "new": len(item.rows) - existing, "existing": existing})
        simulation = sqlite3.connect(":memory:")
        simulation.row_factory = sqlite3.Row
        simulation.execute("PRAGMA foreign_keys = ON")
        source.backup(simulation)
    stats = initial_stats
    try:
        if not errors:
            simulation.execute("BEGIN IMMEDIATE")
            _apply_import(store, simulation, batch, "update")
            stats = store._stats(simulation)
    except (DataError, sqlite3.IntegrityError) as exc:
        errors.append(str(exc))
    finally:
        simulation.close()
    return {"tables": tables, "errors": errors, "warnings": list(batch.warnings), "stats": stats}


def commit_import(store: DataStore, batch: ImportBatch, policy: str = "skip") -> dict[str, Any]:
    with store.transaction() as connection:
        if batch.snapshot is None:
            raise DataError(409, "请先预览导入批次")
        if _snapshot(connection) != batch.snapshot:
            raise DataError(409, "预览后数据库已发生变化，请重新预览后导入")
        try:
            counts = _apply_import(store, connection, batch, policy)
        except sqlite3.IntegrityError as exc:
            raise DataError(422, "导入违反数据库约束；未写入任何记录") from exc
        return {**counts, "stats": store._stats(connection)}


def _csv_bytes(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def export_table(store: DataStore, table: str) -> bytes:
    require_table(table)
    with store.connect() as connection:
        connection.execute("BEGIN")
        rows = [item["values"] for item in store._all(connection, table)]
    columns = TABLES[table]["columns"] if not rows or modern_values(table,rows[0]) else LEGACY_TABLES[table]["columns"]
    return _csv_bytes(columns, rows)


def export_zip(store: DataStore, mode: str = "compatible") -> bytes:
    if mode not in {"compatible", "full"}:
        raise DataError(422, "导出模式必须为 compatible 或 full")
    output = io.BytesIO()
    with store.connect() as connection, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        connection.execute("BEGIN")
        for table, spec in TABLES.items():
            records = store._all(connection, table)
            rows = [item["values"] for item in records]
            columns = list(spec["columns"] if not rows or modern_values(table,rows[0]) else LEGACY_TABLES[table]["columns"])
            if mode == "full":
                columns += ["_record_id", "_revision"]
                rows = [{**item["values"], "_record_id": item["id"], "_revision": item["revision"]} for item in records]
            archive.writestr(COMPATIBLE_NAMES[table] if mode == "compatible" else f"{table}.csv", _csv_bytes(columns, rows))
        if mode == "full":
            columns = ["work_id", "cliche_id", "detail", "work_position", "cliche_position"]
            rows = [dict(row) for row in connection.execute("SELECT * FROM work_cliches ORDER BY work_id,work_position,cliche_id")]
            archive.writestr("work_cliches.csv", _csv_bytes(columns, rows))
            columns = ["id", "source_table", "source_id", "target_table", "target_id", "relation_type", "description", "metadata_json", "revision"]
            rows = [dict(row) for row in connection.execute("SELECT * FROM material_relations ORDER BY id")]
            archive.writestr("material_relations.csv", _csv_bytes(columns, rows))
            manifest = {"format": "InkReference full table export", "format_version": 1, "schema_version": 3, "encoding": "UTF-8 BOM", "tables": [*TABLES, "work_cliches", "material_relations"], "stats": store._stats(connection), "restore": "Use compatible export for v8 CSV import; full export retains relation details and record revisions. This is a data export, not a full project backup."}
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return output.getvalue()


def export_legacy_table(store: DataStore, table: str) -> bytes:
    """Explicit v8 view: export archived legacy rows without inventing old fields."""
    require_table(table)
    with store.connect() as connection:
        connection.execute("BEGIN")
        rows = []
        for record in connection.execute(f"SELECT id,values_json FROM {table} ORDER BY sort_order,id"):
            values = json.loads(record["values_json"])
            if modern_values(table,values):
                archive = connection.execute("SELECT values_json FROM legacy_values WHERE table_key=? AND record_id=?",(table,record["id"])).fetchone()
                if archive is None:
                    raise DataError(422,f"{TABLES[table]['label']} {record['id']} 没有旧版字段归档；请使用当前三表导出，不能无损生成旧版 v8")
                values = json.loads(archive[0])
            rows.append(values)
    return _csv_bytes(LEGACY_TABLES[table]["columns"],rows)


def export_legacy_zip(store: DataStore) -> bytes:
    output = io.BytesIO()
    names = {"works":"works_library_v8.csv","characters":"character_database_v8.csv","cliches":"cliche_database_v8.csv"}
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
        for table in TABLES:
            archive.writestr(names[table],export_legacy_table(store,table))
    return output.getvalue()
