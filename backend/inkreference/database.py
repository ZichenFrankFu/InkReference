"""Transactional SQLite material library; no GUI or model dependencies.

CSV columns are text, including unknown values.  Work/cliche associations live
in one relation table; the two legacy relationship columns are rendered views.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from . import __version__
from . import catalog_schema_v11 as catalog_schema
import hashlib

VERSION = __version__
WORK_COLUMNS = ["作品ID", "作品", "类别", "作者", "发布日期", "版本与原作关系", "用户是否完整体验过", "两句话概括", "剧情补充说明", "热度", "典型桥段（剧透）"]
CHARACTER_COLUMNS = ["人物ID", "作品ID", "作品", "版本", "角色", "角色定位", "开场身份", "开场能力／专长", "后续能力", "性格", "人物补充说明"]
CLICHE_COLUMNS = ["cliché内容", "cliché程度", "出现作品"]
LEGACY_TABLES = {
    "works": {"label": "作品", "id_field": "作品ID", "columns": WORK_COLUMNS},
    "characters": {"label": "人物", "id_field": "人物ID", "columns": CHARACTER_COLUMNS},
    "cliches": {"label": "桥段", "id_field": "cliché内容", "columns": CLICHE_COLUMNS},
}
LIBRARY_WORK_COLUMNS = ["作品ID", "作品", "类别", "作者", "发布日期", "用户是否完整体验过", "两句话概括", "热度权重", "关联作品", "关联角色", "关联cliché"]
LIBRARY_CHARACTER_COLUMNS = ["人物ID", "角色", "别名", "角色定位", "开场身份", "开场能力／专长", "后续能力", "性格", "人物补充说明", "关联作品", "关联cliché"]
SCHEMA4_CLICHE_COLUMNS = ["clichéID", "cliché内容", "cliché程度（%）", "命中作品数", "命中热度合计", "关联作品", "关联角色"]
CLICHE_CHARACTER_TRAITS = ("强大", "智谋", "利他", "共情", "利己", "执念", "反差", "宿命")
CLICHE_PLOT_MECHANISMS = ("信息反转", "机械降神", "顿悟突破", "代价交换")
CLICHE_TAGS = {"人设塑造": CLICHE_CHARACTER_TRAITS, "剧情机制": CLICHE_PLOT_MECHANISMS}
LIBRARY_CLICHE_COLUMNS = [*SCHEMA4_CLICHE_COLUMNS, *CLICHE_TAGS]
SCHEMA4_COLUMNS = {"works": LIBRARY_WORK_COLUMNS, "characters": LIBRARY_CHARACTER_COLUMNS, "cliches": SCHEMA4_CLICHE_COLUMNS}
SCHEMA5_COLUMNS = {"works": list(LIBRARY_WORK_COLUMNS), "characters": list(LIBRARY_CHARACTER_COLUMNS), "cliches": list(LIBRARY_CLICHE_COLUMNS)}
LIBRARY_WORK_COLUMNS = catalog_schema.WORK_COLUMNS
LIBRARY_CHARACTER_COLUMNS = catalog_schema.CHARACTER_COLUMNS
LIBRARY_CLICHE_COLUMNS = catalog_schema.CLICHE_COLUMNS
TABLES = {
    "works": {"label": "作品", "id_field": "作品ID", "columns": LIBRARY_WORK_COLUMNS},
    "characters": {"label": "人物", "id_field": "人物ID", "columns": LIBRARY_CHARACTER_COLUMNS},
    "cliches": {"label": "桥段", "id_field": "clichéID", "columns": LIBRARY_CLICHE_COLUMNS},
}
ASSOCIATIONS = {"关联作品": ("works", "作品ID"), "关联角色": ("characters", "人物ID"), "关联cliché": ("cliches", "clichéID")}
REVERSE_FIELD = {"works": "关联作品", "characters": "关联角色", "cliches": "关联cliché"}


def modern_values(table: str, values: dict[str, str]) -> bool:
    if table == "cliches" and "clichéID" not in values and re.match(r"^C\d+(?=[｜|：:]|$)",values.get("cliché内容","")):
        return False
    markers = {"works": {"热度权重", "关联作品", "关联角色", "关联cliché"}, "characters": {"别名", "关联作品", "关联cliché"}, "cliches": {"clichéID", "关联作品", "cliché程度（%）"}}
    return bool(markers[table] & set(values)) or not bool((set(LEGACY_TABLES[table]["columns"]) - set(TABLES[table]["columns"])) & set(values))


def association_items(value: str, label: str) -> list[Any]:
    if not value:
        return []
    try:
        items = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise DataError(422, f"{label} 必须为 JSON 数组") from exc
    if not isinstance(items, list) or any(not isinstance(item, (str, dict)) for item in items):
        raise DataError(422, f"{label} 必须为编号或对象组成的 JSON 数组")
    return items

WORK_RELATION_FIELD = "典型桥段（剧透）"
CLICHE_ID = re.compile(r"^(C\d+)(?=[｜|：:]|$)")
# A relation starts on its own line. Semicolons inside narrative text are data.
WORK_RELATION = re.compile(r"(?m)^(C\d+)[：:]")
BRACKET_PAIRS = {"〔": "〕", "（": "）", "(": ")", "[": "]", "{": "}", "【": "】", "《": "》", "⟦": "⟧"}
QUALIFIED_WORK_LABEL = re.compile(r"^(.*) ⟦ID:([^⟦⟧]+)⟧$", re.DOTALL)


class DataError(Exception):
    def __init__(self, status_code: int, detail: str | dict[str, Any]):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def require_table(table: str) -> str:
    if table not in TABLES:
        raise DataError(404, "未知数据表")
    return table


def record_id(table: str, values: dict[str, str]) -> str:
    require_table(table)
    if table == "cliches" and "clichéID" not in values:
        match = CLICHE_ID.match(values.get("cliché内容", ""))
        if not match:
            raise DataError(422, "桥段内容必须以 C 编号开头，例如 C001｜描述")
        return match.group(1)
    value = values.get(TABLES[table]["id_field"], "")
    if not value or value != value.strip():
        raise DataError(422, "ID 不能为空或包含首尾空白")
    if value in {".", ".."} or re.search(r"[/\\\x00-\x1f\x7f⟦⟧]", value):
        raise DataError(422, "ID 不能包含斜杠、反斜杠、ASCII 控制字符或 ⟦⟧，也不能为 . 或 ..；请使用可作为 URL 路径段的编号")
    return value


def validate_values(table: str, values: dict[str, str], *, partial: bool = False) -> dict[str, str]:
    require_table(table)
    if not isinstance(values, dict):
        raise DataError(422, "字段必须为对象")
    if table == "works" and "两句话概括" in values and "剧情梗概" in values:
        values = {k:v for k,v in values.items() if k != "两句话概括"}
    if table == "works" and "看过" in values:
        values = {**values, "用户是否完整体验过": values["看过"]}
        del values["看过"]
    modern = modern_values(table, values)
    columns = TABLES[table]["columns"] if catalog_schema.is_current(table,values) or (modern and not (set(values) - set(TABLES[table]["columns"]))) else SCHEMA5_COLUMNS[table] if modern else LEGACY_TABLES[table]["columns"]
    unknown = set(values) - set(columns)
    if unknown:
        raise DataError(422, f"未知字段：{'、'.join(sorted(unknown))}")
    if any(not isinstance(value, str) for value in values.values()):
        raise DataError(422, "所有 CSV 字段必须为字符串")
    normalized = dict(values) if partial else {column: values.get(column, catalog_schema.JSON_DEFAULTS.get(column, "")) for column in columns}
    if modern:
        if catalog_schema.is_current(table, normalized):
            if table == "works":
                for field in ("讨论热度", "商业规模"):
                    if normalized.get(field) and catalog_schema.heat_component(normalized[field]) is None:
                        raise DataError(422, f"{field} 必须为 1–10 分，或包含 min/max 的 1–10 分范围")
            if table == "characters":
                if normalized.get("性格基调", "") not in ("", *CLICHE_CHARACTER_TRAITS):
                    raise DataError(422, "性格基调仅可选择一个标签")
                for field,kind in (("初始状态",dict),("阶段状态",list)):
                    try: parsed=json.loads(normalized.get(field,catalog_schema.JSON_DEFAULTS[field]))
                    except (TypeError, ValueError) as exc: raise DataError(422,f"{field} 必须为 JSON") from exc
                    if not isinstance(parsed,kind): raise DataError(422,f"{field} 格式错误")
                try: aliases=json.loads(normalized.get("别名","[]"))
                except (TypeError, ValueError) as exc:raise DataError(422,"别名必须为 JSON 数组") from exc
                from .character_contract import normalize_character
                snapshot_rows=json.loads(normalized.get("阶段状态","[]"))
                character_version=4 if any(isinstance(s,dict) and "name" in s for s in snapshot_rows) else 3
                canonical=normalize_character({"schema_version":character_version,"id":normalized.get("人物ID",values.get("人物ID","")),"name":normalized.get("角色名",values.get("角色名","")),"aliases":aliases,"role_tag":normalized.get("角色定位",""),"temperament":normalized.get("性格基调",""),"visual_design":normalized.get("视觉元素设计",""),"initial_state":json.loads(normalized.get("初始状态","{}")),"snapshots":json.loads(normalized.get("阶段状态","[]"))})
                normalized["初始状态"]=_json(canonical["initial_state"])
                normalized["阶段状态"]=_json(canonical["snapshots"])

        if table == "cliches":
            for field, options in CLICHE_TAGS.items():
                if field in normalized and normalized[field] not in ("", *options):
                    raise DataError(422, f"{field} 仅可单选：{'、'.join(options)}，或留空")
        for column in (*ASSOCIATIONS, "别名"):
            if column in normalized:
                association_items(normalized[column], column)
    return normalized


def split_occurrences(text: str) -> list[str]:
    """Split only top-level semicolons, preserving punctuation inside editions."""
    result: list[str] = []
    stack: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in BRACKET_PAIRS:
            stack.append(BRACKET_PAIRS[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char in "；;" and not stack:
            result.append(text[start:index])
            start = index + 1
    result.append(text[start:])
    return result if text else []


def parse_work_relations(text: str) -> tuple[list[Any], dict[str, str]]:
    """Return a lossless layout and each C-number's original detail segment."""
    matches = list(WORK_RELATION.finditer(text))
    layout: list[Any] = []
    details: dict[str, str] = {}
    if not matches:
        return ([text] if text else []), details
    if matches[0].start():
        layout.append(text[:matches[0].start()])
    for index, match in enumerate(matches):
        cid = match.group(1)
        if cid in details:
            raise DataError(422, f"同一作品重复关联桥段 {cid}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        details[cid] = text[match.start():end]
        layout.append({"cliche_id": cid})
    return layout, details


def work_label(values: dict[str, str]) -> str:
    return f"{values['作品']}〔{values.get('版本与原作关系', '')}〕"


def character_version(version: str) -> str:
    """Legacy character editions omit the final provenance suffix."""
    return re.split(r"；(?:原创|改编自|源于)", version, maxsplit=1)[0]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args: Any) -> Any:
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class DataStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.write_lock = threading.RLock()
        self.character_sync = None
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        # Acquired before either attached SQLite database takes a write lock.
        # Runtime shares this RLock with reference writes to keep one lock order.
        with self.write_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _migrate(self) -> None:
        with self.transaction() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            if current > 6:
                raise DataError(409, "数据库版本高于当前应用，请使用更新的应用")
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            if 1 not in applied:
                statements = [
                    "CREATE TABLE works (id TEXT PRIMARY KEY, values_json TEXT NOT NULL, cliche_layout TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0), sort_order INTEGER NOT NULL)",
                    "CREATE TABLE characters (id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE, values_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0), sort_order INTEGER NOT NULL)",
                    "CREATE TABLE cliches (id TEXT PRIMARY KEY, values_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0), sort_order INTEGER NOT NULL)",
                    "CREATE TABLE work_cliches (work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE, cliche_id TEXT NOT NULL REFERENCES cliches(id) ON DELETE CASCADE, detail TEXT NOT NULL, work_position INTEGER NOT NULL, cliche_position INTEGER NOT NULL, PRIMARY KEY(work_id,cliche_id))",
                    "CREATE INDEX idx_characters_work ON characters(work_id)",
                    "CREATE INDEX idx_work_cliches_cliche ON work_cliches(cliche_id,cliche_position)",
                    "CREATE INDEX idx_work_cliches_work ON work_cliches(work_id,work_position)",
                ]
                for statement in statements:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations VALUES (1, ?)", (datetime.now(timezone.utc).isoformat(),))
            if 2 not in applied:
                # Tombstones outlive deletions: stale edits cannot target a new
                # incarnation of the same external ID (the ABA problem).
                connection.execute("CREATE TABLE record_versions (table_key TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0), PRIMARY KEY(table_key,record_id))")
                for table in TABLES:
                    connection.execute(f"INSERT INTO record_versions SELECT ?,id,revision FROM {table}", (table,))
                connection.execute("INSERT INTO schema_migrations VALUES (2, ?)", (datetime.now(timezone.utc).isoformat(),))
            if 3 not in applied:
                connection.execute("""CREATE TABLE material_relations (
                    id TEXT PRIMARY KEY,
                    source_table TEXT NOT NULL CHECK(source_table IN ('works','characters','cliches')),
                    source_id TEXT NOT NULL,
                    target_table TEXT NOT NULL CHECK(target_table IN ('works','characters','cliches')),
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL CHECK(length(trim(relation_type)) > 0),
                    description TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
                    CHECK(NOT(source_table = 'cliches' AND target_table = 'cliches')),
                    CHECK(source_table != target_table OR source_id != target_id)
                )""")
                connection.execute("CREATE INDEX idx_material_relations_source ON material_relations(source_table,source_id)")
                connection.execute("CREATE INDEX idx_material_relations_target ON material_relations(target_table,target_id)")
                connection.execute("CREATE UNIQUE INDEX idx_material_relations_meaning ON material_relations(source_table,source_id,target_table,target_id,relation_type)")
                # Polymorphic endpoints cannot use a normal SQLite foreign key.
                # Triggers preserve the same integrity for direct SQLite writes
                # and cascade deletion through a work's characters as well.
                for operation in ("INSERT", "UPDATE OF source_table,source_id,target_table,target_id"):
                    name = "insert" if operation == "INSERT" else "update"
                    checks = " ".join(
                        f"SELECT CASE WHEN NEW.{side}_table = '{table}' AND NOT EXISTS(SELECT 1 FROM {table} WHERE id = NEW.{side}_id) THEN RAISE(ABORT,'material relation endpoint missing') END;"
                        for side in ("source", "target") for table in TABLES
                    )
                    connection.execute(f"CREATE TRIGGER material_relations_{name}_endpoints BEFORE {operation} ON material_relations BEGIN {checks} END")
                for table in TABLES:
                    connection.execute(f"""CREATE TRIGGER {table}_delete_material_relations AFTER DELETE ON {table}
                        BEGIN DELETE FROM material_relations
                        WHERE (source_table = '{table}' AND source_id = OLD.id)
                           OR (target_table = '{table}' AND target_id = OLD.id); END""")
                connection.execute("INSERT INTO schema_migrations VALUES (3, ?)", (datetime.now(timezone.utc).isoformat(),))

            if 4 not in applied:
                # A person can appear in several works. The old ownership column
                # remains a compatibility hint, never the modern relationship.
                for trigger in ("material_relations_insert_endpoints", "material_relations_update_endpoints", "characters_delete_material_relations"):
                    connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                connection.execute("DROP INDEX IF EXISTS idx_characters_work")
                connection.execute("ALTER TABLE characters RENAME TO characters_v3")
                connection.execute("CREATE TABLE characters (id TEXT PRIMARY KEY, work_id TEXT REFERENCES works(id) ON DELETE SET NULL, values_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0), sort_order INTEGER NOT NULL)")
                connection.execute("INSERT INTO characters SELECT * FROM characters_v3")
                connection.execute("DROP TABLE characters_v3")
                connection.execute("CREATE INDEX idx_characters_work ON characters(work_id)")
                for operation in ("INSERT", "UPDATE OF source_table,source_id,target_table,target_id"):
                    name = "insert" if operation == "INSERT" else "update"
                    checks = " ".join(f"SELECT CASE WHEN NEW.{side}_table = '{table}' AND NOT EXISTS(SELECT 1 FROM {table} WHERE id = NEW.{side}_id) THEN RAISE(ABORT,'material relation endpoint missing') END;" for side in ("source", "target") for table in TABLES)
                    connection.execute(f"CREATE TRIGGER material_relations_{name}_endpoints BEFORE {operation} ON material_relations BEGIN {checks} END")
                connection.execute("CREATE TRIGGER characters_delete_material_relations AFTER DELETE ON characters BEGIN DELETE FROM material_relations WHERE (source_table='characters' AND source_id=OLD.id) OR (target_table='characters' AND target_id=OLD.id); END")
                connection.execute("CREATE TABLE library_links (source_table TEXT NOT NULL,source_id TEXT NOT NULL,field TEXT NOT NULL,position INTEGER NOT NULL,target_table TEXT NOT NULL,target_id TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(source_table,source_id,field,position))")
                connection.execute("CREATE INDEX idx_library_links_target ON library_links(target_table,target_id)")
                connection.execute("CREATE TABLE legacy_values (table_key TEXT NOT NULL,record_id TEXT NOT NULL,values_json TEXT NOT NULL,PRIMARY KEY(table_key,record_id))")
                self._upgrade_legacy_rows(connection)
                self._rebuild_library_links(connection)
                connection.execute("INSERT INTO schema_migrations VALUES (4, ?)", (datetime.now(timezone.utc).isoformat(),))
            if 5 not in applied:
                # Schema-only addition: retain all imported text and existing
                # revisions. A missing classification is never inferred.
                for row in connection.execute("SELECT id,values_json FROM cliches").fetchall():
                    values = json.loads(row["values_json"])
                    if modern_values("cliches", values):
                        for field in CLICHE_TAGS:
                            values.setdefault(field, "")
                        connection.execute("UPDATE cliches SET values_json=? WHERE id=?", (_json(values), row["id"]))
                connection.execute("INSERT INTO schema_migrations VALUES (5, ?)", (datetime.now(timezone.utc).isoformat(),))

            connection.execute("CREATE TABLE IF NOT EXISTS catalog_field_archive (table_key TEXT NOT NULL,record_id TEXT NOT NULL,source_schema TEXT NOT NULL,values_json TEXT NOT NULL,PRIMARY KEY(table_key,record_id,source_schema))")
            if 6 not in applied:
                self._upgrade_catalog_rows(connection)
                connection.execute("CREATE TABLE IF NOT EXISTS catalog_research_applies (job_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,result_json TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS character_contexts (character_id TEXT NOT NULL,work_id TEXT NOT NULL,document_id TEXT,extracted_character_id TEXT NOT NULL,character_json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(character_id,work_id),UNIQUE(work_id,extracted_character_id))")
                connection.execute("INSERT INTO schema_migrations VALUES (6, ?)", (datetime.now(timezone.utc).isoformat(),))

            for table in TABLES:
                connection.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_delete_catalog_archives AFTER DELETE ON {table} BEGIN DELETE FROM catalog_field_archive WHERE table_key='{table}' AND record_id=OLD.id; END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS characters_delete_contexts AFTER DELETE ON characters BEGIN DELETE FROM character_contexts WHERE character_id=OLD.id; END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS works_delete_contexts AFTER DELETE ON works BEGIN DELETE FROM character_contexts WHERE work_id=OLD.id; END")

    def _upgrade_catalog_rows(self, connection: sqlite3.Connection) -> None:
        for table in TABLES:
            for row in connection.execute(f"SELECT id,values_json FROM {table}").fetchall():
                values=json.loads(row["values_json"])
                if catalog_schema.is_current(table,values): continue
                connection.execute("INSERT OR IGNORE INTO legacy_values VALUES (?,?,?)",(table,row["id"],_json(values)))
                connection.execute("INSERT OR REPLACE INTO catalog_field_archive VALUES (?,?,?,?)",(table,row["id"],"5" if modern_values(table,values) else "3",_json(values)))
                connection.execute(f"UPDATE {table} SET values_json=? WHERE id=?",(_json(catalog_schema.project_values(table,values)),row["id"]))
        self._rebuild_library_links(connection)

    def _stats(self, connection: sqlite3.Connection) -> dict[str, int]:
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (*TABLES, "work_cliches")}

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._stats(connection)

    def meta(self) -> dict[str, Any]:
        counts = self.stats()
        return {"tables": [{"key": table, **spec, "count": counts[table]} for table, spec in TABLES.items()], "version": VERSION, "schema_version": 6}

    def _row(self, connection: sqlite3.Connection, table: str, identifier: str) -> sqlite3.Row:
        require_table(table)
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise DataError(404, f"记录不存在：{identifier}")
        return row

    def _render(self, connection: sqlite3.Connection, table: str, row: sqlite3.Row) -> dict[str, Any]:
        values = json.loads(row["values_json"])
        if modern_values(table, values):
            return self._catalog_record(connection,table,row,values)
        if table == "works":
            relations = {relation["cliche_id"]: relation for relation in connection.execute("SELECT * FROM work_cliches WHERE work_id = ? ORDER BY work_position,cliche_id", (row["id"],))}
            output: list[str] = []
            seen: set[str] = set()
            for segment in json.loads(row["cliche_layout"]):
                if isinstance(segment, str):
                    output.append(segment)
                elif segment["cliche_id"] in relations:
                    cid = segment["cliche_id"]
                    output.append(relations[cid]["detail"])
                    seen.add(cid)
            for cid, relation in relations.items():
                if cid not in seen:
                    if output and output[-1] and not output[-1].endswith("\n"):
                        output.append("\n")
                    output.append(relation["detail"])
            values[WORK_RELATION_FIELD] = "".join(output)
        elif table == "characters":
            work = json.loads(self._row(connection, "works", row["work_id"])["values_json"])
            values["作品ID"] = row["work_id"]
            values["作品"] = work["作品"]
            # Preserve imported edition strings until the parent's edition changes.
        elif table == "cliches":
            appearances = []
            labels = self._label_map(connection)
            for work in connection.execute("SELECT w.* FROM work_cliches r JOIN works w ON w.id = r.work_id WHERE r.cliche_id = ? ORDER BY r.cliche_position,w.sort_order,w.id", (row["id"],)):
                label = work_label(json.loads(work["values_json"]))
                if len(labels[label]) > 1:
                    label += f" ⟦ID:{work['id']}⟧"
                appearances.append(label)
            values["出现作品"] = "；".join(appearances)
        return self._catalog_record(connection,table,row,values)

    def _catalog_record(self, connection: sqlite3.Connection, table: str, row: sqlite3.Row, values: dict[str,str]) -> dict[str,Any]:
        display=catalog_schema.project_values(table,values)
        if not modern_values(table,values):
            if table=="works":
                display["关联角色"]=_json([r[0] for r in connection.execute("SELECT id FROM characters WHERE work_id=? ORDER BY sort_order,id",(row["id"],))])
                display["关联cliché"]=_json([r[0] for r in connection.execute("SELECT cliche_id FROM work_cliches WHERE work_id=? ORDER BY work_position,cliche_id",(row["id"],))])
            elif table=="characters":display["关联作品"]=_json([{"作品ID":row["work_id"]}] if row["work_id"] else [])
            else:
                display["clichéID"]=row["id"]
                display["cliché内容"]=re.sub(r"^C\d+[｜|：:]?", "", values.get("cliché内容", ""), count=1)
                display["关联作品"]=_json([{"作品ID":r["work_id"],"关联内容":r["detail"]} for r in connection.execute("SELECT * FROM work_cliches WHERE cliche_id=? ORDER BY cliche_position,work_id",(row["id"],))])
        result={"id":row["id"],"values":values,"catalog_values":display,"revision":row["revision"]}
        if table=="works":
            weight,missing=catalog_schema.work_heat(display)
            result["computed"]={"heat":round(weight,4),"missing_heat":missing}
        elif table=="cliches":
            marker=connection.total_changes
            cached=getattr(connection,"_catalog_score_context",None)
            if cached is None or cached[0]!=marker:
                works={r["id"]:catalog_schema.work_heat(json.loads(r["values_json"])) for r in connection.execute("SELECT id,values_json FROM works")}
                by_cliche={}
                for edge in connection.execute("SELECT work_id,cliche_id FROM work_cliches"):by_cliche.setdefault(edge["cliche_id"],set()).add(edge["work_id"])
                cached=(marker,works,by_cliche,sum(item[0] for item in works.values()),sum(item[1] for item in works.values()))
                if isinstance(connection,_ClosingConnection):connection._catalog_score_context=cached
            _,works,by_cliche,denominator,missing_count=cached
            matched=by_cliche.get(row["id"],set())
            score=round(100*sum(works[i][0] for i in matched if i in works)/denominator,4) if denominator else 0.0
            display["cliché评分"]=str(score)
            if catalog_schema.is_current(table,values): result["values"]={**values,"cliché评分":str(score)}
            result["computed"]={"cliche_score":score,"matched_work_count":len(matched),"total_work_count":len(works),"missing_heat_count":missing_count,"formula":catalog_schema.SCORE_FORMULA}
        return result

    def get_record(self, table: str, identifier: str) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._render(connection, table, self._row(connection, table, identifier))

    def _all(self, connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
        require_table(table)
        return [self._render(connection, table, row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY sort_order,id")]

    def all_records(self, table: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._all(connection, table)

    def list_records(self, table: str, q: str = "", offset: int = 0, limit: int = 50, work_id: str | None = None, cliche_id: str | None = None, filters: dict[str,Any] | None = None, sort_by: str | None = None, sort_direction: str = "asc") -> dict[str, Any]:
        require_table(table)
        if offset < 0 or limit < 1 or limit > 500:
            raise DataError(422,"分页范围无效（limit 为 1–500）")
        if sort_direction not in {"asc","desc"}:
            raise DataError(422,"排序方向必须为 asc 或 desc")
        clauses: list[str] = []; parameters: list[Any] = []
        if work_id:
            if table == "works":
                clauses.append("r.id=?"); parameters.append(work_id)
            else:
                legacy = "SELECT id FROM characters WHERE work_id=?" if table == "characters" else "SELECT cliche_id FROM work_cliches WHERE work_id=?"
                clauses.append(f"r.id IN (SELECT target_id FROM library_links WHERE source_table='works' AND source_id=? AND target_table=? UNION SELECT source_id FROM library_links WHERE target_table='works' AND target_id=? AND source_table=? UNION {legacy})")
                parameters.extend((work_id,table,work_id,table,work_id))
        if cliche_id:
            if table == "cliches":
                clauses.append("r.id=?"); parameters.append(cliche_id)
            else:
                legacy = "SELECT work_id FROM work_cliches WHERE cliche_id=?" if table == "works" else "SELECT ch.id FROM characters ch JOIN work_cliches wc ON wc.work_id=ch.work_id WHERE wc.cliche_id=?"
                clauses.append(f"r.id IN (SELECT target_id FROM library_links WHERE source_table='cliches' AND source_id=? AND target_table=? UNION SELECT source_id FROM library_links WHERE target_table='cliches' AND target_id=? AND source_table=? UNION {legacy})")
                parameters.extend((cliche_id,table,cliche_id,table,cliche_id))
        if q:
            # SQLite casefold is intentionally supplied by Python for Unicode.
            clauses.append("EXISTS(SELECT 1 FROM json_each(r.values_json) j WHERE instr(casefold(j.value),?)>0)");parameters.append(q.casefold())
        for key,value in (filters or {}).items():
            if key == "work_ids":
                if table != "works" or not isinstance(value,(list,tuple,set)) or any(not isinstance(item,str) for item in value):
                    raise DataError(422,"work_ids 必须为作品编号列表")
                clauses.append("r.id IN (SELECT value FROM json_each(?))");parameters.append(_json(list(value)))
                continue
            field = {"category":"类别","seen":"用户是否完整体验过","看过":"用户是否完整体验过","role":"角色定位","character_trait":"性格基调" if table=="characters" else "人设塑造","plot_mechanism":"剧情机制"}.get(key,key)
            if field not in TABLES[table]["columns"]:
                raise DataError(422,f"不支持的筛选字段：{key}")
            if not isinstance(value, str):
                raise DataError(422,f"筛选字段 {key} 必须为字符串")
            if field=="性格基调" and value not in ("",*CLICHE_CHARACTER_TRAITS):
                raise DataError(422,"性格基调筛选值无效")
            if field in CLICHE_TAGS and value not in ("", *CLICHE_TAGS[field]):
                raise DataError(422,f"{field} 筛选值无效")
            if field == "用户是否完整体验过" and value == "unknown":
                clauses.append("COALESCE(json_extract(r.values_json,?), '') NOT IN ('是','否')");parameters.append('$."'+field+'"')
            elif value != "":
                clauses.append("json_extract(r.values_json,?)=?");parameters.extend(('$."'+field+'"',value))
        order = "r.sort_order,r.id"
        if sort_by:
            if sort_by not in {"id",*TABLES[table]["columns"]}:
                raise DataError(422,"排序字段无效")
            direction = "DESC" if sort_direction == "desc" else "ASC"
            if sort_by == "id": order = f"r.id {direction}"
            elif table=="cliches" and sort_by=="cliché评分":
                order=f"COALESCE((SELECT SUM(catalog_work_heat(w.values_json)) FROM works w JOIN work_cliches wc ON wc.work_id=w.id WHERE wc.cliche_id=r.id)/(SELECT SUM(catalog_work_heat(values_json)) FROM works),0) {direction},r.id"
            else:
                # Field is selected from a fixed schema allowlist; bind its JSON path.
                order = f"json_extract(r.values_json,?) COLLATE NOCASE {direction},r.id"
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            connection.create_function("casefold",1,lambda value:str(value).casefold(),deterministic=True)
            connection.create_function("catalog_work_heat",1,lambda value:catalog_schema.work_heat(json.loads(value))[0],deterministic=True)
            connection.execute("BEGIN")
            total = connection.execute(f"SELECT COUNT(*) FROM {table} r{where}",parameters).fetchone()[0]
            rows_parameters = list(parameters)
            if sort_by and sort_by != "id" and not (table=="cliches" and sort_by=="cliché评分"): rows_parameters.append('$."'+sort_by+'"')
            rows = connection.execute(f"SELECT r.* FROM {table} r{where} ORDER BY {order} LIMIT ? OFFSET ?",(*rows_parameters,limit,offset))
            items = [self._render(connection,table,row) for row in rows]
            categories = [row[0] for row in connection.execute("SELECT DISTINCT json_extract(values_json,'$.类别') category FROM works WHERE COALESCE(json_extract(values_json,'$.类别'),'') != '' ORDER BY category")]
            roles = [row[0] for row in connection.execute("SELECT DISTINCT json_extract(values_json,'$.角色定位') role FROM characters WHERE COALESCE(json_extract(values_json,'$.角色定位'),'') != '' ORDER BY role")]
            works = [{"id":row["id"], "title":row["title"]} for row in connection.execute("SELECT id,json_extract(values_json,'$.作品') title FROM works ORDER BY sort_order,id")]
            facets = {"categories":categories,"roles":roles,"works":works,"character_traits":list(CLICHE_CHARACTER_TRAITS),"plot_mechanisms":list(CLICHE_PLOT_MECHANISMS)}
            return {"items":items,"total":total,"offset":offset,"limit":limit,"facets":facets,"summary":{"matches":total,**self._stats(connection)}}

    def relations(self, table: str, identifier: str) -> dict[str, list[dict[str, Any]]]:
        """Return direct, labelled edges; sharing a work never creates an edge.

        The three material groups remain backward compatible. Each material has
        ``relationships`` (and a first ``relationship`` for simple clients), so
        several semantic edges to one target do not duplicate the target card.
        """
        require_table(table)
        with self.connect() as connection:
            connection.execute("BEGIN")
            row = self._row(connection, table, identifier)
            if modern_values(table,json.loads(row["values_json"])):
                return self._modern_relations(connection,table,identifier)
            result = {key: [] for key in TABLES}
            result["edges"] = []
            targets: dict[tuple[str, str], dict[str, Any]] = {}

            def add(edge: dict[str, Any], target_table: str, target_id: str, label: str) -> None:
                if table == target_table == "cliches":
                    return
                direction = "outgoing" if edge["source_table"] == table and edge["source_id"] == identifier else "incoming"
                relation = {"id": edge["id"], "type": edge["relation_type"], "label": label,
                            "description": edge["description"], "direction": direction,
                            "metadata": edge.get("metadata", {}), "source": edge.get("source", "explicit")}
                key = (target_table, target_id)
                if key not in targets:
                    item = self._render(connection, target_table, self._row(connection, target_table, target_id))
                    item["relationship"] = relation
                    item["relationships"] = []
                    targets[key] = item
                    result[target_table].append(item)
                targets[key]["relationships"].append(relation)
                result["edges"].append({**edge, "direction": direction, "label": label})

            if table in {"works", "characters"}:
                children = connection.execute("SELECT * FROM characters WHERE work_id = ? ORDER BY sort_order,id", (identifier,)) if table == "works" else [row]
                for child in children:
                    values = json.loads(child["values_json"])
                    role = values.get("角色定位", "")
                    label = "登场人物" if table == "works" else "所属作品"
                    if role:
                        label += f" · {role}"
                    edge = {"id": f"character:{child['id']}", "source_table": "characters", "source_id": child["id"],
                            "target_table": "works", "target_id": child["work_id"], "relation_type": "character_of",
                            "description": role, "metadata": {"角色定位": role}, "source": "character_work"}
                    add(edge, "characters" if table == "works" else "works", child["id"] if table == "works" else child["work_id"], label)
            if table in {"works", "cliches"}:
                where, order = ("work_id", "work_position,cliche_id") if table == "works" else ("cliche_id", "cliche_position,work_id")
                for link in connection.execute(f"SELECT * FROM work_cliches WHERE {where} = ? ORDER BY {order}", (identifier,)):
                    edge = {"id": f"work-cliche:{link['work_id']}:{link['cliche_id']}", "source_table": "works", "source_id": link["work_id"],
                            "target_table": "cliches", "target_id": link["cliche_id"], "relation_type": "uses_cliche",
                            "description": link["detail"], "metadata": {}, "source": "work_cliches"}
                    add(edge, "cliches" if table == "works" else "works", link["cliche_id"] if table == "works" else link["work_id"], "使用桥段" if table == "works" else "出现于")
            for link in connection.execute("""SELECT * FROM material_relations
                WHERE (source_table = ? AND source_id = ?) OR (target_table = ? AND target_id = ?) ORDER BY id""", (table, identifier, table, identifier)):
                edge = self._render_material_relation(link)
                outgoing = edge["source_table"] == table and edge["source_id"] == identifier
                add(edge, edge["target_table"] if outgoing else edge["source_table"], edge["target_id"] if outgoing else edge["source_id"], edge["relation_type"])
            return result

    @staticmethod
    def _render_material_relation(row: sqlite3.Row) -> dict[str, Any]:
        values = dict(row)
        values["metadata"] = json.loads(values.pop("metadata_json"))
        return values

    def _validate_material_relation(self, connection: sqlite3.Connection, values: dict[str, Any]) -> dict[str, Any]:
        """Validate canonical relation rows, preserving future fields in metadata."""
        if not isinstance(values, dict):
            raise DataError(422, "素材关联必须为对象")
        normalized: dict[str, Any] = {}
        for field in ("id", "source_table", "source_id", "target_table", "target_id", "relation_type", "description"):
            value = values.get(field, "")
            if not isinstance(value, str):
                raise DataError(422, f"关联字段 {field} 必须为文本")
            normalized[field] = value
        if not normalized["id"] or normalized["id"] != normalized["id"].strip() or re.search(r"[/\\\x00-\x1f\x7f]", normalized["id"]) or normalized["id"] in {".", ".."}:
            raise DataError(422, "关联 ID 不能为空、包含首尾空白、斜杠或控制字符")
        for side in ("source", "target"):
            key = normalized[f"{side}_table"]
            if key not in TABLES:
                raise DataError(422, f"关联 {normalized['id']} 的 {side}_table 无效：{key}")
            if connection.execute(f"SELECT 1 FROM {key} WHERE id = ?", (normalized[f"{side}_id"],)).fetchone() is None:
                raise DataError(422, f"关联 {normalized['id']} 引用的{TABLES[key]['label']}不存在：{normalized[f'{side}_id']}")
        if normalized["source_table"] == normalized["target_table"] == "cliches":
            raise DataError(422, "桥段不能关联桥段；请选择作品或人物")
        if (normalized["source_table"], normalized["source_id"]) == (normalized["target_table"], normalized["target_id"]):
            raise DataError(422, "素材不能关联自身")
        normalized["relation_type"] = normalized["relation_type"].strip()
        if not normalized["relation_type"]:
            raise DataError(422, f"关联 {normalized['id']} 缺少关系类型（relation_type）")
        duplicate = connection.execute("""SELECT id FROM material_relations
            WHERE source_table = ? AND source_id = ? AND target_table = ? AND target_id = ? AND relation_type = ? AND id != ?""", (
                normalized["source_table"], normalized["source_id"], normalized["target_table"], normalized["target_id"], normalized["relation_type"], normalized["id"]
            )).fetchone()
        if duplicate:
            raise DataError(409, f"关联 {normalized['id']} 与 {duplicate['id']} 的两端素材及关系类型相同；请合并这两条关联或使用不同的关系类型")
        try:
            metadata = values["metadata"] if "metadata" in values else json.loads(values.get("metadata_json", "{}"))
        except (TypeError, ValueError) as error:
            raise DataError(422, f"关联 {normalized['id']} 的 metadata_json 无效") from error
        if not isinstance(metadata, dict):
            raise DataError(422, "关联 metadata 必须为对象")
        unknown = {key: value for key, value in values.items() if key not in {*normalized, "revision", "metadata", "metadata_json"}}
        try:
            normalized["metadata_json"] = _json({**metadata, **unknown})
        except (TypeError, ValueError) as error:
            raise DataError(422, "关联的扩展字段必须可保存为 JSON") from error
        return normalized

    def _upsert_material_relation(self, connection: sqlite3.Connection, values: dict[str, Any], *, expected_revision: int | None = None, conflict_policy: str = "error") -> str:
        """Import helper; caller owns a transaction covering all material tables."""
        if conflict_policy not in {"error", "skip", "update"}:
            raise DataError(422, "关联冲突策略必须为 error、skip 或 update")
        values = self._validate_material_relation(connection, values)
        identifier = values["id"]
        previous = connection.execute("SELECT * FROM material_relations WHERE id = ?", (identifier,)).fetchone()
        if previous:
            if expected_revision is not None and previous["revision"] != expected_revision:
                raise DataError(409, f"关联 {identifier} 已被修改，请刷新后重试")
            if all((json.loads(previous[field]) == json.loads(value) if field == "metadata_json" else previous[field] == value) for field, value in values.items()):
                return identifier
            if conflict_policy == "skip":
                return identifier
            if conflict_policy == "error":
                raise DataError(409, f"关联 ID 已存在：{identifier}")
            assignments = ",".join(f"{field} = ?" for field in values if field != "id")
            connection.execute(f"UPDATE material_relations SET {assignments},revision = revision + 1 WHERE id = ?", (*[value for field, value in values.items() if field != "id"], identifier))
            revision = previous["revision"] + 1
        else:
            if expected_revision is not None:
                raise DataError(404, f"关联不存在：{identifier}")
            tombstone = connection.execute("SELECT revision FROM record_versions WHERE table_key = 'material_relations' AND record_id = ?", (identifier,)).fetchone()
            revision = tombstone[0] + 1 if tombstone else 1
            columns = list(values)
            connection.execute(f"INSERT INTO material_relations ({','.join(columns)},revision) VALUES ({','.join('?' for _ in range(len(columns) + 1))})", (*values.values(), revision))
        self._remember_version(connection, "material_relations", identifier, revision)
        return identifier

    def list_material_relations(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [self._render_material_relation(row) for row in connection.execute("SELECT * FROM material_relations ORDER BY id")]

    def create_material_relation(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as connection:
            identifier = self._upsert_material_relation(connection, {"id": f"R-{uuid4().hex}", **values})
            return self._render_material_relation(connection.execute("SELECT * FROM material_relations WHERE id = ?", (identifier,)).fetchone())

    def update_material_relation(self, identifier: str, values: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        with self.transaction() as connection:
            previous = connection.execute("SELECT * FROM material_relations WHERE id = ?", (identifier,)).fetchone()
            if previous is None:
                raise DataError(404, f"关联不存在：{identifier}")
            if "id" in values and values["id"] != identifier:
                raise DataError(422, "不能更改已有关联的 ID")
            self._upsert_material_relation(connection, {**dict(previous), **values}, expected_revision=expected_revision, conflict_policy="update")
            return self._render_material_relation(connection.execute("SELECT * FROM material_relations WHERE id = ?", (identifier,)).fetchone())

    def delete_material_relation(self, identifier: str, expected_revision: int) -> dict[str, int]:
        with self.transaction() as connection:
            previous = connection.execute("SELECT * FROM material_relations WHERE id = ?", (identifier,)).fetchone()
            if previous is None:
                raise DataError(404, f"关联不存在：{identifier}")
            if previous["revision"] != expected_revision:
                raise DataError(409, f"关联 {identifier} 已被修改，请刷新后重试")
            connection.execute("DELETE FROM material_relations WHERE id = ?", (identifier,))
            return {"deleted": 1}

    def _next_order(self, connection: sqlite3.Connection, table: str) -> int:
        return connection.execute(f"SELECT COALESCE(MAX(sort_order), -1) + 1 FROM {table}").fetchone()[0]

    def _insert_raw(self, connection: sqlite3.Connection, table: str, values: dict[str, str]) -> str:
        values = validate_values(table, values)
        identifier = record_id(table, values)
        if connection.execute(f"SELECT 1 FROM {table} WHERE id = ?", (identifier,)).fetchone():
            raise DataError(409, f"ID 已存在：{identifier}")
        order = self._next_order(connection, table)
        version = connection.execute("SELECT revision FROM record_versions WHERE table_key = ? AND record_id = ?", (table, identifier)).fetchone()
        revision = version[0] + 1 if version else 1
        if modern_values(table, values):
            if table == "characters":
                connection.execute("INSERT INTO characters (id,work_id,values_json,sort_order,revision) VALUES (?,NULL,?,?,?)", (identifier, _json(values), order, revision))
            elif table == "works":
                connection.execute("INSERT INTO works (id,values_json,cliche_layout,sort_order,revision) VALUES (?,?,'[]',?,?)", (identifier, _json(values), order, revision))
            else:
                connection.execute("INSERT INTO cliches (id,values_json,sort_order,revision) VALUES (?,?,?,?)", (identifier, _json(values), order, revision))
            self._remember_version(connection, table, identifier, revision)
            return identifier
        if table == "characters":
            self._validate_character(connection, values)
            connection.execute("INSERT INTO characters (id,work_id,values_json,sort_order,revision) VALUES (?,?,?,?,?)", (identifier, values["作品ID"], _json(values), order, revision))
        elif table == "works":
            layout, _ = parse_work_relations(values[WORK_RELATION_FIELD])
            connection.execute("INSERT INTO works (id,values_json,cliche_layout,sort_order,revision) VALUES (?,?,?,?,?)", (identifier, _json(values), _json(layout), order, revision))
        else:
            connection.execute("INSERT INTO cliches (id,values_json,sort_order,revision) VALUES (?,?,?,?)", (identifier, _json(values), order, revision))
        self._remember_version(connection, table, identifier, revision)
        if table == "works":
            self._bump(connection, "cliches", self._cliches_for_labels(connection, {work_label(values)}))
        return identifier

    def _validate_character(self, connection: sqlite3.Connection, values: dict[str, str]) -> None:
        row = connection.execute("SELECT values_json FROM works WHERE id = ?", (values["作品ID"],)).fetchone()
        if row is None:
            raise DataError(422, f"人物引用的作品不存在：{values['作品ID']}")
        work = json.loads(row[0])
        if values["作品"] and values["作品"] != work["作品"]:
            raise DataError(422, f"人物的作品名称与 {values['作品ID']} 不一致")
        values["作品"] = work["作品"]

    def _label_map(self, connection: sqlite3.Connection) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for row in connection.execute("SELECT * FROM works"):
            result.setdefault(work_label(json.loads(row["values_json"])), []).append(row["id"])
        return result

    def _cliches_for_labels(self, connection: sqlite3.Connection, labels: set[str]) -> set[str]:
        if not connection.execute("SELECT 1 FROM work_cliches LIMIT 1").fetchone():
            return set()
        work_ids = {row["id"] for row in connection.execute("SELECT id,values_json FROM works") if work_label(json.loads(row["values_json"])) in labels}
        return {row["cliche_id"] for row in connection.execute("SELECT work_id,cliche_id FROM work_cliches") if row["work_id"] in work_ids}

    def _appearance_ids(self, connection: sqlite3.Connection, text: str) -> list[str]:
        labels = self._label_map(connection)
        result = []
        for label in split_occurrences(text):
            qualified = QUALIFIED_WORK_LABEL.match(label)
            if qualified:
                base, identifier = qualified.groups()
                row = connection.execute("SELECT values_json FROM works WHERE id = ?", (identifier,)).fetchone()
                if row is None or work_label(json.loads(row[0])) != base:
                    raise DataError(422, f"出现作品的 ID 限定与作品名称或版本不一致：{label}")
                matches = [identifier]
            else:
                matches = labels.get(label, [])
            if len(matches) != 1:
                reason = "未找到" if not matches else "存在同名同版本作品，无法唯一识别"
                raise DataError(422, f"出现作品{reason}：{label}")
            if matches[0] in result:
                raise DataError(422, f"桥段重复关联作品：{label}")
            result.append(matches[0])
        return result

    def _bump(self, connection: sqlite3.Connection, table: str, identifiers: set[str]) -> None:
        for identifier in identifiers:
            connection.execute(f"UPDATE {table} SET revision = revision + 1 WHERE id = ?", (identifier,))
            row = connection.execute(f"SELECT revision FROM {table} WHERE id = ?", (identifier,)).fetchone()
            if row:
                self._remember_version(connection, table, identifier, row[0])

    def _remember_version(self, connection: sqlite3.Connection, table: str, identifier: str, revision: int) -> None:
        connection.execute("INSERT INTO record_versions VALUES (?,?,?) ON CONFLICT(table_key,record_id) DO UPDATE SET revision=excluded.revision", (table, identifier, revision))

    def _sync_work(self, connection: sqlite3.Connection, identifier: str, text: str, *, bump_related: bool = True) -> None:
        layout, details = parse_work_relations(text)
        old = {row["cliche_id"]: dict(row) for row in connection.execute("SELECT * FROM work_cliches WHERE work_id = ?", (identifier,))}
        for cid in details:
            if connection.execute("SELECT 1 FROM cliches WHERE id = ?", (cid,)).fetchone() is None:
                raise DataError(422, f"作品引用的桥段不存在：{cid}")
        for cid in old.keys() - details.keys():
            connection.execute("DELETE FROM work_cliches WHERE work_id = ? AND cliche_id = ?", (identifier, cid))
        for position, (cid, detail) in enumerate(details.items()):
            cpos = old[cid]["cliche_position"] if cid in old else connection.execute("SELECT COALESCE(MAX(cliche_position),-1)+1 FROM work_cliches WHERE cliche_id = ?", (cid,)).fetchone()[0]
            connection.execute("INSERT INTO work_cliches VALUES (?,?,?,?,?) ON CONFLICT(work_id,cliche_id) DO UPDATE SET detail=excluded.detail,work_position=excluded.work_position", (identifier, cid, detail, position, cpos))
        connection.execute("UPDATE works SET cliche_layout = ? WHERE id = ?", (_json(layout), identifier))
        if bump_related:
            self._bump(connection, "cliches", set(old) ^ set(details))

    def _sync_cliche(self, connection: sqlite3.Connection, identifier: str, text: str, *, bump_related: bool = True) -> None:
        work_ids = self._appearance_ids(connection, text)
        old = {row["work_id"]: dict(row) for row in connection.execute("SELECT * FROM work_cliches WHERE cliche_id = ?", (identifier,))}
        for wid in old.keys() - set(work_ids):
            connection.execute("DELETE FROM work_cliches WHERE work_id = ? AND cliche_id = ?", (wid, identifier))
            self._normalize_work_layout(connection, wid)
        for position, wid in enumerate(work_ids):
            if wid in old:
                connection.execute("UPDATE work_cliches SET cliche_position = ? WHERE work_id = ? AND cliche_id = ?", (position, wid, identifier))
            else:
                wpos = connection.execute("SELECT COALESCE(MAX(work_position),-1)+1 FROM work_cliches WHERE work_id = ?", (wid,)).fetchone()[0]
                connection.execute("INSERT INTO work_cliches VALUES (?,?,?,?,?)", (wid, identifier, f"{identifier}：", wpos, position))
        if bump_related:
            self._bump(connection, "works", set(old) ^ set(work_ids))

    def _normalize_work_layout(self, connection: sqlite3.Connection, identifier: str) -> None:
        rendered = self._render(connection, "works", self._row(connection, "works", identifier))["values"][WORK_RELATION_FIELD]
        layout, _ = parse_work_relations(rendered)
        connection.execute("UPDATE works SET cliche_layout = ? WHERE id = ?", (_json(layout), identifier))

    def create_record(self, table: str, values: dict[str, str]) -> dict[str, Any]:
        require_table(table)
        values = validate_values(table, values)
        with self.transaction() as connection:
            identifier = self._insert_raw(connection, table, values)
            if modern_values(table, values):
                self._sync_modern_views(connection, table, identifier, {}, values)
                self._rebuild_library_links(connection)
            elif table == "works":
                self._sync_work(connection, identifier, values[WORK_RELATION_FIELD])
            elif table == "cliches":
                self._sync_cliche(connection, identifier, values["出现作品"])
            return self._render(connection, table, self._row(connection, table, identifier))

    def _update_raw(self, connection: sqlite3.Connection, table: str, identifier: str, values: dict[str, str], expected_revision: int) -> None:
        row = self._row(connection, table, identifier)
        if row["revision"] != expected_revision:
            raise DataError(409, f"记录 {identifier} 已被修改，请刷新后重试")
        if record_id(table, values) != identifier:
            raise DataError(422, "不能更改已有记录的 ID")
        previous = self._render(connection, table, row)["values"]
        if modern_values(table, values):
            connection.execute(f"UPDATE {table} SET values_json=?,revision=revision+1 WHERE id=?", (_json(values), identifier))
            self._remember_version(connection, table, identifier, expected_revision + 1)
            return
        label_changed = table == "works" and (previous["作品"] != values["作品"] or previous["版本与原作关系"] != values["版本与原作关系"])
        affected_labels = {work_label(previous), work_label(values)} if label_changed else set()
        affected_cliches = self._cliches_for_labels(connection, affected_labels) if label_changed else set()
        if table == "characters":
            self._validate_character(connection, values)
            if values["作品ID"] != row["work_id"] and values["版本"] == previous["版本"]:
                target = json.loads(self._row(connection, "works", values["作品ID"])["values_json"])
                values["版本"] = character_version(target["版本与原作关系"])
            connection.execute("UPDATE characters SET work_id = ?, values_json = ?, revision = revision + 1 WHERE id = ?", (values["作品ID"], _json(values), identifier))
        else:
            connection.execute(f"UPDATE {table} SET values_json = ?, revision = revision + 1 WHERE id = ?", (_json(values), identifier))
        self._remember_version(connection, table, identifier, expected_revision + 1)
        if label_changed:
            for character in connection.execute("SELECT * FROM characters WHERE work_id = ?", (identifier,)).fetchall():
                child = json.loads(character["values_json"])
                child["作品"] = values["作品"]
                if previous["版本与原作关系"] != values["版本与原作关系"]:
                    child["版本"] = character_version(values["版本与原作关系"])
                connection.execute("UPDATE characters SET values_json = ?, revision = revision + 1 WHERE id = ?", (_json(child), character["id"]))
                self._remember_version(connection, "characters", character["id"], character["revision"] + 1)
            affected_cliches |= self._cliches_for_labels(connection, affected_labels)
            self._bump(connection, "cliches", affected_cliches)

    def update_record(self, table: str, identifier: str, values: dict[str, str], expected_revision: int) -> dict[str, Any]:
        require_table(table)
        with self.transaction() as connection:
            current = self._render(connection, table, self._row(connection, table, identifier))["values"]
            # Validate in the record's own schema; shared fields cannot imply a
            # schema switch when editing an old compatibility record.
            if catalog_schema.is_current(table,current):
                values=dict(values)
                if table=="works" and "两句话概括" in values: values["剧情梗概"]=values.pop("两句话概括")
                if table=="characters" and "角色" in values: values["角色名"]=values.pop("角色")
                if table=="cliches": values.pop("cliché评分",None)
            if not catalog_schema.is_current(table,current) and any(k in values for k in ({"works":{"剧情梗概","讨论热度","商业规模"},"characters":{"角色名","性格基调","视觉元素设计","初始状态","阶段状态"},"cliches":{"cliché评分"}}[table])):
                connection.execute("INSERT OR IGNORE INTO legacy_values VALUES (?,?,?)",(table,identifier,_json(current)))
                current=catalog_schema.project_values(table,current)
            candidate = {**current, **values}
            if table=="cliches" and catalog_schema.is_current(table,candidate):candidate["cliché评分"]=""
            merged = validate_values(table, candidate)
            patch = dict(values)
            if table == "works" and "看过" in patch:
                patch["用户是否完整体验过"] = patch.pop("看过")
            if modern_values(table, current):
                self._update_raw(connection, table, identifier, merged, expected_revision)
                sync=getattr(self,"character_sync",None)
                if table=="characters" and sync is not None and any(current.get(k)!=merged.get(k) for k in ("角色名","别名","角色定位","性格基调","视觉元素设计")):
                    sync(connection,identifier,merged)
                association_changed = any(current.get(field) != merged.get(field) for field in ASSOCIATIONS)
                title_changed = table == "works" and current.get("作品") != merged.get("作品")
                if association_changed or title_changed:
                    self._sync_modern_views(connection, table, identifier, current, merged)
                    self._rebuild_library_links(connection)
                return self._render(connection, table, self._row(connection, table, identifier))
            if table == "characters" and merged["作品ID"] != current["作品ID"]:
                target = json.loads(self._row(connection, "works", merged["作品ID"])["values_json"])
                if "作品" not in patch:
                    merged["作品"] = target["作品"]
                if "版本" not in patch:
                    merged["版本"] = character_version(target["版本与原作关系"])
            self._update_raw(connection, table, identifier, merged, expected_revision)
            if table == "works" and WORK_RELATION_FIELD in patch:
                self._sync_work(connection, identifier, merged[WORK_RELATION_FIELD])
            elif table == "cliches" and "出现作品" in patch:
                self._sync_cliche(connection, identifier, merged["出现作品"])
            return self._render(connection, table, self._row(connection, table, identifier))

    def delete_record(self, table: str, identifier: str, expected_revision: int, cascade: bool = False) -> dict[str, int]:
        require_table(table)
        with self.transaction() as connection:
            row = self._row(connection, table, identifier)
            if row["revision"] != expected_revision:
                raise DataError(409, "记录已被修改，请刷新后重试")
            if modern_values(table, json.loads(row["values_json"])):
                links = connection.execute("SELECT COUNT(*) FROM library_links WHERE (source_table=? AND source_id=?) OR (target_table=? AND target_id=?)", (table,identifier,table,identifier)).fetchone()[0]
                manual = connection.execute("SELECT COUNT(*) FROM material_relations WHERE (source_table=? AND source_id=?) OR (target_table=? AND target_id=?)", (table,identifier,table,identifier)).fetchone()[0]
                if (links or manual) and not cascade:
                    raise DataError(409, f"存在 {links + manual} 条关联；请确认解除关联并删除")
                self._remove_library_target(connection, table, identifier)
                connection.execute(f"DELETE FROM {table} WHERE id=?", (identifier,))
                connection.execute("DELETE FROM legacy_values WHERE table_key=? AND record_id=?", (table, identifier))
                self._rebuild_library_links(connection)
                return {"deleted": 1}
            related = 0
            cids: set[str] = set()
            wids: set[str] = set()
            if table == "works":
                related = connection.execute("SELECT COUNT(*) FROM characters WHERE work_id = ?", (identifier,)).fetchone()[0]
                cids = {item[0] for item in connection.execute("SELECT cliche_id FROM work_cliches WHERE work_id = ?", (identifier,))}
                related += len(cids)
                cids |= self._cliches_for_labels(connection, {work_label(json.loads(row["values_json"]))})
            elif table == "cliches":
                wids = {item[0] for item in connection.execute("SELECT work_id FROM work_cliches WHERE cliche_id = ?", (identifier,))}
                related = len(wids)
            related += connection.execute("""SELECT COUNT(*) FROM material_relations
                WHERE (source_table = ? AND source_id = ?) OR (target_table = ? AND target_id = ?)""", (table, identifier, table, identifier)).fetchone()[0]
            if related and not cascade:
                raise DataError(409, f"存在 {related} 条关联；请确认级联删除")
            count = 1 + (connection.execute("SELECT COUNT(*) FROM characters WHERE work_id = ?", (identifier,)).fetchone()[0] if table == "works" else 0)
            if table == "works":
                connection.execute("DELETE FROM characters WHERE work_id=?", (identifier,))
            connection.execute(f"DELETE FROM {table} WHERE id = ?", (identifier,))
            self._bump(connection, "cliches", cids)
            self._bump(connection, "works", wids)
            for wid in wids:
                self._normalize_work_layout(connection, wid)
            return {"deleted": count}

    def _upgrade_legacy_rows(self, connection: sqlite3.Connection) -> None:
        """Archive all visible v3 values before replacing any parent view."""
        # Legacy reverse columns are rendered from authoritative relations and
        # can differ from a stale values_json field after interactive edits.
        snapshots = {table:{row["id"]:self._render(connection,table,row)["values"] for row in connection.execute(f"SELECT * FROM {table}").fetchall()} for table in TABLES}
        for table in TABLES:
            for row in connection.execute(f"SELECT * FROM {table}").fetchall():
                old = snapshots[table][row["id"]]
                if modern_values(table, old):
                    continue
                connection.execute("INSERT OR IGNORE INTO legacy_values VALUES (?,?,?)", (table,row["id"],_json(old)))
                new = catalog_schema.project_values(table, old)
                if table == "works":
                    new["热度权重"] = old.get("热度", "")
                    new["关联角色"] = _json([r[0] for r in connection.execute("SELECT id FROM characters WHERE work_id=? ORDER BY sort_order,id", (row["id"],))])
                    new["关联cliché"] = _json([r[0] for r in connection.execute("SELECT cliche_id FROM work_cliches WHERE work_id=? ORDER BY work_position,cliche_id", (row["id"],))])
                elif table == "characters":
                    new["关联作品"] = _json([{"作品ID":row["work_id"]}] if row["work_id"] else [])
                else:
                    new["clichéID"] = row["id"]
                    new["cliché内容"] = re.sub(r"^C\d+[｜|：:]?", "", old.get("cliché内容", ""), count=1)
                    new["cliché程度（%）"] = old.get("cliché程度", "")
                    new["关联作品"] = _json([{"作品ID":r["work_id"],"关联内容":r["detail"]} for r in connection.execute("SELECT * FROM work_cliches WHERE cliche_id=? ORDER BY cliche_position,work_id", (row["id"],))])
                new = catalog_schema.project_values(table,new)
                connection.execute(f"UPDATE {table} SET values_json=? WHERE id=?", (_json(new),row["id"]))

    def _library_rows(self, connection: sqlite3.Connection) -> dict[str, dict[str, dict[str, str]]]:
        return {table: {row["id"]: json.loads(row["values_json"]) for row in connection.execute(f"SELECT id,values_json FROM {table}")} for table in TABLES}

    @staticmethod
    def _association_id(item: Any, id_field: str, label: str) -> str:
        identifier = item if isinstance(item, str) else item.get(id_field)
        if not isinstance(identifier, str) or not identifier:
            raise DataError(422, f"{label} 缺少 {id_field}")
        return identifier

    def _rebuild_library_links(self, connection: sqlite3.Connection) -> None:
        """Materialize ID-based links without discarding source order or payload."""
        records = self._library_rows(connection)
        links = []
        work_cliches: dict[tuple[str,str], list[Any]] = {}
        modern_work_ids = {identifier for identifier, values in records["works"].items() if modern_values("works", values)}
        modern_cliche_ids = {identifier for identifier, values in records["cliches"].items() if modern_values("cliches", values)}
        for table, rows in records.items():
            for identifier, values in rows.items():
                if not modern_values(table, values):
                    continue
                for field, (target_table, id_field) in ASSOCIATIONS.items():
                    if field not in values:
                        continue
                    seen: set[tuple[str, str]] = set()
                    for position, item in enumerate(association_items(values[field], f"{TABLES[table]['label']} {identifier} 的{field}")):
                        target_id = self._association_id(item, id_field, f"{identifier} 的{field}")
                        context = item.get("作品ID", "") if isinstance(item, dict) and target_table in {"characters","cliches"} else ""
                        if (target_id,context) in seen:
                            raise DataError(422, f"{identifier} 的{field}重复引用 {target_id}")
                        seen.add((target_id,context))
                        if target_id not in records[target_table]:
                            raise DataError(422, f"{TABLES[table]['label']} {identifier} 的{field}引用 {TABLES[target_table]['label']} {target_id}，对应表缺少该 ID；两个文件疑似版本不一致")
                        if table == target_table and identifier == target_id:
                            raise DataError(422, f"{identifier} 不能关联自身")
                        if table == target_table == "cliches":
                            raise DataError(422, "桥段不能关联桥段")
                        if isinstance(item, dict):
                            if "作品名" in item and target_table == "works" and item["作品名"] != records["works"][target_id].get("作品", ""):
                                raise DataError(422, f"{identifier} 的关联作品 {target_id} 名称与作品表不一致；两个文件疑似版本不一致")
                            if "人物设定" in item and not isinstance(item["人物设定"], dict):
                                raise DataError(422, f"{identifier} 的人物设定必须为 JSON 对象")
                            if context and context not in records["works"]:
                                raise DataError(422, f"{identifier} 的关联角色 {target_id} 引用作品 {context}，作品表缺少该 ID")
                        links.append((table,identifier,field,position,target_table,target_id,_json(item)))
                        if {table,target_table} == {"works","cliches"}:
                            wid,cid = (identifier,target_id) if table == "works" else (target_id,identifier)
                            edge = work_cliches.setdefault((wid,cid), ["",0,0])
                            edge[1 if table == "works" else 2] = position
                            if isinstance(item, dict) and item.get("关联内容") is not None:
                                edge[0] = str(item["关联内容"])
        def relation_context(source_table: str,target_table: str,payload_json: str) -> str:
            payload = json.loads(payload_json)
            return str(payload.get("作品ID","")) if {source_table,target_table} == {"characters","cliches"} and isinstance(payload,dict) else ""
        claims = {(source_table,source_id,target_table,target_id,relation_context(source_table,target_table,payload)) for source_table,source_id,field,position,target_table,target_id,payload in links}
        for source_table,source_id,target_table,target_id,context in claims:
            if source_table == target_table:
                continue
            target = records[target_table][target_id]
            reverse = REVERSE_FIELD[source_table]
            if modern_values(target_table,target) and reverse in target and (target_table,target_id,source_table,source_id,context) not in claims:
                suffix = f"（作品 {context}）" if context else ""
                raise DataError(422,f"{source_id} 与 {target_id}{suffix} 的双向关联不一致；请使用同一版本的三张表，已有记录更新请选择显式更新")
        connection.execute("DELETE FROM library_links")
        connection.executemany("INSERT INTO library_links VALUES (?,?,?,?,?,?,?)", links)
        # Compatibility view is generated from the same canonical IDs. No title
        # matching is used, including when several works share a name.
        for wid in modern_work_ids:
            connection.execute("DELETE FROM work_cliches WHERE work_id=?", (wid,))
        for cid in modern_cliche_ids:
            connection.execute("DELETE FROM work_cliches WHERE cliche_id=?", (cid,))
        connection.executemany("INSERT INTO work_cliches VALUES (?,?,?,?,?)", [(wid,cid,*edge) for (wid,cid),edge in work_cliches.items()])

    def _write_library_values(self, connection: sqlite3.Connection, table: str, identifier: str, values: dict[str,str]) -> None:
        previous = self._row(connection,table,identifier)
        if json.loads(previous["values_json"]) == values:
            return
        connection.execute(f"UPDATE {table} SET values_json=?,revision=revision+1 WHERE id=?", (_json(values),identifier))
        self._remember_version(connection,table,identifier,previous["revision"]+1)

    def _sync_modern_views(self, connection: sqlite3.Connection, table: str, identifier: str, before: dict[str,str], after: dict[str,str]) -> None:
        """A direct association edit updates its reverse view in the same transaction."""
        reverse = REVERSE_FIELD[table]
        if table == "works" and before.get("作品") != after.get("作品"):
            for source_table,rows in self._library_rows(connection).items():
                for source_id,target in rows.items():
                    if "关联作品" not in target: continue
                    items = association_items(target["关联作品"],"关联作品")
                    changed = False
                    for item in items:
                        if isinstance(item,dict) and item.get("作品ID") == identifier and "作品名" in item:
                            item["作品名"] = after.get("作品",""); changed = True
                    if changed:
                        target["关联作品"] = _json(items)
                        self._write_library_values(connection,source_table,source_id,target)
        for field,(target_table,id_field) in ASSOCIATIONS.items():
            if field not in after or before.get(field,"[]") == after[field] or table == target_table:
                continue
            contextual = {table,target_table} == {"characters","cliches"}
            def key(item: Any) -> tuple[str,str]:
                return (self._association_id(item,id_field,field), str(item.get("作品ID","")) if contextual and isinstance(item,dict) else "")
            old = {key(item):item for item in association_items(before.get(field,"[]"),field)}
            new = {key(item):item for item in association_items(after[field],field)}
            for target_id,context in old.keys() | new.keys():
                target_row = self._row(connection,target_table,target_id)
                target = json.loads(target_row["values_json"])
                if not modern_values(target_table,target) or reverse not in target:
                    continue
                items = association_items(target[reverse],reverse)
                own_id = TABLES[table]["id_field"]
                def matches(item: Any) -> bool:
                    return self._association_id(item,own_id,reverse) == identifier and (not contextual or (str(item.get("作品ID","")) if isinstance(item,dict) else "") == context)
                existing = [item for item in items if matches(item)]
                if (target_id,context) in new:
                    payload = new[(target_id,context)]
                    if not existing:
                        addition: Any = {own_id:identifier} if table == "works" or contextual else identifier
                        if contextual and context: addition["作品ID"] = context
                        if isinstance(payload,dict) and isinstance(addition,dict) and "关联内容" in payload: addition["关联内容"] = payload["关联内容"]
                        items.append(addition)
                    elif isinstance(payload,dict) and "关联内容" in payload:
                        for item in existing:
                            if isinstance(item,dict) and "关联内容" in item: item["关联内容"] = payload["关联内容"]
                else:
                    items = [item for item in items if not matches(item)]
                if items != association_items(target[reverse],reverse):
                    target[reverse] = _json(items)
                    self._write_library_values(connection,target_table,target_id,target)

    def _remove_library_target(self, connection: sqlite3.Connection, table: str, identifier: str) -> None:
        for source_table, rows in self._library_rows(connection).items():
            for source_id, values in rows.items():
                if not modern_values(source_table, values):
                    continue
                changed = False
                for field,(target_table,id_field) in ASSOCIATIONS.items():
                    if field not in values:
                        continue
                    items = association_items(values[field],field)
                    keep = [item for item in items if not ((target_table == table and self._association_id(item,id_field,field) == identifier) or (table == "works" and isinstance(item,dict) and target_table in {"characters","cliches"} and item.get("作品ID") == identifier))]
                    if keep != items:
                        values[field] = _json(keep)
                        changed = True
                if changed and (source_table,source_id) != (table,identifier):
                    self._write_library_values(connection,source_table,source_id,values)

    def _modern_relations(self, connection: sqlite3.Connection, table: str, identifier: str) -> dict[str, Any]:
        result: dict[str,Any] = {key:[] for key in TABLES}
        result["edges"] = []
        targets: dict[tuple[str,str],dict[str,Any]] = {}
        links = connection.execute("SELECT * FROM library_links WHERE (source_table=? AND source_id=?) OR (target_table=? AND target_id=?) ORDER BY source_table,source_id,field,position", (table,identifier,table,identifier))
        for link in links:
            outgoing = link["source_table"] == table and link["source_id"] == identifier
            target_table,target_id = (link["target_table"],link["target_id"]) if outgoing else (link["source_table"],link["source_id"])
            if table == target_table == "cliches":
                continue
            payload = json.loads(link["payload_json"])
            meta = payload if isinstance(payload,dict) else {}
            description = str(meta.get("关联内容",meta.get("关联方式","")))
            label = description or ("登场人物" if {table,target_table} == {"works","characters"} else "关联桥段" if "cliches" in {table,target_table} else "关联作品")
            relation = {"id":f"library:{link['source_table']}:{link['source_id']}:{link['field']}:{link['position']}","type":link["field"],"label":label,"description":description,"metadata":meta,"direction":"outgoing" if outgoing else "incoming","source":"library_links","relation_type":link["field"]}
            key = target_table,target_id
            if key not in targets:
                item = self._render(connection,target_table,self._row(connection,target_table,target_id))
                item["relationships"] = []
                targets[key] = item
                result[target_table].append(item)
            # Reverse arrays describe the same edge; prefer the rich payload.
            def meaning(metadata):
                ignored={TABLES[table]["id_field"],TABLES[target_table]["id_field"]}
                return {k:v for k,v in metadata.items() if k not in ignored}
            if any(r["description"] == description and meaning(r["metadata"]) == meaning(meta) for r in targets[key]["relationships"]):
                continue
            targets[key]["relationships"].append(relation)
            targets[key]["relationship"] = max(targets[key]["relationships"],key=lambda r:len(r["description"])+len(_json(r["metadata"])))
            result["edges"].append({**relation,"source_table":link["source_table"],"source_id":link["source_id"],"target_table":link["target_table"],"target_id":link["target_id"]})
        for link in connection.execute("SELECT * FROM material_relations WHERE (source_table=? AND source_id=?) OR (target_table=? AND target_id=?) ORDER BY id", (table,identifier,table,identifier)):
            edge = self._render_material_relation(link)
            outgoing = edge["source_table"] == table and edge["source_id"] == identifier
            target_table,target_id = (edge["target_table"],edge["target_id"]) if outgoing else (edge["source_table"],edge["source_id"])
            relation = {"id":edge["id"],"type":edge["relation_type"],"label":edge["relation_type"],"description":edge["description"],"metadata":edge["metadata"],"direction":"outgoing" if outgoing else "incoming","source":"explicit"}
            key = target_table,target_id
            if key not in targets:
                item = self._render(connection,target_table,self._row(connection,target_table,target_id)); item["relationships"] = []
                targets[key] = item; result[target_table].append(item)
            targets[key]["relationships"].append(relation); targets[key]["relationship"] = relation
            result["edges"].append({**edge,"direction":relation["direction"],"label":relation["label"]})
        return result

    def _catalog_revision(self, connection: sqlite3.Connection) -> str:
        payload={table:[(r['id'],r['revision'],r['values_json']) for r in connection.execute(f'SELECT id,revision,values_json FROM {table} ORDER BY id')] for table in TABLES}
        payload['archives']=[tuple(r) for r in connection.execute('SELECT * FROM catalog_field_archive ORDER BY table_key,record_id,source_schema')]
        payload['contexts']=[tuple(r) for r in connection.execute('SELECT character_id,work_id,revision,character_json FROM character_contexts ORDER BY character_id,work_id')]
        payload['relations']=[tuple(r) for r in connection.execute('SELECT source_table,source_id,target_table,target_id,relation_type,revision FROM material_relations ORDER BY id')]
        return hashlib.sha256(_json(payload).encode()).hexdigest()

    def catalog_snapshot(self) -> dict[str,Any]:
        with self.connect() as connection:
            connection.execute('BEGIN')
            return {'revision':self._catalog_revision(connection),**{table:self._all(connection,table) for table in TABLES}}

    def apply_research(self, draft: dict[str,Any], decisions: dict[str,Any], expected_catalog_revision: str, job_id: str) -> dict[str,Any]:
        """User-reviewed metadata only; all material tables and reverse maps commit once."""
        from .character_contract import normalize_character
        if not isinstance(draft,dict) or not isinstance(decisions,dict) or not isinstance(job_id,str) or not job_id:
            raise DataError(422,'入库数据格式错误')
        fingerprint=hashlib.sha256(_json({'draft':draft,'decisions':decisions}).encode()).hexdigest()
        with self.transaction() as connection:
            previous=connection.execute('SELECT * FROM catalog_research_applies WHERE job_id=?',(job_id,)).fetchone()
            if previous:
                if previous['payload_hash']!=fingerprint:raise DataError(409,'此研究任务已经入库，不能用不同内容重复确认')
                return json.loads(previous['result_json'])
            if self._catalog_revision(connection)!=expected_catalog_revision:raise DataError(409,'数据库在研究期间发生变化，请刷新候选并重新确认')
            source=draft.get('work',{})
            if not isinstance(source,dict):raise DataError(422,'缺少作品信息')
            work_id=decisions.get('work_id')
            if work_id:
                before=catalog_schema.project_values('works',self._render(connection,'works',self._row(connection,'works',work_id))['values'])
                def identity(v):return re.sub(r'\s+','',str(v)).casefold()
                if identity(before['作品'])!=identity(source.get('title',source.get('作品',''))) or identity(before['作者'])!=identity(source.get('author',source.get('作者',''))):
                    raise DataError(409,'候选作品的书名或作者与选定作品不一致，请重新选择作品')
            else:
                work_id='W-'+uuid4().hex;before=catalog_schema.project_values('works',{'作品ID':work_id})
            work={**before,'作品ID':work_id}
            mapping={'title':'作品','author':'作者','category':'类别','publication_date':'发布日期','synopsis':'剧情梗概','discussion_heat':'讨论热度','market_scale':'商业规模'}
            for src,dest in mapping.items():
                if src in source:
                    item=source[src];work[dest]=_json(item) if isinstance(item,dict) else str(item or '')
            if not work['作品'].strip() or not work['作者'].strip():raise DataError(422,'书名和作者不能为空')
            work=validate_values('works',work)
            exists=connection.execute('SELECT revision FROM works WHERE id=?',(work_id,)).fetchone()
            if exists:self._update_raw(connection,'works',work_id,work,exists[0])
            else:self._insert_raw(connection,'works',work)
            character_ids={};cliche_ids={}
            for index,item in enumerate(draft.get('characters',[])):
                candidate_id=str(item.get('candidate_id',item.get('id',f'character-{index}')))
                decision=decisions.get('characters',{}).get(candidate_id,{})
                if decision.get('action') not in {'new','reuse'}:raise DataError(422,f'请确认人物 {candidate_id} 的新建或复用决定')
                cid=decision.get('id') if decision['action']=='reuse' else 'CH-'+uuid4().hex
                if decision['action']=='new':
                    def normal_name(v):return re.sub(r'\s+','',str(v)).casefold()
                    names={normal_name(v) for v in [item.get('name',''),*item.get('aliases',[])] if str(v).strip()}
                    for existing_character in connection.execute('SELECT id,work_id,values_json FROM characters'):
                        previous_values=catalog_schema.project_values('characters',json.loads(existing_character['values_json']))
                        linked=existing_character['work_id']==work_id or any(self._association_id(link,'作品ID','关联作品')==work_id for link in association_items(previous_values['关联作品'],'关联作品'))
                        if not linked:continue
                        previous_names={normal_name(v) for v in [previous_values['角色名'],*json.loads(previous_values['别名'])] if str(v).strip()}
                        if names & previous_names:raise DataError(422,f"人物 {item.get('name','')} 已关联当前作品（{existing_character['id']}），请选择复用该人物")
                if decision['action']=='reuse':
                    record=self._row(connection,'characters',cid);values=catalog_schema.project_values('characters',json.loads(record['values_json']))
                else:values=catalog_schema.project_values('characters',{'人物ID':cid})
                canonical=normalize_character({'schema_version':3,'id':cid,'name':item.get('name',''),'aliases':item.get('aliases',[]),'role_tag':item.get('role',item.get('role_tag','')),'temperament':item.get('temperament',''),'visual_design':item.get('visual_design',''),'initial_state':item.get('initial_state',{}),'snapshots':item.get('states',item.get('snapshots',[]))})
                # Stable identity and all per-work changes are saved in work context.
                if decision['action']=='new':
                    values.update({'人物ID':cid,'角色名':canonical['name'],'别名':_json(canonical['aliases']),'角色定位':canonical['role_tag'],'性格基调':canonical['temperament'],'视觉元素设计':canonical['visual_design'],'初始状态':_json(canonical['initial_state']),'阶段状态':_json(canonical['snapshots'])})
                    self._insert_raw(connection,'characters',validate_values('characters',values))
                if decision['action']=='reuse':
                    static_fields={'角色名':'name','角色定位':'role_tag','性格基调':'temperament','视觉元素设计':'visual_design'}
                    merged=dict(values)
                    for field,business in static_fields.items():
                        if canonical[business]:merged[field]=canonical[business]
                    merged['别名']=_json(list(dict.fromkeys([*json.loads(values['别名']),*canonical['aliases']])))
                    if merged!=values:
                        self._write_library_values(connection,'characters',cid,merged)
                        sync=getattr(self,'character_sync',None)
                        if sync is not None:sync(connection,cid,merged)
                    values=merged
                context=connection.execute('SELECT * FROM character_contexts WHERE character_id=? AND work_id=?',(cid,work_id)).fetchone()
                if context:
                    # Web research cannot erase text-derived timelines, change a
                    # source binding, or replace evidence with an empty web draft.
                    preserved=json.loads(context['character_json'])
                    for field,business in {'角色名':'name','角色定位':'role_tag','性格基调':'temperament','视觉元素设计':'visual_design'}.items():
                        if values.get(field):preserved[business]=values[field]
                    preserved['aliases']=list(dict.fromkeys([*preserved.get('aliases',[]),*json.loads(values['别名'])]))
                    if preserved!=json.loads(context['character_json']):connection.execute('UPDATE character_contexts SET character_json=?,revision=revision+1 WHERE character_id=? AND work_id=?',(_json(preserved),cid,work_id))
                else:
                    connection.execute('INSERT INTO character_contexts(character_id,work_id,document_id,extracted_character_id,character_json,revision) VALUES(?,?,NULL,?,?,1)',(cid,work_id,cid,_json(canonical)))
                self._append_catalog_link(connection,'characters',cid,'关联作品',{'作品ID':work_id})
                character_ids[candidate_id]=cid
            for index,item in enumerate(draft.get('cliches',[])):
                candidate_id=str(item.get('candidate_id',item.get('id',f'cliche-{index}')))
                decision=decisions.get('cliches',{}).get(candidate_id,{})
                if decision.get('action') not in {'reuse','new'}:raise DataError(422,f'请确认桥段 {candidate_id} 的新建或复用决定')
                cid=decision.get('id') if decision['action']=='reuse' else 'C-'+uuid4().hex
                if decision['action']=='reuse':self._row(connection,'cliches',cid)
                else:
                    values=catalog_schema.project_values('cliches',{'clichéID':cid,'cliché内容':str(item.get('content','')),'人设塑造':str(item.get('character_trait','')),'剧情机制':str(item.get('plot_mechanism',''))})
                    if not values['cliché内容'].strip():raise DataError(422,'桥段内容不能为空')
                    self._insert_raw(connection,'cliches',validate_values('cliches',values))
                self._append_catalog_link(connection,'cliches',cid,'关联作品',{'作品ID':work_id,'关联内容':str(item.get('description',''))})
                for candidate_character in item.get('character_ids',[]):
                    character_id=character_ids.get(candidate_character)
                    if not character_id:raise DataError(422,f'桥段 {candidate_id} 引用了未确认的人物 {candidate_character}')
                    self._append_catalog_link(connection,'cliches',cid,'关联角色',{'人物ID':character_id,'作品ID':work_id})
                cliche_ids[candidate_id]=cid
            self._rebuild_library_links(connection)
            result={'work_id':work_id,'characters':character_ids,'cliches':cliche_ids,'catalog_revision':self._catalog_revision(connection),'job_id':job_id}
            connection.execute('INSERT INTO catalog_research_applies VALUES(?,?,?)',(job_id,fingerprint,_json(result)))
            return result

    def _append_catalog_link(self, connection: sqlite3.Connection, table: str, identifier: str, field: str, payload: Any) -> None:
        row=self._row(connection,table,identifier);before=json.loads(row['values_json']);after=catalog_schema.project_values(table,before)
        target_id_field=ASSOCIATIONS[field][1];target=self._association_id(payload,target_id_field,field)
        items=association_items(after[field],field)
        context=payload.get('作品ID','') if isinstance(payload,dict) and {table,ASSOCIATIONS[field][0]}=={'characters','cliches'} else None
        if any(self._association_id(item,target_id_field,field)==target and (context is None or (item.get('作品ID','') if isinstance(item,dict) else '')==context) for item in items):return
        after[field]=_json([*items,payload]);self._write_library_values(connection,table,identifier,after);self._sync_modern_views(connection,table,identifier,before,after)
