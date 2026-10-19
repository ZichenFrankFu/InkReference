"""Bounded catalog search over explicit material relationships.

SQL chooses and paginates every group before records are rendered. It never
loads source documents or follows coappearance as an implicit relationship.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import catalog_schema_v11 as schema
from .database import DataError, TABLES
from .storage_utils import json_field_expression

TABLE_KEYS = tuple(TABLES)
WORK_FILTER_FIELDS = {"category": "类别", "seen": "用户是否完整体验过", "genre": "作品题材", "style": "作品风格"}
STATE_FILTERS = {"has_uploaded_text", "has_text", "analysis_completed", "has_analysis"}
SORT_ALIASES = {"discussion_heat": "讨论热度", "commercial_scale": "商业规模", "market_scale": "商业规模"}
# All endpoints are external IDs. Symmetric views may repeat an edge, hence UNION.
EDGE_CTE = """edges(source_table,source_id,target_table,target_id,label,description) AS (
 SELECT source_table,source_id,target_table,target_id,field,
   CASE WHEN json_type(payload_json)='object' THEN COALESCE(json_extract(payload_json,'$.关联内容'),json_extract(payload_json,'$.关联方式'),'') ELSE '' END
 FROM library_links
 UNION SELECT target_table,target_id,source_table,source_id,field,
   CASE WHEN json_type(payload_json)='object' THEN COALESCE(json_extract(payload_json,'$.关联内容'),json_extract(payload_json,'$.关联方式'),'') ELSE '' END
 FROM library_links
 UNION SELECT source_table,source_id,target_table,target_id,relation_type,description FROM material_relations
 UNION SELECT target_table,target_id,source_table,source_id,relation_type,description FROM material_relations
 UNION SELECT 'works',work_id,'characters',id,'登场人物','' FROM characters WHERE work_id IS NOT NULL
 UNION SELECT 'characters',id,'works',work_id,'所属作品','' FROM characters WHERE work_id IS NOT NULL
 UNION SELECT 'works',work_id,'cliches',cliche_id,'关联桥段',detail FROM work_cliches
 UNION SELECT 'cliches',cliche_id,'works',work_id,'出现于',detail FROM work_cliches
)"""
RECORDS_CTE = "records(table_key,id,values_json) AS NOT MATERIALIZED (" + " UNION ALL ".join(f"SELECT '{table}',id,values_json FROM {table}" for table in TABLE_KEYS) + ")"



def attach_catalog_state(db: sqlite3.Connection, path: str | Path) -> None:
    if not any(row[1] == "catalog_texts" for row in db.execute("PRAGMA database_list")):
        db.execute("ATTACH DATABASE ? AS catalog_texts", (str(path),))


def work_state_sql(db: sqlite3.Connection, kind: str, alias: str = "r") -> str:
    """Return a metadata-only EXISTS predicate, using an already attached store."""
    attached = any(row[1] == "catalog_texts" for row in db.execute("PRAGMA database_list"))
    if not attached:
        raise DataError(422, "文本与分析筛选需要关联作品状态")
    tables = {row[0] for row in db.execute("SELECT name FROM catalog_texts.sqlite_schema WHERE type='table'")}
    if "doc_documents" not in tables:
        return "0"
    if kind in {"has_uploaded_text", "has_text"}:
        return f"EXISTS(SELECT 1 FROM catalog_texts.doc_documents d WHERE d.work_id={alias}.id)"
    if "reference_analysis" not in tables:
        return "0"
    if kind == "has_analysis":
        return f"EXISTS(SELECT 1 FROM catalog_texts.doc_documents d JOIN catalog_texts.reference_analysis a ON a.document_id=d.id WHERE d.work_id={alias}.id AND a.revision>0)"
    if kind != "analysis_completed":
        raise DataError(422, "未知作品状态")
    if not {"reference_section_commits", "run_jobs", "run_chunks", "run_stage_chunks"} <= tables:
        return "0"
    # A completed work has a reviewed fixed pipeline for the current complete
    # text: every chunk covers a contiguous span and every stage/section has
    # been confirmed. Valid empty section results still count as reviewed.
    characters = "COALESCE((SELECT v.characters FROM catalog_texts.doc_version_views v WHERE v.version_id=d.latest_version_id),(SELECT CAST(json_extract(v.payload_json,'$.coverage.characters') AS INTEGER) FROM catalog_texts.doc_versions v WHERE v.id=d.latest_version_id),length(d.source_text))" if "doc_version_views" in tables else "COALESCE((SELECT CAST(json_extract(v.payload_json,'$.coverage.characters') AS INTEGER) FROM catalog_texts.doc_versions v WHERE v.id=d.latest_version_id),length(d.source_text))"
    return f"""EXISTS(SELECT 1 FROM catalog_texts.doc_documents d
        JOIN catalog_texts.reference_analysis a ON a.document_id=d.id
        JOIN catalog_texts.run_jobs j ON j.document_id=d.id AND j.version_id=d.latest_version_id
        WHERE d.work_id={alias}.id AND a.revision>0 AND j.status='approved'
        AND j.total>0 AND (SELECT COUNT(*) FROM catalog_texts.run_chunks k WHERE k.run_id=j.id)=j.total
        AND NOT EXISTS(SELECT 1 FROM catalog_texts.run_chunks k WHERE k.run_id=j.id AND (
            k.status<>'completed' OR k.end<=k.start OR
            (k.start<>0 AND NOT EXISTS(SELECT 1 FROM catalog_texts.run_chunks p WHERE p.run_id=j.id AND p.end=k.start)) OR
            (SELECT COUNT(DISTINCT s.stage) FROM catalog_texts.run_stage_chunks s WHERE s.run_id=j.id AND s.chunk_id=k.id AND s.status='completed' AND s.stage IN ('plot','characters','settings','language'))<>4 OR
            (SELECT COUNT(DISTINCT c.section) FROM catalog_texts.reference_section_commits c WHERE c.document_id=d.id AND c.run_id=j.id AND c.chunk_id=k.id AND c.source_version_id=d.latest_version_id AND c.section IN ('plot','characters','settings','style','rhythm','opening','era_background'))<>7))
        AND (SELECT MIN(k.start) FROM catalog_texts.run_chunks k WHERE k.run_id=j.id)=0
        AND (SELECT MAX(k.end) FROM catalog_texts.run_chunks k WHERE k.run_id=j.id)={characters}
        AND (SELECT SUM(k.end-k.start) FROM catalog_texts.run_chunks k WHERE k.run_id=j.id)={characters})"""


def work_filter_sql(db: sqlite3.Connection, filters: dict[str, Any], alias: str = "r") -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    for key, value in filters.items():
        if key in WORK_FILTER_FIELDS:
            if not isinstance(value, str):
                raise DataError(422, f"筛选字段 {key} 必须为字符串")
            field = json_field_expression(WORK_FILTER_FIELDS[key], prefix=alias + ".")
            if key == "seen" and value == "unknown":
                clauses.append(f"COALESCE({field},'') NOT IN ('是','否')")
            elif value:
                clauses.append(f"{field}=?")
                values.append(value)
        elif key in STATE_FILTERS:
            if type(value) is not bool:
                raise DataError(422, f"筛选字段 {key} 必须为布尔值")
            expression = work_state_sql(db, key, alias)
            clauses.append(expression if value else f"NOT ({expression})")
    return clauses, values


def facets(db: sqlite3.Connection, *, include_primary: bool = True) -> dict[str, Any]:
    def distinct(table: str, field: str) -> list[str]:
        expression = json_field_expression(field)
        return [row[0] for row in db.execute(f"SELECT DISTINCT {expression} v FROM {table} WHERE COALESCE({expression},'')<>'' ORDER BY v LIMIT 200")]
    return {"categories": distinct("works", "类别") if include_primary else [], "genres": sorted(set(schema.GENRES) | set(distinct("works", "作品题材"))),
            "styles": sorted(set(schema.STYLES) | set(distinct("works", "作品风格"))),
            "roles": distinct("characters", "角色定位") if include_primary else [], "character_traits": list(schema.TRAITS),
            "plot_mechanisms": ["信息反转", "机械降神", "顿悟突破", "代价交换"], "works": [], "works_paged": True}


def record_order(table: str, sort_by: str | None, sort_direction: str) -> str:
    if sort_direction not in {"asc", "desc"}:
        raise DataError(422, "排序方向必须为 asc 或 desc")
    sort_by = SORT_ALIASES.get(sort_by, sort_by)
    direction = sort_direction.upper()
    if sort_by in {"讨论热度", "商业规模"}:
        if table != "works":
            return "r.sort_order,r.id"
        expression = f"catalog_heat_component({json_field_expression(sort_by, prefix='r.')})"
        # Missing evidence follows known scores in both directions.
        return f"({expression} IS NULL),{expression} {direction},r.id"
    if not sort_by:
        return "r.sort_order,r.id"
    if sort_by == "id":
        return f"r.id {direction}"
    if table == "cliches" and sort_by == "cliché评分":
        return f"COALESCE((SELECT SUM(catalog_work_heat(w.values_json)) FROM works w JOIN work_cliches wc ON wc.work_id=w.id WHERE wc.cliche_id=r.id),0) {direction},r.id"
    if sort_by not in TABLES[table]["columns"]:
        raise DataError(422, "排序字段无效")
    return f"{json_field_expression(sort_by, prefix='r.')} COLLATE NOCASE {direction},r.id"


class CatalogSearch:
    def __init__(self, store, documents):
        self.store, self.documents = store, documents

    def search(self, q: str = "", *, offset: int = 0, limit: int = 50,
               work_id: str | None = None, character_id: str | None = None, cliche_id: str | None = None,
               filters: dict[str, Any] | None = None, sort_by: str | None = None, sort_direction: str = "asc") -> dict[str, Any]:
        if not isinstance(q, str) or len(q) > 1000 or offset < 0 or not 1 <= limit <= 200:
            raise DataError(422, "搜索或分页范围无效")
        filters = {key: value for key, value in (filters or {}).items() if value is not None}
        supported = {*WORK_FILTER_FIELDS, *STATE_FILTERS, "role", "character_trait", "plot_mechanism"}
        if set(filters) - supported:
            raise DataError(422, "搜索筛选字段无效")
        normalized_sort = SORT_ALIASES.get(sort_by, sort_by)
        if normalized_sort and normalized_sort not in {"id", *(column for spec in TABLES.values() for column in spec["columns"])}:
            raise DataError(422, "排序字段无效")
        scopes = [(table, identity) for table, identity in (("works", work_id), ("characters", character_id), ("cliches", cliche_id)) if identity]
        if len(scopes) > 1:
            raise DataError(422, "请选择一个关联范围")
        groups = {}
        with self.store.connect() as db:
            db.create_function("casefold", 1, lambda value: str(value).casefold(), deterministic=True)
            if any(key in filters for key in STATE_FILTERS):
                documents = self.documents() if callable(self.documents) else self.documents
                attach_catalog_state(db, documents.db_path)
            db.execute("BEGIN")
            base = f"WITH {RECORDS_CTE},{EDGE_CTE}"
            parameters: list[Any] = []
            # Association JSON contains IDs, not the metadata a user is seeking.
            seed_match = "EXISTS(SELECT 1 FROM json_each(n.values_json) j WHERE j.key NOT IN ('关联作品','关联角色','关联cliché','典型桥段（剧透）','出现作品') AND instr(casefold(j.value),?)>0)" if q else "1"
            if q:
                parameters.append(q.casefold())
            base += f",seeds AS (SELECT table_key,id FROM records n WHERE {seed_match})"
            if scopes:
                table, identity = scopes[0]
                self.store._row(db, table, identity)
                base += ",scope(table_key,id) AS (SELECT ?,? UNION SELECT target_table,target_id FROM edges WHERE source_table=? AND source_id=?)"
                parameters.extend((table, identity, table, identity))
            work_filters = {key: value for key, value in filters.items() if key in WORK_FILTER_FIELDS or key in STATE_FILTERS}
            if work_filters:
                work_clauses, work_values = work_filter_sql(db, work_filters)
                base += ",eligible_works AS (SELECT id FROM works r WHERE " + (" AND ".join(work_clauses) or "1") + ")"
                parameters.extend(work_values)
            for table in TABLE_KEYS:
                clauses = []
                if q:
                    clauses.append("(EXISTS(SELECT 1 FROM seeds s WHERE s.table_key=? AND s.id=r.id) OR EXISTS(SELECT 1 FROM edges e JOIN seeds s ON s.table_key=e.target_table AND s.id=e.target_id WHERE e.source_table=? AND e.source_id=r.id))")
                group_parameters = [*parameters, table, table] if q else list(parameters)
                if scopes:
                    clauses.append("EXISTS(SELECT 1 FROM scope s WHERE s.table_key=? AND s.id=r.id)")
                    group_parameters.append(table)
                if work_filters:
                    clauses.append("r.id IN (SELECT id FROM eligible_works)" if table == "works" else "EXISTS(SELECT 1 FROM edges e JOIN eligible_works w ON e.target_table='works' AND e.target_id=w.id WHERE e.source_table=? AND e.source_id=r.id)")
                    if table != "works":
                        group_parameters.append(table)
                group_filters = {"role": "角色定位", "character_trait": "性格基调"} if table == "characters" else {"character_trait": "人设塑造", "plot_mechanism": "剧情机制"} if table == "cliches" else {}
                for key, field in group_filters.items():
                    if key in filters and filters[key] != "":
                        if not isinstance(filters[key], str):
                            raise DataError(422, "素材筛选值必须为字符串")
                        clauses.append(f"{json_field_expression(field, prefix='r.')}=?")
                        group_parameters.append(filters[key])
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                total = db.execute(f"{base} SELECT COUNT(*) FROM {table} r{where}", group_parameters).fetchone()[0]
                group_sort = normalized_sort if normalized_sort in {"id", *TABLES[table]["columns"]} else None
                order = record_order(table, group_sort, sort_direction)
                rows = db.execute(f"{base} SELECT r.* FROM {table} r{where} ORDER BY {order} LIMIT ? OFFSET ?", (*group_parameters, limit, offset)).fetchall()
                items = []
                for row in rows:
                    item = self.store._render(db, table, row)
                    direct = bool(db.execute(f"{base} SELECT 1 FROM seeds WHERE table_key=? AND id=?", (*parameters, table, row["id"])).fetchone()) if q else True
                    # At most 12 relationship explanations per rendered card.
                    rel_where = "e.source_table=? AND e.source_id=?"
                    rel_values: list[Any] = [*parameters, table, row["id"]]
                    if scopes:
                        rel_where += " AND e.target_table=? AND e.target_id=?"
                        rel_values.extend(scopes[0])
                    elif q:
                        rel_where += " AND EXISTS(SELECT 1 FROM seeds s WHERE s.table_key=e.target_table AND s.id=e.target_id)"
                    related = [dict(edge) for edge in db.execute(f"{base} SELECT DISTINCT e.target_table AS 'table',e.target_id AS id,COALESCE(json_extract(n.values_json,'$.作品'),json_extract(n.values_json,'$.角色名'),json_extract(n.values_json,'$.角色'),json_extract(n.values_json,'$.cliché内容'),e.target_id) AS title,e.label,e.description FROM edges e JOIN records n ON n.table_key=e.target_table AND n.id=e.target_id WHERE {rel_where} ORDER BY e.target_table,e.target_id,e.label LIMIT 12", rel_values)]
                    item["match"] = {"direct": direct, "related": related}
                    items.append(item)
                groups[table] = {"items": items, "total": total, "offset": offset, "limit": limit}
            return {"q": q, "groups": groups, "facets": facets(db), "scope": {"table": scopes[0][0], "id": scopes[0][1]} if scopes else None}
