"""Persistent EPUB/PDF -> Markdown -> plain-text conversion jobs.

EPUB uses the local safe spine reader and markdownify (MIT). Plain text is
rendered from markdown-it-py tokens (MIT), never by deleting Markdown-looking
prose with broad regular expressions. PDF uses the optional official MinerU 4
Python SDK in a separate, cancellable trusted Python process. No OCR model or
MinerU installation is required by the base application.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .database import DataError
from .documents import MAX_UPLOAD_BYTES, _BookHTML, epub_spine_sections

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_META_COLUMNS = ("id,filename,kind,original_sha256,options_json,status,stage,progress,completed,total,error,"
                 "imported_document_id,attempt,created_at,updated_at,length(original) AS original_bytes,"
                 "markdown IS NOT NULL AS has_markdown,plain_text IS NOT NULL AS has_text,"
                 "coalesce(length(markdown),0) AS markdown_characters,coalesce(length(plain_text),0) AS text_characters")
_WORKER = r'''
import importlib.metadata
import sys
from pathlib import Path
try:
    version = importlib.metadata.version("mineru")
    if version.split(".", 1)[0] != "4":
        raise RuntimeError("MinerU 4.x is required; install mineru>=4,<5 in this Python environment")
    from mineru.parser import parse
    result = parse(Path(sys.argv[1]), tier=sys.argv[3], ocr_mode=sys.argv[4],
                   page_range="", image_analysis=False)
    # Full rendering includes headers/footers. Short fiction lines can be
    # classified as headers; dropping those blocks would silently lose prose.
    markdown = result.markdown(add_markers=True)
    if not isinstance(markdown, str) or not markdown.strip():
        raise RuntimeError("MinerU returned no Markdown; scanned PDF may require standard/ocr and downloaded models")
    Path(sys.argv[2]).write_text(markdown, encoding="utf-8", errors="strict")
except Exception as error:
    print(type(error).__name__ + ": " + str(error), file=sys.stderr)
    sys.exit(1)
'''


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def markdown_to_text(markdown: str) -> str:
    """Render parser tokens, preserving literal code, Unicode and table text."""
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise DataError(503, "缺少 markdown-it-py，请重新运行依赖安装脚本") from error
    parser = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")
    parts: list[str] = []

    def line() -> None:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")

    def render(tokens) -> None:
        for token in tokens:
            kind = token.type
            if kind == "inline":
                render(token.children or [])
            elif kind in {"text", "code_inline"}:
                parts.append(token.content)
            elif kind in {"fence", "code_block"}:
                line()
                parts.append(token.content)
                line()
            elif kind in {"softbreak", "hardbreak"}:
                parts.append("\n")
            elif kind == "image":
                # Image alt/caption text is meaningful source content.
                parts.append(token.content)
            elif kind in {"html_inline", "html_block"}:
                html = _BookHTML()
                html.feed(token.content)
                html.close()
                parts.append("".join(html.parts))
            elif kind in {"td_close", "th_close"}:
                parts.append("\t")
            elif kind in {"paragraph_close", "heading_close", "list_item_close", "tr_close",
                          "blockquote_close", "table_close", "hr"}:
                line()
            elif token.children:
                render(token.children)
            elif token.content:
                parts.append(token.content)
    render(parser.parse(markdown))
    return "".join(parts).strip("\r\n")


def _options(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"tier", "ocr_mode", "title", "work_id", "mode", "target_document_id", "base_version_id"}:
        raise DataError(422, "转换选项仅支持 tier、ocr_mode、title、work_id、mode、target_document_id、base_version_id")
    result = {"tier": "flash", "ocr_mode": "txt", "title": "", "work_id": None, **value}
    if (not isinstance(result["tier"], str) or not isinstance(result["ocr_mode"], str)
            or result["tier"] not in {"flash", "standard"} or result["ocr_mode"] not in {"txt", "ocr"}):
        raise DataError(422, "tier 请选择 flash/standard，ocr_mode 请选择 txt/ocr")
    if result["ocr_mode"] == "ocr":
        if "tier" not in value:
            result["tier"] = "standard"
        elif result["tier"] != "standard":
            raise DataError(422, "扫描件 OCR 请使用 standard 层级，并先准备 MinerU 模型")
    if not isinstance(result["title"], str) or len(result["title"]) > 500:
        raise DataError(422, "title 必须是不超过 500 字的字符串")
    if result["work_id"] is not None and (not isinstance(result["work_id"], str) or len(result["work_id"]) > 500):
        raise DataError(422, "work_id 必须为作品 ID 字符串")
    if result.get("mode", "new") not in {"new", "replace", "append"}:
        raise DataError(422, "上传方式请选择 new、replace 或 append")
    for key in ("target_document_id", "base_version_id"):
        if result.get(key) is not None and (not isinstance(result[key], str) or not 1 <= len(result[key]) <= 200):
            raise DataError(422, "目标文档和版本 ID 无效")
    if result.get("mode", "new") != "new" and (not result.get("target_document_id") or not result.get("base_version_id")):
        raise DataError(422, "更新或补充需要目标文档及其当前版本")
    if result.get("mode", "new") == "new" and (result.get("target_document_id") or result.get("base_version_id")):
        raise DataError(422, "新建全文不能同时指定更新目标")
    return result


class _Cancelled(Exception):
    pass


class ConversionStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._closed = False
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS conv_jobs (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL,
                    original BLOB NOT NULL, original_sha256 TEXT NOT NULL,
                    options_json TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                    progress REAL, completed INTEGER NOT NULL DEFAULT 0, total INTEGER,
                    error TEXT, markdown TEXT, plain_text TEXT, imported_document_id TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conv_imports (
                    job_id TEXT PRIMARY KEY REFERENCES conv_jobs(id),
                    target_json TEXT NOT NULL, version_id TEXT
                );
                CREATE TABLE IF NOT EXISTS conv_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS conv_original_immutable
                BEFORE UPDATE OF original,original_sha256,filename,kind ON conv_jobs
                BEGIN SELECT RAISE(ABORT, 'conversion original is immutable'); END;
            """)
            interrupted = list(connection.execute("SELECT id,attempt FROM conv_jobs WHERE status='running'"))
            for row in interrupted:
                message = "应用重启导致任务中断，请点击重试"
                connection.execute("INSERT INTO conv_errors(job_id,attempt,message,created_at) VALUES (?,?,?,?)",
                                   (row["id"], row["attempt"], message, _now()))
            connection.execute("UPDATE conv_jobs SET status='interrupted',stage='interrupted',progress=NULL,error=?,updated_at=? WHERE status='running'",
                               ("应用重启导致任务中断，请点击重试", _now()))

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
        row = connection.execute("SELECT * FROM conv_jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise DataError(404, "转换任务不存在")
        return row

    @staticmethod
    def _meta(row: sqlite3.Row) -> dict[str, Any]:
        keys = ("id", "filename", "kind", "original_sha256", "status", "stage", "progress", "completed",
                "total", "error", "imported_document_id", "attempt", "created_at", "updated_at")
        measured = "original_bytes" in row.keys()
        return {key: row[key] for key in keys} | {
            "options": json.loads(row["options_json"]),
            "original_bytes": row["original_bytes"] if measured else len(row["original"]),
            "outputs": {"markdown": bool(row["has_markdown"]) if measured else row["markdown"] is not None,
                        "text": bool(row["has_text"]) if measured else row["plain_text"] is not None},
            "markdown_characters": row["markdown_characters"] if measured else len(row["markdown"] or ""),
            "text_characters": row["text_characters"] if measured else len(row["plain_text"] or ""),
        }

    def create(self, filename: str, raw: bytes, options: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(filename, str) or not filename or len(filename) > 255 or any(ord(ch) < 32 for ch in filename):
            raise DataError(422, "文件名无效")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        kind = Path(filename).suffix.lower().lstrip(".")
        if kind not in {"epub", "pdf"}:
            raise DataError(422, "转换任务仅接受 EPUB 和 PDF")
        if not isinstance(raw, bytes) or not raw:
            raise DataError(422, "请上传非空原文件")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise DataError(413, "原文件不能超过 50 MiB")
        if kind == "pdf" and b"%PDF-" not in raw[:1024]:
            raise DataError(422, "文件缺少有效的 PDF 文件头")
        selected = _options(options)
        identifier = uuid.uuid4().hex
        now = _now()
        with self._connection() as connection:
            connection.execute("INSERT INTO conv_jobs(id,filename,kind,original,original_sha256,options_json,status,stage,progress,created_at,updated_at) VALUES (?,?,?,?,?,?,'queued','queued',0,?,?)",
                               (identifier, filename, kind, raw, hashlib.sha256(raw).hexdigest(), _json(selected), now, now))
        return self.get(identifier)

    def get(self, identifier: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(f"SELECT {_META_COLUMNS} FROM conv_jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise DataError(404, "转换任务不存在")
            meta = self._meta(row)
            meta["errors"] = [dict(row) for row in connection.execute(
                "SELECT attempt,message,created_at FROM conv_errors WHERE job_id=? ORDER BY id", (identifier,))]
            return meta

    def list(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            return [self._meta(row) for row in connection.execute(f"SELECT {_META_COLUMNS} FROM conv_jobs ORDER BY created_at DESC,id")]

    def start(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise DataError(503, "转换服务正在关闭，请重启应用后重试")
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._row(connection, identifier)
                if row["status"] == "running":
                    return self._meta(row)
                if row["status"] != "queued":
                    raise DataError(409, "此任务不能直接启动；失败、中断或取消后请重试")
                connection.execute("UPDATE conv_jobs SET status='running',stage='preparing',progress=NULL,attempt=attempt+1,updated_at=? WHERE id=?", (_now(), identifier))
            self._cancellations[identifier] = threading.Event()
            thread = threading.Thread(target=self._run, args=(identifier,), daemon=True, name=f"conversion-{identifier[:8]}")
            self._threads[identifier] = thread
            thread.start()
        return self.get(identifier)

    def _check_cancelled(self, identifier: str) -> None:
        if self._cancellations[identifier].is_set():
            raise _Cancelled()

    def _update(self, identifier: str, **fields: Any) -> None:
        # All column names below are internal constants, never uploaded input.
        allowed = {"status", "stage", "progress", "completed", "total", "error", "markdown", "plain_text"}
        if set(fields) - allowed:
            raise ValueError("invalid conversion state update")
        if self._closed:
            return
        fields["updated_at"] = _now()
        with self._connection() as connection:
            connection.execute("UPDATE conv_jobs SET " + ",".join(f"{key}=?" for key in fields)
                               + " WHERE id=? AND status='running'", (*fields.values(), identifier))

    def _run(self, identifier: str) -> None:
        try:
            with self._connection() as connection:
                row = dict(self._row(connection, identifier))
            self._check_cancelled(identifier)
            raw = bytes(row["original"])
            if hashlib.sha256(raw).hexdigest() != row["original_sha256"]:
                raise DataError(422, "原文件哈希校验失败")
            options = json.loads(row["options_json"])
            if row["kind"] == "epub":
                from markdownify import markdownify
                sections = epub_spine_sections(raw)
                total = len(sections) + 1
                self._update(identifier, stage="epub_to_markdown", total=total, completed=0, progress=0)
                markdown_sections = []
                for index, section in enumerate(sections):
                    self._check_cancelled(identifier)
                    markdown_sections.append(markdownify(section["html"], heading_style="ATX"))
                    self._update(identifier, completed=index + 1, progress=(index + 1) / total)
                markdown = "\n\n".join(markdown_sections)
            else:
                self._update(identifier, stage="pdf_mineru", progress=None, total=None, completed=0)
                markdown = self._pdf_markdown(identifier, raw, options)
            self._check_cancelled(identifier)
            if not isinstance(markdown, str) or not markdown.strip():
                raise DataError(422, "转换未生成 Markdown，任务没有完成")
            self._update(identifier, stage="markdown_to_text", markdown=markdown)
            plain = markdown_to_text(markdown)
            if not plain.strip():
                raise DataError(422, "没有提取到正文；扫描 PDF 请选 standard/ocr 并先准备模型")
            self._check_cancelled(identifier)
            current = self.get(identifier)
            total = current["total"] or 1
            self._update(identifier, status="completed", stage="completed", progress=1, completed=total,
                         total=total, plain_text=plain, error=None)
        except _Cancelled:
            pass
        except Exception as error:
            if self._closed:
                return
            detail = str(error.detail) if isinstance(error, DataError) else f"{type(error).__name__}：{error}"
            detail = detail[:6000]
            with self._connection() as connection:
                row = self._row(connection, identifier)
                if row["status"] == "running":
                    connection.execute("UPDATE conv_jobs SET status='failed',stage='failed',progress=NULL,error=?,updated_at=? WHERE id=?", (detail, _now(), identifier))
                    connection.execute("INSERT INTO conv_errors(job_id,attempt,message,created_at) VALUES (?,?,?,?)",
                                       (identifier, row["attempt"], detail, _now()))
        finally:
            with self._lock:
                self._processes.pop(identifier, None)

    @staticmethod
    def _python() -> str:
        configured = os.environ.get("INKREFERENCE_MINERU_PYTHON", "").strip()
        if configured:
            executable = Path(configured).expanduser()
            if not executable.is_absolute() or not executable.is_file():
                raise DataError(503, "INKREFERENCE_MINERU_PYTHON 必须指向已安装 MinerU 4 的 Python 绝对路径")
            return str(executable)
        if getattr(sys, "frozen", False):
            raise DataError(503, "桌面安装版请先安装 MinerU 4，并设置 INKREFERENCE_MINERU_PYTHON 为该环境的 python.exe 完整路径")
        return sys.executable

    def _pdf_markdown(self, identifier: str, raw: bytes, options: dict[str, Any]) -> str:
        executable = self._python()
        with tempfile.TemporaryDirectory(prefix="inkreference-mineru-") as temporary:
            directory = Path(temporary)
            source, output = directory / "document.pdf", directory / "document.md"
            source.write_bytes(raw)
            command = [executable, "-I", "-c", _WORKER, str(source), str(output), options["tier"], options["ocr_mode"]]
            kwargs: dict[str, Any] = {"cwd": directory, "stdin": subprocess.DEVNULL,
                                      "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                      "shell": False}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            with self._lock:
                # Publish process ownership atomically with the close check:
                # shutdown cannot miss a process launched after its snapshot.
                self._check_cancelled(identifier)
                if self._closed:
                    raise _Cancelled()
                process = subprocess.Popen(command, **kwargs)
                self._processes[identifier] = process
            while True:
                if self._cancellations[identifier].is_set():
                    if not self._closed:
                        self._terminate_tree(process)
                    process.communicate()
                    raise _Cancelled()
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            self._check_cancelled(identifier)
            if process.returncode:
                message = (stderr or stdout).decode("utf-8", errors="replace")[-4000:]
                raise DataError(503, "MinerU 转换失败。请在转换 Python 环境安装 mineru>=4,<5；OCR 还需准备模型。\n" + message)
            if not output.is_file():
                raise DataError(422, "MinerU 进程结束但没有生成 Markdown 文件")
            return output.read_text(encoding="utf-8", errors="strict")

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               shell=False, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def cancel(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            meta = self.get(identifier)
            if meta["status"] in TERMINAL:
                return meta
            if identifier in self._cancellations:
                self._cancellations[identifier].set()
            process = self._processes.get(identifier)
            with self._connection() as connection:
                connection.execute("UPDATE conv_jobs SET status='cancelled',stage='cancelled',progress=NULL,updated_at=? WHERE id=? AND status IN ('queued','running')", (_now(), identifier))
        if process is not None:
            self._terminate_tree(process)
        return self.get(identifier)

    def retry(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            meta = self.get(identifier)
            if meta["status"] not in {"failed", "cancelled", "interrupted"}:
                raise DataError(409, "仅失败、取消或中断的任务可以重试")
            thread = self._threads.get(identifier)
            if thread is not None and thread.is_alive():
                raise DataError(409, "上次任务仍在结束，请稍后重试")
            with self._connection() as connection:
                connection.execute("UPDATE conv_jobs SET status='queued',stage='queued',progress=0,completed=0,total=NULL,error=NULL,markdown=NULL,plain_text=NULL,updated_at=? WHERE id=?", (_now(), identifier))
        return self.start(identifier)

    def get_output(self, identifier: str, format: str = "markdown") -> str:
        column = {"markdown": "markdown", "text": "plain_text", "md": "markdown", "txt": "plain_text"}.get(format)
        if column is None:
            raise DataError(422, "输出格式请选择 markdown 或 text")
        with self._connection() as connection:
            value = self._row(connection, identifier)[column]
            if value is None:
                raise DataError(409, "该输出尚未生成")
            return value

    def active_threads(self) -> list[threading.Thread]:
        with self._lock:
            return [thread for thread in self._threads.values() if thread.is_alive()]

    def shutdown(self, timeout: float = 2.5) -> None:
        """Signal every task first, then reap within one global time budget.

        SQLite may be busy during abrupt exit. In that case the next service
        startup already converts any remaining running rows to interrupted.
        """
        deadline = time.monotonic() + min(2.5, max(0.1, timeout))
        with self._lock:
            self._closed = True
            for event in self._cancellations.values():
                event.set()
            processes = list(self._processes.values())
            threads = list(self._threads.values())

        if os.name == "nt" and processes:
            # One taskkill invocation accepts repeated /PID options. Dispatch
            # every tree together instead of spending the budget on task one.
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            command = [str(taskkill)]
            for process in processes:
                command.extend(["/PID", str(process.pid)])
            command.extend(["/T", "/F"])
            try:
                killer = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    killer.wait(timeout=max(0, min(0.5, deadline - time.monotonic())))
            except OSError:
                pass
            # A failed taskkill still must not leave primary workers alive.
            for process in processes:
                if process.poll() is None:
                    with contextlib.suppress(OSError):
                        process.kill()
        elif processes:
            # No wait occurs before all groups receive TERM. Escalate every
            # group, even if its leader exited but a child still remains.
            for process in processes:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            grace = min(deadline, time.monotonic() + 0.15)
            while time.monotonic() < grace and any(process.poll() is None for process in processes):
                time.sleep(min(0.01, max(0, grace - time.monotonic())))
            for process in processes:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)

        message = "应用关闭，任务已中断；重新打开后可重试"
        connection = None
        try:
            connection = sqlite3.connect(self.db_path, timeout=max(0, min(0.1, deadline - time.monotonic())))
            rows = connection.execute("SELECT id,attempt FROM conv_jobs WHERE status='running'").fetchall()
            connection.execute("UPDATE conv_jobs SET status='interrupted',stage='interrupted',progress=NULL,error=?,updated_at=? WHERE status='running'", (message, _now()))
            connection.executemany("INSERT INTO conv_errors(job_id,attempt,message,created_at) VALUES (?,?,?,?)",
                                   [(identifier, attempt, message, _now()) for identifier, attempt in rows])
            connection.commit()
        except sqlite3.Error:
            pass
        finally:
            if connection is not None:
                connection.close()
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

    def get_original(self, identifier: str) -> tuple[bytes, str]:
        with self._connection() as connection:
            row = self._row(connection, identifier)
            raw = bytes(row["original"])
            if hashlib.sha256(raw).hexdigest() != row["original_sha256"]:
                raise DataError(422, "原文件哈希校验失败")
            return raw, row["filename"]

    def import_to_document(self, identifier: str, documents, *, target_document_id: str | None = None,
                           base_version_id: str | None = None, mode: str | None = None,
                           progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
        """Commit converted TXT to a fixed target, preserving its binary origin.

        A durable target binding plus document ingestion key closes both crash
        windows: before the document commit and before linking its conversion.
        """
        with self._lock:
            meta = self.get(identifier)
            if meta["status"] != "completed":
                raise DataError(409, "转换完成后才能导入全文库")
            selected_mode = mode if mode is not None else meta["options"].get("mode", "new")
            target_id = target_document_id if target_document_id is not None else meta["options"].get("target_document_id")
            base_id = base_version_id if base_version_id is not None else meta["options"].get("base_version_id")
            _options({"mode": selected_mode, "target_document_id": target_id, "base_version_id": base_id})
            target = _json({"mode": selected_mode, "target_document_id": target_id, "base_version_id": base_id})
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                binding = connection.execute("SELECT * FROM conv_imports WHERE job_id=?", (identifier,)).fetchone()
                if binding is not None and binding["target_json"] != target:
                    raise DataError(409, "该转换已绑定另一导入目标，请重新上传文件")
                if binding is None:
                    # Validate ownership/revision before pinning an import target.
                    if target_id:
                        document = documents.get_document(target_id)
                        documents.get_version(target_id, base_id)
                        if document["latest_version_id"] != base_id:
                            raise DataError(409, "全文已有新版本，请刷新后重新选择更新或补充")
                    connection.execute("INSERT INTO conv_imports(job_id,target_json) VALUES (?,?)", (identifier, target))
            key = f"conv:{identifier}"
            committed = documents.ingestion_result(key)
            if committed is None:
                plain = self.get_output(identifier, "text")
                raw, filename = self.get_original(identifier)
                if selected_mode == "new":
                    document = documents.upload(filename, raw, encoding="utf-8",
                                                title=meta["options"]["title"] or Path(filename).stem,
                                                work_id=meta["options"]["work_id"], idempotency_key=key,
                                                converted_text=plain, preserve_text=True, progress=progress)
                    committed = documents.ingestion_result(key)
                else:
                    writer = documents.replace if selected_mode == "replace" else documents.append
                    version = writer(target_id, base_id, filename, raw, encoding="utf-8", idempotency_key=key,
                                     converted_text=plain, preserve_text=True, progress=progress)
                    committed = {"document_id": target_id, "version_id": version["id"]}
            with self._connection() as connection:
                connection.execute("UPDATE conv_jobs SET imported_document_id=?,updated_at=? WHERE id=?",
                                   (committed["document_id"], _now(), identifier))
                connection.execute("UPDATE conv_imports SET version_id=? WHERE job_id=?", (committed["version_id"], identifier))
            return {**documents.get_document(committed["document_id"]), "imported_version_id": committed["version_id"]}
