"""Transactional SQLite material library; no GUI or model dependencies.

CSV columns are text, including unknown values.  Work/cliche associations live
in one relation table; the two legacy relationship columns are rendered views.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__

VERSION = __version__
WORK_COLUMNS = ["作品ID", "作品", "类别", "作者", "发布日期", "版本与原作关系", "用户是否完整体验过", "两句话概括", "剧情补充说明", "热度", "典型桥段（剧透）"]
CHARACTER_COLUMNS = ["人物ID", "作品ID", "作品", "版本", "角色", "角色定位", "开场身份", "开场能力／专长", "后续能力", "性格", "人物补充说明"]
CLICHE_COLUMNS = ["cliché内容", "cliché程度", "出现作品"]
TABLES = {
    "works": {"label": "作品", "id_field": "作品ID", "columns": WORK_COLUMNS},
    "characters": {"label": "人物", "id_field": "人物ID", "columns": CHARACTER_COLUMNS},
    "cliches": {"label": "桥段", "id_field": "cliché内容", "columns": CLICHE_COLUMNS},
}
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
    if table == "cliches":
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
    unknown = set(values) - set(TABLES[table]["columns"])
    if unknown:
        raise DataError(422, f"未知字段：{'、'.join(sorted(unknown))}")
    if any(not isinstance(value, str) for value in values.values()):
        raise DataError(422, "所有 CSV 字段必须为字符串")
    return dict(values) if partial else {column: values.get(column, "") for column in TABLES[table]["columns"]}


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
    return f"{values['作品']}〔{values['版本与原作关系']}〕"


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
            if current > 2:
                raise DataError(409, "数据库版本高于当前应用，请使用更新的应用")
            if current < 1:
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
            if current < 2:
                # Tombstones outlive deletions: stale edits cannot target a new
                # incarnation of the same external ID (the ABA problem).
                connection.execute("CREATE TABLE record_versions (table_key TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0), PRIMARY KEY(table_key,record_id))")
                for table in TABLES:
                    connection.execute(f"INSERT INTO record_versions SELECT ?,id,revision FROM {table}", (table,))
                connection.execute("INSERT INTO schema_migrations VALUES (2, ?)", (datetime.now(timezone.utc).isoformat(),))

    def _stats(self, connection: sqlite3.Connection) -> dict[str, int]:
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (*TABLES, "work_cliches")}

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            connection.execute("BEGIN")
            return self._stats(connection)

    def meta(self) -> dict[str, Any]:
        counts = self.stats()
        return {"tables": [{"key": table, **spec, "count": counts[table]} for table, spec in TABLES.items()], "version": VERSION}

    def _row(self, connection: sqlite3.Connection, table: str, identifier: str) -> sqlite3.Row:
        require_table(table)
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise DataError(404, f"记录不存在：{identifier}")
        return row

    def _render(self, connection: sqlite3.Connection, table: str, row: sqlite3.Row) -> dict[str, Any]:
        values = json.loads(row["values_json"])
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
        return {"id": row["id"], "values": values, "revision": row["revision"]}

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

    def list_records(self, table: str, q: str = "", offset: int = 0, limit: int = 50, work_id: str | None = None, cliche_id: str | None = None) -> dict[str, Any]:
        require_table(table)
        if offset < 0 or limit < 1 or limit > 500:
            raise DataError(422, "分页范围无效（limit 为 1–500）")
        with self.connect() as connection:
            connection.execute("BEGIN")
            items = self._all(connection, table)
            if work_id:
                if table == "characters":
                    items = [item for item in items if item["values"]["作品ID"] == work_id]
                elif table == "cliches":
                    ids = {row[0] for row in connection.execute("SELECT cliche_id FROM work_cliches WHERE work_id = ?", (work_id,))}
                    items = [item for item in items if item["id"] in ids]
                else:
                    items = [item for item in items if item["id"] == work_id]
            if cliche_id:
                ids = {row[0] for row in connection.execute("SELECT work_id FROM work_cliches WHERE cliche_id = ?", (cliche_id,))}
                if table == "works":
                    items = [item for item in items if item["id"] in ids]
                elif table == "characters":
                    items = [item for item in items if item["values"]["作品ID"] in ids]
                else:
                    items = [item for item in items if item["id"] == cliche_id]
            if q:
                needle = q.casefold()
                items = [item for item in items if any(needle in value.casefold() for value in item["values"].values())]
            return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}

    def relations(self, table: str, identifier: str) -> dict[str, list[dict[str, Any]]]:
        require_table(table)
        with self.connect() as connection:
            connection.execute("BEGIN")
            row = self._row(connection, table, identifier)
            if table == "works":
                work_ids = {identifier}
            elif table == "characters":
                work_ids = {row["work_id"]}
            else:
                work_ids = {item[0] for item in connection.execute("SELECT work_id FROM work_cliches WHERE cliche_id = ?", (identifier,))}
            result = {key: [] for key in TABLES}
            for key in ("works", "characters"):
                for item in self._all(connection, key):
                    wid = item["id"] if key == "works" else item["values"]["作品ID"]
                    if wid in work_ids and not (key == table and item["id"] == identifier):
                        result[key].append(item)
            if table != "cliches":
                cids = {item["cliche_id"] for item in connection.execute("SELECT * FROM work_cliches") if item["work_id"] in work_ids}
                result["cliches"] = [item for item in self._all(connection, "cliches") if item["id"] in cids]
            return result

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
            if table == "works":
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
        patch = validate_values(table, values, partial=True)
        with self.transaction() as connection:
            current = self._render(connection, table, self._row(connection, table, identifier))["values"]
            merged = {**current, **patch}
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
            if related and not cascade:
                raise DataError(409, f"存在 {related} 条关联；请确认级联删除")
            count = 1 + (connection.execute("SELECT COUNT(*) FROM characters WHERE work_id = ?", (identifier,)).fetchone()[0] if table == "works" else 0)
            connection.execute(f"DELETE FROM {table} WHERE id = ?", (identifier,))
            self._bump(connection, "cliches", cids)
            self._bump(connection, "works", wids)
            for wid in wids:
                self._normalize_work_layout(connection, wid)
            return {"deleted": count}
