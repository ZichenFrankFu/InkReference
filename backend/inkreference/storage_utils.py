"""Small, versioned SQLite index plan shared by lazily created local stores.

The caller owns its transaction.  No connection is retained, no source rows are
rewritten, and a store whose tables have not been created is simply skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3


INDEX_PLAN_VERSION = 1


@dataclass(frozen=True)
class IndexSpec:
    namespace: str
    table: str
    name: str
    expression: str
    columns: tuple[str, ...]

    @property
    def sql(self) -> str:
        return f'CREATE INDEX IF NOT EXISTS "{self.name}" ON "{self.table}" ({self.expression})'


def json_field_expression(field: str, *, prefix: str = "") -> str:
    """Quote a schema-owned field; callers must validate against their schema."""
    path = '$."' + field.replace('"', '\\"') + '"'
    return f"json_extract({prefix}values_json,'{path.replace(chr(39), chr(39) * 2)}')"


def _spec(namespace: str, table: str, suffix: str, expression: str, *columns: str) -> IndexSpec:
    return IndexSpec(namespace, table, f"ix_growth_{table}_{suffix}_v1", expression, tuple(columns))


INDEX_SPECS = (
    *(_spec("catalog", table, "order", "sort_order,id", "sort_order", "id")
      for table in ("works", "characters", "cliches")),
    *(_spec("catalog", table, suffix, json_field_expression(field) + ",sort_order,id", "values_json", "sort_order", "id")
      for table, field, suffix in (
          ("works", "类别", "category"), ("works", "用户是否完整体验过", "seen"),
          ("characters", "角色定位", "role"), ("characters", "性格基调", "temperament"),
          ("cliches", "人设塑造", "trait"), ("cliches", "剧情机制", "mechanism"))),
    _spec("catalog", "library_links", "outgoing", "source_table,source_id,target_table,target_id", "source_table", "source_id", "target_table", "target_id"),
    _spec("catalog", "library_links", "incoming", "target_table,target_id,source_table,source_id", "target_table", "target_id", "source_table", "source_id"),
    _spec("workbench", "doc_documents", "recent", "created_at DESC,id", "created_at", "id"),
    _spec("workbench", "doc_documents", "work_recent", "work_id,created_at DESC,id", "work_id", "created_at", "id"),
    _spec("workbench", "run_jobs", "recent", "created_at DESC,id", "created_at", "id"),
    _spec("workbench", "run_jobs", "document_recent", "document_id,created_at DESC,id", "document_id", "created_at", "id"),
    _spec("workbench", "run_jobs", "status", "status,id", "status", "id"),
    _spec("workbench", "run_chunks", "status", "run_id,status", "run_id", "status"),
    _spec("workbench", "run_events", "history", "run_id,seq", "run_id", "seq"),
    _spec("workbench", "reference_section_commits", "section_clock", "document_id,section,committed_at", "document_id", "section", "committed_at"),
    _spec("workbench", "reference_section_commits", "source", "document_id,source_version_id", "document_id", "source_version_id"),
    _spec("workbench", "usage_calls", "recent", "started_at DESC,id DESC", "started_at", "id"),
    _spec("workbench", "usage_calls", "run_recent", "run_id,started_at DESC,id DESC", "run_id", "started_at", "id"),
)


def eligible_indexes(connection: sqlite3.Connection, namespace: str) -> list[IndexSpec]:
    if namespace not in {"catalog", "workbench"}:
        raise ValueError("unknown index namespace")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    columns: dict[str, set[str]] = {}
    result = []
    for spec in INDEX_SPECS:
        if spec.namespace != namespace or spec.table not in tables:
            continue
        if spec.table not in columns:
            columns[spec.table] = {row[1] for row in connection.execute(f'PRAGMA table_info("{spec.table}")')}
        if set(spec.columns) <= columns[spec.table]:
            result.append(spec)
    return result


def ensure_growth_indexes(connection: sqlite3.Connection, namespace: str) -> list[str]:
    """Apply missing indexes to existing tables inside the caller's transaction.

    Schema inspection is bounded by the fixed manifest. Building a new index on
    an upgraded store is a one-time operation; subsequent opens only read schema.
    """
    present = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='index'")}
    added = []
    for spec in eligible_indexes(connection, namespace):
        if spec.name not in present:
            connection.execute(spec.sql)
            added.append(spec.name)
    return added
