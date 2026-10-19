"""One durable upload pipeline for TXT, EPUB and PDF.

The GUI observes this job from source upload through chapter parsing and issue
scan. Conversion reuses ConversionStore; texts remain unchanged until reviewed.
All originals and pinned target versions remain in the existing workbench DB.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .database import DataError
from .diagnostics import background_target, logged_operation, record_exception
from .documents import MAX_UPLOAD_BYTES

_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_META = ("id,filename,kind,original_sha256,options_json,status,stage,progress,completed,total,error,"
         "document_id,version_id,conversion_id,attempt,created_at,updated_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _Cancelled(Exception):
    pass


class IngestionStore:
    def __init__(self, data_dir: str | Path, documents, conversions):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        self.documents, self.conversions = documents, conversions
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self._closed = False
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS ing_jobs (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL,
                    original BLOB, original_sha256 TEXT NOT NULL, options_json TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL, progress REAL,
                    completed INTEGER NOT NULL DEFAULT 0, total INTEGER, error TEXT,
                    document_id TEXT, version_id TEXT, conversion_id TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS ing_original_immutable
                BEFORE UPDATE OF original,original_sha256,filename,kind,options_json,conversion_id ON ing_jobs
                BEGIN SELECT RAISE(ABORT, 'ingestion original and target are immutable'); END;
            """)
            connection.execute("UPDATE ing_jobs SET status='interrupted',stage='interrupted',progress=NULL,error=?,updated_at=? WHERE status='running'",
                               ("应用重启导致上传处理中断，请重试", _now()))

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row(connection: sqlite3.Connection, identifier: str) -> sqlite3.Row:
        row = connection.execute(f"SELECT {_META} FROM ing_jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise DataError(404, "上传处理任务不存在")
        return row

    @staticmethod
    def _meta(row: sqlite3.Row) -> dict[str, Any]:
        result = {key: row[key] for key in row.keys() if key != "options_json"}
        result["options"] = json.loads(row["options_json"])
        return result

    def create(self, filename: str, raw: bytes, encoding: str = "auto", title: str = "",
               work_id: str | None = None, *, mode: str = "new", target_document_id: str | None = None,
               base_version_id: str | None = None, conversion_options: dict[str, Any] | None = None,
               book_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._closed:
            raise DataError(503, "上传处理服务正在关闭")
        if not isinstance(filename, str) or not filename or len(filename) > 255 or re.search(r"[\x00-\x1f]", filename):
            raise DataError(422, "文件名无效")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        kind = Path(filename).suffix.lower().lstrip(".")
        if kind not in {"txt", "pdf", "epub"}:
            raise DataError(422, "全文与清洗仅接受 TXT、PDF、EPUB 文件")
        if not isinstance(raw, bytes) or not raw:
            raise DataError(422, "请上传非空原始文件")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise DataError(413, "原文件不能超过 50 MiB")
        if not isinstance(encoding, str) or len(encoding) > 80:
            raise DataError(422, "请选择有效文本编码")
        if not isinstance(title, str) or len(title) > 500 or (work_id is not None and not isinstance(work_id, str)):
            raise DataError(422, "标题及作品 ID 无效")
        if not isinstance(mode, str) or mode not in {"new", "replace", "append"}:
            raise DataError(422, "上传方式请选择新建、更新或补充")
        if mode == "new":
            if target_document_id is not None or base_version_id is not None:
                raise DataError(422, "新建全文不需要目标文档或版本")
        else:
            if not isinstance(target_document_id, str) or not isinstance(base_version_id, str):
                raise DataError(422, "更新或补充需要目标文档及其当前版本")
            target = self.documents.get_document(target_document_id)
            self.documents.get_version(target_document_id, base_version_id)
            if target["latest_version_id"] != base_version_id:
                raise DataError(409, "全文已有新版本，请刷新后重新选择更新或补充")
        if book_metadata:
            from .chapter_storage import validate_metadata
            if mode != "new":
                raise DataError(422, "更新或补充正文时，请在作品信息中修改元数据")
            try:
                book_metadata = validate_metadata(book_metadata)
            except ValueError as exc:
                raise DataError(422, str(exc)) from exc
        if conversion_options is None:
            conversion_options = {}
        if not isinstance(conversion_options, dict) or set(conversion_options) - {"tier", "ocr_mode"}:
            raise DataError(422, "格式转换设置仅支持 tier 和 ocr_mode")
        if kind != "pdf" and conversion_options:
            raise DataError(422, "MinerU 设置仅适用于 PDF 文件")
        options = {"encoding": encoding, "title": title, "work_id": work_id, "mode": mode,
                   "target_document_id": target_document_id, "base_version_id": base_version_id,
                   "conversion_options": conversion_options, "book_metadata": book_metadata or {}}
        conversion_id = None
        if kind in {"pdf", "epub"}:
            conversion = self.conversions.create(filename, raw, {**conversion_options, "title": title, "work_id": work_id,
                                                                 "mode": mode, "target_document_id": target_document_id,
                                                                 "base_version_id": base_version_id})
            conversion_id = conversion["id"]
        identifier, now = uuid.uuid4().hex, _now()
        with self._connection() as connection:
            connection.execute("INSERT INTO ing_jobs(id,filename,kind,original,original_sha256,options_json,status,stage,progress,conversion_id,created_at,updated_at) VALUES (?,?,?,?,?,?,'queued','queued',0,?,?,?)",
                               (identifier, filename, kind, raw if kind == "txt" else None, hashlib.sha256(raw).hexdigest(),
                                _json(options), conversion_id, now, now))
        return self.get(identifier)

    def get(self, identifier: str) -> dict[str, Any]:
        with self._connection() as connection:
            result = self._meta(self._row(connection, identifier))
        if result["status"] == "running" and result["stage"] == "converting" and result["conversion_id"]:
            conversion = self.conversions.get(result["conversion_id"])
            result.update(progress=conversion["progress"], completed=conversion["completed"], total=conversion["total"],
                          conversion_stage=conversion["stage"])
        return result

    def list(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            identifiers = [row["id"] for row in connection.execute("SELECT id FROM ing_jobs ORDER BY created_at DESC,id")]
        return [self.get(identifier) for identifier in identifiers]

    def _update(self, identifier: str, **fields: Any) -> None:
        if self._closed:
            return
        allowed = {"status", "stage", "progress", "completed", "total", "error", "document_id", "version_id"}
        if set(fields) - allowed:
            raise ValueError("invalid ingestion state update")
        fields["updated_at"] = _now()
        with self._connection() as connection:
            connection.execute("UPDATE ing_jobs SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=? AND status='running'",
                               (*fields.values(), identifier))

    def start(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise DataError(503, "上传处理服务正在关闭")
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._row(connection, identifier)
                if row["status"] == "running":
                    return self._meta(row)
                if row["status"] != "queued":
                    raise DataError(409, "此上传任务不能直接启动；失败或中断后请重试")
                connection.execute("UPDATE ing_jobs SET status='running',stage='decoding',progress=NULL,error=NULL,attempt=attempt+1,updated_at=? WHERE id=?",
                                   (_now(), identifier))
            self._cancellations[identifier] = threading.Event()
            thread = threading.Thread(target=background_target(self._run), args=(identifier,), daemon=True, name=f"ingestion-{identifier[:8]}")
            self._threads[identifier] = thread
            thread.start()
        return self.get(identifier)

    def _check_cancelled(self, identifier: str) -> None:
        if self._closed or self._cancellations[identifier].is_set():
            raise _Cancelled()

    def _finish_cancellation(self, identifier: str, meta: dict[str, Any] | None) -> None:
        if self._closed:
            return  # shutdown owns final state; late callbacks cannot write.
        keys = [f"ing:{identifier}"]
        conversion_id = meta.get("conversion_id") if meta else None
        if conversion_id:
            keys.append(f"conv:{conversion_id}")
        committed = next((result for key in keys if (result := self.documents.ingestion_result(key)) is not None), None)
        if committed is not None:
            self._update(identifier, status="completed", stage="completed", progress=1, completed=1, total=1,
                         document_id=committed["document_id"], version_id=committed["version_id"], error=None)
            return
        # A native retry can start after the initial cancellation read. Reap
        # that work as well, so a cancelled upload owns no live conversion.
        if conversion_id:
            conversion = self.conversions.get(conversion_id)
            if conversion["status"] in {"queued", "running"}:
                self.conversions.cancel(conversion_id)
        self._update(identifier, status="cancelled", stage="cancelled", progress=None, error=None)

    @logged_operation("ingestion", "upload_task", task_arg="identifier")
    def _run(self, identifier: str) -> None:
        meta = None
        try:
            meta = self.get(identifier)
            options, key = meta["options"], f"ing:{identifier}"
            self._check_cancelled(identifier)

            def progress(stage: str, completed: int, total: int) -> None:
                self._check_cancelled(identifier)
                self._update(identifier, stage=stage, completed=completed, total=total,
                             progress=completed / total if total else None)

            committed = self.documents.ingestion_result(key)
            if committed is None and meta["conversion_id"]:
                self._update(identifier, stage="converting", progress=None, completed=0, total=None)
                conversion_id = meta["conversion_id"]
                conversion = self.conversions.get(conversion_id)
                if conversion["status"] == "queued":
                    self.conversions.start(conversion_id)
                elif conversion["status"] in {"failed", "cancelled", "interrupted"}:
                    self.conversions.retry(conversion_id)
                while True:
                    self._check_cancelled(identifier)
                    conversion = self.conversions.get(conversion_id)
                    if conversion["status"] == "completed":
                        break
                    if conversion["status"] == "cancelled":
                        raise _Cancelled()
                    if conversion["status"] in {"failed", "interrupted"}:
                        # cancel() sets our event before cancelling conversion.
                        # That can occur after the loop's first check and before
                        # this read; preserve the user's cancellation outcome.
                        self._check_cancelled(identifier)
                        raise DataError(422, conversion["error"] or "格式转换未完成，请重试")
                    self._cancellations[identifier].wait(0.1)
                self._check_cancelled(identifier)
                document = self.conversions.import_to_document(conversion_id, self.documents, progress=progress,
                    normalize_layout=True, book_metadata=options.get("book_metadata") or None)
                committed = {"document_id": document["id"], "version_id": document["imported_version_id"]}
            elif committed is None:
                with self._connection() as connection:
                    row = connection.execute("SELECT original FROM ing_jobs WHERE id=?", (identifier,)).fetchone()
                raw = bytes(row["original"])
                if hashlib.sha256(raw).hexdigest() != meta["original_sha256"]:
                    raise DataError(422, "上传原件校验失败")
                if options["mode"] == "new":
                    self.documents.upload(meta["filename"], raw, options["encoding"], options["title"], options["work_id"],
                                          idempotency_key=key, preserve_text=True, progress=progress, normalize_layout=True,
                                          book_metadata=options.get("book_metadata") or None)
                    committed = self.documents.ingestion_result(key)
                else:
                    writer = self.documents.replace if options["mode"] == "replace" else self.documents.append
                    version = writer(options["target_document_id"], options["base_version_id"], meta["filename"], raw,
                                     options["encoding"], idempotency_key=key, preserve_text=True, progress=progress, normalize_layout=True)
                    committed = {"document_id": options["target_document_id"], "version_id": version["id"]}
            # A completed document commit wins a simultaneous cancellation: its
            # immutable version exists and must remain visible to the user.
            self._update(identifier, status="completed", stage="completed", progress=1, completed=1, total=1,
                         document_id=committed["document_id"], version_id=committed["version_id"], error=None)
        except _Cancelled:
            self._finish_cancellation(identifier, meta)
        except Exception as error:
            if self._closed or self._cancellations[identifier].is_set():
                self._finish_cancellation(identifier, meta)
                return
            record_exception("ingestion", "upload_task", error, task_id=identifier)
            detail = error.detail if isinstance(error, DataError) else f"{type(error).__name__}：{error}"
            if isinstance(detail, dict):
                detail = detail.get("message", _json(detail))
            self._update(identifier, status="failed", stage="failed", progress=None, error=str(detail)[:6000])

    def cancel(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            meta = self.get(identifier)
            if meta["status"] == "queued":
                with self._connection() as connection:
                    connection.execute("UPDATE ing_jobs SET status='cancelled',stage='cancelled',progress=NULL,updated_at=? WHERE id=? AND status='queued'",
                                       (_now(), identifier))
            elif meta["status"] == "running":
                self._cancellations[identifier].set()
            else:
                return meta
            if meta["conversion_id"]:
                conversion = self.conversions.get(meta["conversion_id"])
                if conversion["status"] in {"queued", "running"}:
                    self.conversions.cancel(meta["conversion_id"])
        return self.get(identifier)

    def retry(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise DataError(503, "上传处理服务正在关闭")
            meta = self.get(identifier)
            if meta["status"] not in {"failed", "cancelled", "interrupted"}:
                raise DataError(409, "只有失败、取消或中断的上传可以重试")
            previous = self._threads.get(identifier)
            if previous is not None and previous.is_alive():
                raise DataError(409, "任务仍在停止，请稍后重试")
            with self._connection() as connection:
                connection.execute("UPDATE ing_jobs SET status='queued',stage='queued',progress=0,error=NULL,updated_at=? WHERE id=?",
                                   (_now(), identifier))
            return self.start(identifier)

    def active_threads(self) -> list[threading.Thread]:
        with self._lock:
            return [thread for thread in self._threads.values() if thread.is_alive()]

    def shutdown(self, timeout: float = 2.5) -> None:
        deadline = time.monotonic() + timeout
        with self._lock:
            self._closed = True
            for event in self._cancellations.values():
                event.set()
            threads = list(self._threads.values())
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        # An active version commit may still own SQLite's write lock. Closing
        # the window must not enter the normal thirty-second connection wait;
        # any running row left behind is recovered by the next startup.
        connection = None
        try:
            connection = sqlite3.connect(self.db_path, timeout=max(0, min(0.1, deadline - time.monotonic())))
            connection.execute("UPDATE ing_jobs SET status='interrupted',stage='interrupted',progress=NULL,error=?,updated_at=? WHERE status='running'",
                               ("应用关闭导致上传处理中断，请重试", _now()))
            connection.commit()
        except sqlite3.Error:
            pass
        finally:
            if connection is not None:
                connection.close()
