"""Bounded metadata queries and explicit controls for the local task center.

Listing reads existing tables directly and never constructs a lazy domain
service, reads full text, returns prompts, or invokes a provider.
"""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import math

from .database import DataError

KINDS = ("extraction", "embedding_download", "research", "volume_research", "ingestion", "conversion", "repair", "prompt_workbench")
ACTIVE = {"queued", "running", "cancelling", "verifying", "sending", "pausing", "terminating"}
FINISHED = {"approved", "completed", "succeeded", "rolled_back"}
STOPPED = {"paused", "terminated", "failed", "cancelled", "interrupted"}
STATUS_BUCKETS = {"active": ACTIVE, "review": {"prepared", "awaiting_reply", "needs_review"},
                  "paused": {"paused"}, "terminated": {"cancelled", "terminated"},
                  "completed": FINISHED, "failed": {"failed", "interrupted"}}
VALID_STATUSES = ACTIVE | FINISHED | STOPPED | {"prepared", "awaiting_reply", "needs_review", "approving", "unknown"}
_COLUMNS = ("id", "kind", "title", "status", "stage", "progress", "completed", "total", "created_at", "updated_at", "document_id", "version_id", "profile_id", "model", "mode", "operation", "error", "run_id", "chunk_id")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(column, path, default="NULL"):
    return f"COALESCE(json_extract({column},'$.{path}'),{default})"


def _timestamp(expression):
    return f"CASE WHEN typeof({expression}) IN ('real','integer') THEN strftime('%Y-%m-%dT%H:%M:%fZ',{expression},'unixepoch') ELSE {expression} END"


def record_external_task(data_dir, *, identifier, operation, status, stage="", progress=None,
                         title="", error="", document_id=None):
    """Mirror scalar repair-operation metadata without affecting its commit.

    The repair manifest remains authoritative. A missing or busy metadata
    database cannot turn a successful repair into a failed file transaction.
    """
    try:
        if not all(isinstance(value, str) for value in (identifier, operation, status)) or not identifier:
            return False
        path = Path(data_dir) / "workbench.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        ratio = float(progress) if isinstance(progress, (int, float)) and not isinstance(progress, bool) and math.isfinite(progress) else None
        if ratio is not None:
            ratio = max(0, min(1, ratio / 100))
        completed, total = (round(ratio * 100), 100) if ratio is not None else (0, None)
        stamp = _now()
        with contextlib.closing(sqlite3.connect(path, timeout=.1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS task_external_metadata (id TEXT PRIMARY KEY,kind TEXT NOT NULL,operation TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,progress REAL,completed INTEGER NOT NULL,total INTEGER,document_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,error TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS task_external_updated ON task_external_metadata(updated_at DESC,id)")
            db.execute("INSERT INTO task_external_metadata VALUES (?,'repair',?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET operation=excluded.operation,title=excluded.title,status=excluded.status,stage=excluded.stage,progress=excluded.progress,completed=excluded.completed,total=excluded.total,document_id=excluded.document_id,updated_at=excluded.updated_at,error=excluded.error",
                       (identifier[:200], operation[:100], str(title or "修复操作")[:300], status[:50], str(stage)[:100], ratio, completed, total, str(document_id)[:200] if document_id else None, stamp, stamp, str(error or "")[:600]))
            db.execute("DELETE FROM task_external_metadata WHERE kind='repair' AND status NOT IN ('queued','running','cancelling','verifying') AND id NOT IN (SELECT id FROM task_external_metadata WHERE kind='repair' AND status NOT IN ('queued','running','cancelling','verifying') ORDER BY updated_at DESC,id LIMIT 24)")
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


class TaskHub:
    def __init__(self, runtime, *, repair_getter=None):
        self.runtime = runtime
        self.path = Path(runtime.data_dir) / "workbench.sqlite3"
        self.repair_getter = repair_getter
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS task_control_overrides (kind TEXT NOT NULL,id TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(kind,id))")
            db.execute("CREATE INDEX IF NOT EXISTS task_controls_status ON task_control_overrides(status,updated_at)")

    @contextlib.contextmanager
    def _db(self):
        with contextlib.closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=30000")
            with db:
                yield db

    @staticmethod
    def _project(table, kind, **values):
        values = {"id": "j.id", "kind": f"'{kind}'", "title": "j.id", "status": "j.status", "stage": "j.status", "progress": "NULL", "completed": "0", "total": "NULL", "created_at": "''", "updated_at": "''", **values}
        return "SELECT " + ",".join(f"{values.get(column, 'NULL')} AS {column}" for column in _COLUMNS) + f" FROM {table} j"

    def _sources(self, db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        queries = []
        document_title = lambda field: f"COALESCE((SELECT title FROM doc_documents d WHERE d.id={field}),{field})" if "doc_documents" in tables else field
        if "run_jobs" in tables:
            completed = "(SELECT COUNT(*) FROM run_chunks c WHERE c.run_id=j.id AND c.status='completed')"
            total = "j.total"
            next_chunk = "(SELECT c.id FROM run_chunks c WHERE c.run_id=j.id AND c.status!='completed' ORDER BY c.position LIMIT 1)"
            next_stage = "j.stage"
            profile_id = "j.profile_id"
            if "run_stage_chunks" in tables:
                stage_query = "SELECT sc.{field} FROM run_stage_chunks sc JOIN run_chunks c ON c.run_id=sc.run_id AND c.id=sc.chunk_id WHERE sc.run_id=j.id AND sc.status!='completed' ORDER BY sc.stage_order,c.position LIMIT 1"
                next_stage = f"COALESCE(({stage_query.format(field='stage')}),j.stage)"
                next_chunk = f"COALESCE(({stage_query.format(field='chunk_id')}),{next_chunk})"
                stage_count = "(SELECT COUNT(*) FROM run_stage_chunks sc WHERE sc.run_id=j.id)"
                completed = f"CASE WHEN {stage_count}>0 THEN (SELECT COUNT(*) FROM run_stage_chunks sc WHERE sc.run_id=j.id AND sc.status='completed') ELSE {completed} END"
                total = f"CASE WHEN {stage_count}>0 THEN {stage_count} ELSE j.total END"
                profile_id = f"COALESCE(json_extract(j.snapshots_json,'$.stage_models.'||({next_stage})||'.id'),j.profile_id)"
            queries.append(self._project("run_jobs", "extraction", title=document_title("j.document_id"), stage=next_stage, completed=completed, total=total,
                progress=f"CASE WHEN ({total})>0 THEN CAST(({completed}) AS REAL)/({total}) ELSE 0 END", created_at="j.created_at", updated_at="j.updated_at", document_id="j.document_id", version_id="j.version_id", profile_id=profile_id,
                model=f"COALESCE(json_extract(j.snapshots_json,'$.stage_models.'||({next_stage})||'.model'),json_extract(j.snapshots_json,'$.model.model'))",
                mode="COALESCE(json_extract(j.snapshots_json,'$.interaction_mode'),CASE WHEN json_extract(j.snapshots_json,'$.model.provider')='manual' THEN 'semi' ELSE 'automatic' END)", error="substr(j.error,1,600)", run_id="j.id", chunk_id=next_chunk, operation="'extraction'"))
        if "embedding_downloads" in tables:
            raw = "j.data_json"
            queries.append(self._project("embedding_downloads", "embedding_download", title="substr(" + _json(raw, "model", "''") + ",1,500)", status=_json(raw,"status","'unknown'"), stage=_json(raw,"stage","''"), completed=_json(raw,"completed","0"), total=_json(raw,"total"),
                progress=f"CASE WHEN {_json(raw,'total','0')}>0 THEN CAST({_json(raw,'completed','0')} AS REAL)/{_json(raw,'total')} ELSE NULL END", created_at=_json(raw,"created_at","''"), updated_at=_json(raw,"updated_at","''"), profile_id=_json(raw,"profile_id"), model=_json(raw,"model"), error=f"substr({_json(raw,'error')},1,600)"))
        if "research_jobs" in tables:
            raw = "j.payload_json"
            queries.append(self._project("research_jobs", "research", title="substr(" + _json(raw, "title", "''") + ",1,500)", stage="j.stage", completed=_json(raw,"progress.completed","0"), total=_json(raw,"progress.total"), created_at="j.created_at", updated_at="j.updated_at", profile_id=_json(raw,"profiles.model.id"), model=_json(raw,"profiles.model.model"), mode=_json(raw,"mode","'automatic'"), operation="'catalog'", error=f"substr({_json(raw,'error')},1,600)"))
        if "volume_research_jobs" in tables:
            raw = "j.payload_json"
            queries.append(self._project("volume_research_jobs", "volume_research", title="substr(" + _json(raw, "title", "''") + ",1,500)", stage=_json(raw,"stage","j.status"), completed=_json(raw,"progress.completed","0"), total=_json(raw,"progress.total"), created_at="j.updated_at", updated_at="j.updated_at", document_id="j.document_id", version_id="j.version_id", profile_id=_json(raw,"profile.id"), model=_json(raw,"profile.model"), mode="'automatic'", operation="'volumes'", error=f"substr({_json(raw,'error')},1,600)"))
        for table, kind in (("ing_jobs", "ingestion"), ("conv_jobs", "conversion")):
            if table not in tables:
                continue
            queries.append(self._project(table, kind, title="substr(j.filename,1,500)", stage="j.stage", progress="j.progress", completed="j.completed", total="j.total", created_at="j.created_at", updated_at="j.updated_at", document_id="j.document_id" if table == "ing_jobs" else "j.imported_document_id", version_id="j.version_id" if table == "ing_jobs" else "NULL", error="substr(j.error,1,600)"))
        if "repair_generations" in tables:
            columns = {row[1] for row in db.execute("PRAGMA table_info(repair_generations)")}
            raw = "j.summary_json" if "summary_json" in columns else "j.payload_json"
            queries.append(self._project("repair_generations", "repair", title="'快捷修复'", stage=_json(raw,"stage","j.status"), completed=_json(raw,"progress.completed","0"), total=_json(raw,"progress.total"), created_at=_timestamp("j.updated_at"), updated_at=_timestamp("j.updated_at"), profile_id=_json("j.payload_json","profile.id"), model=_json("j.payload_json","profile.model"), mode="'automatic'", operation="'repair'", error=f"substr({_json(raw,'error')},1,600)"))
        if "prompt_preparations" in tables:
            columns = {row[1] for row in db.execute("PRAGMA table_info(prompt_preparations)")}
            raw = "j.summary_json" if "summary_json" in columns else "j.payload_json"
            queries.append(self._project("prompt_preparations", "prompt_workbench", title=f"substr({_json(raw,'title','j.operation')},1,500)", stage=_json(raw,"stage","j.status"), completed=_json(raw,"progress.completed","0"), total=_json(raw,"progress.total"), created_at=_timestamp("j.created_at"), updated_at=_timestamp("j.updated_at"), document_id=_json(raw,"document_id"), version_id=_json(raw,"version_id"), profile_id=_json(raw,"profile_id"), model=_json(raw,"model"), mode=_json(raw,"mode"), operation="j.operation", error=f"substr({_json(raw,'error')},1,600)", run_id=_json(raw,"run_id"), chunk_id=_json(raw,"chunk_id")))
        if "task_external_metadata" in tables:
            queries.append(self._project("task_external_metadata", "repair", title="j.title", stage="j.stage", progress="j.progress", completed="j.completed", total="j.total", created_at="j.created_at", updated_at="j.updated_at", document_id="j.document_id", operation="j.operation", error="j.error"))
        return queries

    def _query(self, db):
        queries = self._sources(db)
        if not queries:
            return "SELECT " + ",".join(f"NULL AS {column}" for column in _COLUMNS) + ",NULL AS source_status WHERE 0"
        active = ",".join(f"'{state}'" for state in sorted(ACTIVE))
        finished = ",".join(f"'{state}'" for state in sorted(FINISHED))
        columns = ",".join("s." + column for column in _COLUMNS if column != "status")
        return ("SELECT " + columns + ",s.status AS source_status,CASE WHEN o.status IS NULL OR s.status IN (" + finished + ") THEN s.status "
                "WHEN s.status IN (" + active + ") THEN CASE WHEN o.status='terminated' THEN 'terminating' ELSE 'pausing' END ELSE o.status END AS status "
                "FROM (" + " UNION ALL ".join(queries) + ") s LEFT JOIN task_control_overrides o ON o.kind=s.kind AND o.id=s.id")

    @staticmethod
    def _linked_preparation(db, task):
        if task.get("kind") != "extraction" or not task.get("chunk_id"):
            return None
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompt_preparations'").fetchone():
            return None
        row = db.execute("SELECT p.id,p.status,COALESCE(json_extract(p.payload_json,'$.in_flight'),0) AS in_flight,"
                         "json_extract(p.payload_json,'$.profile_id') AS profile_id,json_extract(p.payload_json,'$.profile.model') AS model,json_extract(p.payload_json,'$.mode') AS mode "
                         "FROM prompt_preparations p JOIN run_jobs r ON r.id=? WHERE p.operation='extraction' AND p.status!='completed' "
                         "AND json_extract(p.payload_json,'$.binding.run_id')=r.id "
                         "AND json_extract(p.payload_json,'$.binding.chunk_id')=? "
                         "AND json_extract(p.payload_json,'$.binding.stage')=? "
                         "AND json_extract(p.payload_json,'$.binding.revision')=r.revision "
                         "AND json_extract(p.payload_json,'$.binding.source_version_id')=r.version_id "
                         "ORDER BY (json_extract(p.payload_json,'$.call_id') IS NOT NULL) DESC,p.updated_at DESC,p.id LIMIT 1",
                         (task["id"], task["chunk_id"], task["stage"])).fetchone()
        return dict(row) if row else None

    def _enrich(self, db, row):
        item = self._public(row)
        linked = self._linked_preparation(db, item)
        if linked:
            item["navigation"].update(prepared_id=linked["id"], operation="extraction")
            if linked.get("profile_id"):
                item["navigation"]["profile_id"] = linked["profile_id"]
                item["profile_id"] = linked["profile_id"]
                item["model"] = linked["model"]
            item["preparation_status"] = linked["status"]
            item["mode"] = linked["mode"]
            if linked["mode"] == "semi":
                item["model"] = "网页模型"
            item["in_flight"] = bool(linked["in_flight"])
            if linked["in_flight"]:
                item["capabilities"]["resume"] = False
                if linked["status"] == "sending" and item["status"] not in {"terminated", "terminating"}:
                    item["capabilities"]["pause"] = True
        return item

    @staticmethod
    def _public(row):
        item = dict(row)
        status, kind = item["status"], item["kind"]
        done = status in FINISHED
        if item.get("progress") is None and item.get("total"):
            item["progress"] = (item.get("completed") or 0) / item["total"]
        if item.get("progress") is not None:
            item["progress"] = max(0, min(1, item["progress"]))
        stop_allowed = not done and status not in {"terminated", "terminating", "approving"}
        if kind == "extraction":
            capabilities = {"pause": status in {"queued", "running", "cancelling"}, "resume": status in STOPPED | {"queued"}, "terminate": stop_allowed and status not in {"needs_review"}}
        elif kind == "prompt_workbench":
            capabilities = {"pause": status in {"prepared", "sending", "needs_review"}, "resume": status in STOPPED, "terminate": stop_allowed}
        else:
            capabilities = {"pause": stop_allowed and status not in STOPPED, "resume": status in STOPPED, "terminate": stop_allowed}
        script_repair = kind == "repair" and str(item.get("operation", "")).startswith("repair_")
        if script_repair:
            capabilities = {"pause": False, "resume": False, "terminate": False}
        section = {"extraction": "jobs", "embedding_download": "models", "research": "library", "volume_research": "documents", "ingestion": "documents", "conversion": "documents", "repair": "repair", "prompt_workbench": "documents"}[kind]
        if kind == "prompt_workbench":
            section = {"extraction": "jobs", "catalog": "library", "repair": "repair", "summarize": "reference", "translate": "reference", "volumes": "documents"}.get(item.get("operation"), "documents")
        action = "open_prompt" if kind in {"extraction", "research", "volume_research", "repair", "prompt_workbench"} and not done else "open"
        if kind == "extraction" and item.get("mode") == "manual":
            action = "open"
        if script_repair:
            action = "open"
        if kind in {"ingestion", "conversion", "embedding_download"} and status in STOPPED:
            action = "retry"
        navigation = {"section": section, "action": action, "id": item["id"], **{field: item[field] for field in ("document_id", "version_id", "profile_id", "run_id", "chunk_id", "operation", "mode", "stage") if item.get(field)}}
        if kind == "prompt_workbench":
            navigation["prepared_id"] = item["id"]
        item["capabilities"], item["navigation"] = capabilities, navigation
        return item

    def list(self, *, q="", kind="", status="", offset=0, limit=30):
        if kind and kind not in KINDS:
            raise DataError(422, "任务类型无效")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100 or not isinstance(q, str) or len(q) > 500:
            raise DataError(422, "任务查询参数无效")
        if status and status not in STATUS_BUCKETS and status not in VALID_STATUSES:
            raise DataError(422, "任务状态无效")
        where, args = [], []
        if q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(title LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            args += [f"%{escaped}%"] * 2
        base_where, base_args = list(where), list(args)
        if kind:
            where.append("kind=?"); args.append(kind)
        count_clause = " WHERE " + " AND ".join(where) if where else ""
        count_args = list(args)
        if status:
            states = sorted(STATUS_BUCKETS.get(status, {status}))
            where.append("status IN (" + ",".join("?" for _ in states) + ")"); args.extend(states)
        clause = " WHERE " + " AND ".join(where) if where else ""
        kinds_clause = " WHERE " + " AND ".join(base_where) if base_where else ""
        with self._db() as db:
            cte = "WITH tasks AS (" + self._query(db) + ") "
            counts = {row["status"]: row["n"] for row in db.execute(cte + "SELECT status,COUNT(*) n FROM tasks" + count_clause + " GROUP BY status", count_args)}
            kinds = {row["kind"]: row["n"] for row in db.execute(cte + "SELECT kind,COUNT(*) n FROM tasks" + kinds_clause + " GROUP BY kind", base_args)}
            total = db.execute(cte + "SELECT COUNT(*) FROM tasks" + clause, args).fetchone()[0]
            rows = db.execute(cte + "SELECT * FROM tasks" + clause + " ORDER BY updated_at DESC,kind,id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            items = [self._enrich(db, row) for row in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit, "counts": counts, "kinds": kinds}

    def get(self, kind, identifier):
        if kind not in KINDS:
            raise DataError(422, "任务类型无效")
        with self._db() as db:
            row = db.execute("WITH tasks AS (" + self._query(db) + ") SELECT * FROM tasks WHERE kind=? AND id=?", (kind, identifier)).fetchone()
            if row is None:
                raise DataError(404, "任务不存在")
            return self._enrich(db, row)

    def _override(self, kind, identifier, status):
        with self._db() as db:
            if status is None:
                db.execute("DELETE FROM task_control_overrides WHERE kind=? AND id=?", (kind, identifier))
            else:
                db.execute("INSERT INTO task_control_overrides VALUES (?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at", (kind, identifier, status, _now()))

    def control(self, kind, identifier, action):
        if action not in {"pause", "resume", "terminate"}:
            raise DataError(422, "任务操作无效")
        task = self.get(kind, identifier)
        if not task["capabilities"].get(action):
            raise DataError(409, "当前任务状态不支持此操作")
        if kind == "prompt_workbench":
            from .prompt_workbench import get_prompt_workbench
            get_prompt_workbench(self.runtime.studio, self.repair_getter).control_preparation(identifier, action)
        elif kind == "extraction":
            runner = self.runtime.studio.runner
            prepared_id = task["navigation"].get("prepared_id")
            workbench = None
            if prepared_id:
                from .prompt_workbench import get_prompt_workbench
                workbench = get_prompt_workbench(self.runtime.studio, self.repair_getter)
            if action == "resume":
                if workbench:
                    workbench.control_preparation(prepared_id, "resume")
                else:
                    runner.reopen(identifier)
            else:
                if workbench:
                    workbench.control_preparation(prepared_id, action)
                getattr(runner, action)(identifier)
        elif action == "resume":
            if kind in {"embedding_download", "ingestion", "conversion"}:
                # Fixed local processing reuses its original task and pinned
                # source. It has no editable language-model prompt to approve.
                studio = self.runtime.studio
                service = {"embedding_download": "embedding_downloads", "ingestion": "ingestions", "conversion": "conversions"}[kind]
                getattr(studio, service).retry(identifier)
                self._override(kind, identifier, None)
                return {"task": self.get(kind, identifier)}
            # Model work resumes at prompt review rather than starting an
            # implicit request against the prior model or source.
            self._override(kind, identifier, None)
            result = self.get(kind, identifier)
            result["navigation"]["action"] = "open_prompt"
            return {"task": result, "action": result["navigation"]}
        else:
            status = "paused" if action == "pause" else "terminated"
            studio = self.runtime.studio
            if kind == "research":
                studio.research.cancel(identifier, pause=True)
            elif kind == "embedding_download":
                studio.embedding_downloads.cancel(identifier)
            elif kind == "volume_research":
                studio.volume_research.cancel(identifier)
            elif kind == "ingestion":
                studio.ingestions.cancel(identifier)
            elif kind == "conversion":
                studio.conversions.cancel(identifier)
            elif kind == "repair":
                if self.repair_getter is None:
                    raise DataError(409, "修复服务尚未配置")
                self.repair_getter().cancel_generation(identifier)
            self._override(kind, identifier, status)
        result = self.get(kind, identifier)
        return {"task": result, **({"action": result["navigation"]} if action == "resume" else {})}
