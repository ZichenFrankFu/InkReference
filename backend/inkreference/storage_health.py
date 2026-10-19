"""Metadata-only storage inspection and explicit, non-destructive maintenance."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Callable, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from .database import DataError
from .diagnostics import record_event, record_exception
from .projects import DATABASES
from .storage_utils import INDEX_PLAN_VERSION, eligible_indexes, ensure_growth_indexes


def _namespace(name: str) -> str | None:
    return {"inkreference.sqlite3": "catalog", "workbench.sqlite3": "workbench"}.get(name)


def _size(path: Path) -> int:
    # WAL files can disappear when the last concurrent connection closes.
    try:
        return path.stat().st_size
    except OSError:
        return 0


class StorageHealth:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def inspect(self) -> dict:
        databases = []
        for name in DATABASES:
            path = self.data_dir / name
            if not path.is_file():
                continue
            item = {"name": name, "bytes": _size(path), "wal_bytes": _size(path.with_name(name + "-wal"))}
            try:
                # Read-only open cannot create a database or checkpoint a WAL.
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
                    db.execute("PRAGMA query_only=ON")
                    item.update({key: db.execute("PRAGMA " + pragma).fetchone()[0] for key, pragma in (("page_size", "page_size"), ("pages", "page_count"), ("free_pages", "freelist_count"), ("journal_mode", "journal_mode"))})
                    item["reusable_bytes"] = item["page_size"] * item["free_pages"]
                    item["tables"] = db.execute("SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0]
                    present = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='index'")}
                    namespace = _namespace(name)
                    item["missing_indexes"] = [spec.name for spec in eligible_indexes(db, namespace) if spec.name not in present] if namespace else []
                    # sqlite_stat1 is optimizer metadata, not a COUNT(*) over
                    # growing business rows. No estimates exist before ANALYZE.
                    has_stats = db.execute("SELECT 1 FROM sqlite_schema WHERE name='sqlite_stat1'").fetchone()
                    estimates = {}
                    if has_stats:
                        for table, stat in db.execute("SELECT tbl,stat FROM sqlite_stat1"):
                            if stat and stat.split()[0].isdigit():
                                estimates[table] = max(estimates.get(table, 0), int(stat.split()[0]))
                    item["row_estimates"] = estimates
                    item["status"] = "ready"
            except sqlite3.Error as error:
                item["status"] = "unavailable"
                item["diagnostic_id"] = record_exception("storage", "inspect", error, data_dir=self.data_dir, stage=name, status=(getattr(error, "sqlite_errorname", None) or "SQLITE_ERROR").lower())
            databases.append(item)
        try:
            free_bytes = shutil.disk_usage(self.data_dir if self.data_dir.exists() else self.data_dir.parent).free
        except OSError:
            free_bytes = None
        return {"databases": databases, "database_bytes": sum(item["bytes"] for item in databases),
                "wal_bytes": sum(item["wal_bytes"] for item in databases), "disk_free_bytes": free_bytes,
                "index_plan_version": INDEX_PLAN_VERSION}

    def optimize(self, database: str = "all") -> dict:
        if database != "all" and database not in DATABASES:
            raise DataError(422, "请选择有效的应用数据库")
        results = []
        for name in DATABASES if database == "all" else (database,):
            path = self.data_dir / name
            if not path.is_file():
                continue
            start = time.monotonic()
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.5)) as db:
                    db.execute("PRAGMA busy_timeout=500")
                    # Explicit maintenance is bounded. A busy writer or very
                    # large first index build can be retried without data loss.
                    db.set_progress_handler(lambda: int(time.monotonic() - start > 2), 10_000)
                    namespace = _namespace(name)
                    with db:
                        added = ensure_growth_indexes(db, namespace) if namespace else []
                    db.execute("PRAGMA analysis_limit=400")
                    db.execute("PRAGMA optimize=0x10002")
                    db.set_progress_handler(None, 0)
                    checkpoint = db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                    results.append({"name": name, "status": "ready", "added_indexes": added,
                                    "checkpoint": {"busy": checkpoint[0], "log_pages": checkpoint[1], "checkpointed_pages": checkpoint[2]},
                                    "duration_ms": round((time.monotonic() - start) * 1000)})
            except sqlite3.Error as error:
                item = {"name": name, "status": "deferred", "duration_ms": round((time.monotonic() - start) * 1000)}
                if (getattr(error, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_INTERRUPT}:
                    record_event("storage", "optimize", data_dir=self.data_dir, level="WARNING", status="deferred", stage=name)
                else:
                    item["status"] = "error"
                    item["diagnostic_id"] = record_exception("storage", "optimize", error, data_dir=self.data_dir, stage=name, status=(getattr(error, "sqlite_errorname", None) or "SQLITE_ERROR").lower())
                results.append(item)
        return {"items": results, "destructive": False}


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    database: Literal["all", "inkreference.sqlite3", "workbench.sqlite3", "skills.sqlite3", "workflow_definitions.sqlite3", "checkpoints.sqlite3"] = "all"


def create_storage_router(getter: Callable[[], StorageHealth], before_optimize: Callable[[], None] | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/studio/storage", tags=["storage"])

    @router.get("")
    def inspect():
        return getter().inspect()

    @router.post("/optimize")
    def optimize(body: MaintenanceRequest = MaintenanceRequest()):
        if before_optimize is not None:
            before_optimize()
        return getter().optimize(body.database)

    return router
