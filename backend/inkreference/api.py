"""Local HTTP boundary for the reference library.

Importing this module does not create a database or touch a user's data directory.
The server lifespan initializes the configured application data directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from starlette.concurrency import run_in_threadpool

from . import __version__
from .csvio import commit_import, export_table, export_zip, parse_files, preview_import
from .database import DataError, DataStore
from .workspace_lock import workspace_lock
from .local_security import SECURITY_HEADERS, allowed_origin, trusted_host

TableName = Literal["works", "characters", "cliches"]
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_BATCH_BYTES = 128 * 1024 * 1024
BATCH_TTL_SECONDS = 2 * 60 * 60
_CHUNK_BYTES = 1024 * 1024


def default_data_dir() -> Path:
    """Return the OS application-data location, without creating it."""
    configured = os.environ.get("INKREFERENCE_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "InkReference"
    if sys.platform == "darwin":
        # Earlier source checkouts used the Linux-style path on macOS. Reuse
        # an existing workspace so this platform update never hides its data.
        legacy = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "InkReference"
        try:
            with (legacy / "inkreference.sqlite3").open("rb") as database:
                if database.read(16) == b"SQLite format 3\x00":
                    return legacy
        except OSError:
            pass
        return Path.home() / "Library" / "Application Support" / "InkReference"
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "InkReference"


class CreateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values: dict[StrictStr, StrictStr] = Field(max_length=200)


class UpdateRecord(CreateRecord):
    expected_revision: StrictInt = Field(ge=1)


class CreateRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values: dict[str, Any] = Field(max_length=200)


class UpdateRelation(CreateRelation):
    expected_revision: StrictInt = Field(ge=1)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    batch_id: StrictStr = Field(min_length=1, max_length=100)
    policy: Literal["skip", "update", "error"]


@dataclass
class PendingBatch:
    batch: object
    expires_at: float
    source_dir: Path
    kind: str = "legacy_csv"


class Runtime:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.lock = threading.RLock()
        self.pending: dict[str, PendingBatch] = {}
        self._store: DataStore | None = None
        self._studio = None

    @property
    def studio(self):
        with self.lock:
            if self._studio is None:
                from .studio import Studio
                self._studio = Studio(self)
            return self._studio

    @property
    def store(self) -> DataStore:
        with self.lock:
            if self._store is None:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                self._store = DataStore(self.data_dir / "inkreference.sqlite3")
            return self._store

    def expire_batches(self) -> None:
        now = time.monotonic()
        for batch_id in list(self.pending):
            if self.pending[batch_id].expires_at <= now:
                del self.pending[batch_id]


def _safe_filename(filename: str | None, index: int) -> str:
    # Both separators are handled even when the server runs on the other OS.
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .")[:120]
    if not name or name in {".", ".."}:
        name = f"upload_{index}.csv"
    # The index also makes duplicate uploaded names independent files.
    return f"{index:02d}_{name}"


def _save_sources(runtime: Runtime, batch_id: str, files: list[tuple[str, bytes]]) -> Path:
    imports_dir = runtime.data_dir / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = imports_dir / batch_id
    source_dir.mkdir(mode=0o700)
    manifest = {"batch_id": batch_id, "created_at_unix": time.time(), "files": []}
    try:
        for index, (uploaded_name, raw) in enumerate(files, start=1):
            stored_name = _safe_filename(uploaded_name, index)
            target = source_dir / stored_name
            target.write_bytes(raw)
            target.chmod(0o600)
            manifest["files"].append({
                "uploaded_name": uploaded_name,
                "stored_name": stored_name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
        target = source_dir / "manifest.json"
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        target.chmod(0o600)
    except Exception:
        shutil.rmtree(source_dir, ignore_errors=True)
        raise
    return source_dir


def _origin_allowed(request: Request, origin: str) -> bool:
    return allowed_origin(origin, str(request.base_url).rstrip("/"))


def create_app(data_dir: str | Path | None = None, static_dir: str | Path | None = None) -> FastAPI:
    runtime = Runtime(Path(data_dir).expanduser().resolve() if data_dir is not None else default_data_dir())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with workspace_lock(runtime.data_dir, pending_threads=lambda: runtime._studio.active_threads() if runtime._studio else []):
            runtime.store
            try:
                yield
            finally:
                if runtime._studio is not None:
                    if runtime._studio._ingestions is not None:
                        await run_in_threadpool(runtime._studio.ingestions.shutdown)
                    # Stop native conversion processes before waiting for a
                    # model request boundary; the desktop close is bounded.
                    conversions = runtime._studio._conversions
                    if conversions is not None:
                        await run_in_threadpool(conversions.shutdown)
                    if runtime._studio._runner is not None:
                        await run_in_threadpool(runtime._studio.runner.shutdown)
                runtime.pending.clear()

    application = FastAPI(title="InkReference", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    application.state.runtime = runtime

    @application.exception_handler(DataError)
    async def handle_data_error(_: Request, exc: DataError):
        return JSONResponse(status_code=exc.status_code, content={"detail": jsonable_encoder(exc.detail)})

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError):
        # FastAPI's default error includes rejected input, which can be a key.
        errors = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    @application.middleware("http")
    async def local_mutation_guard(request: Request, call_next):
        def protected(response):
            response.headers.update(SECURITY_HEADERS)
            if request.url.path.startswith("/api/") or "text/html" in response.headers.get("content-type", ""):
                response.headers["Cache-Control"] = "no-store"
            return response

        hosts = request.headers.getlist("host")
        if len(hosts) != 1 or not trusted_host(hosts[0]):
            return protected(JSONResponse(status_code=400, content={"detail": "仅允许本机工作区地址。"}))
        origin = request.headers.get("origin")
        if origin is not None and not _origin_allowed(request, origin):
            return protected(JSONResponse(status_code=403, content={"detail": "已阻止来自其他网站的访问。"}))
        if request.headers.get("sec-fetch-site") == "cross-site":
            return protected(JSONResponse(status_code=403, content={"detail": "已阻止跨站访问本机工作区。"}))
        # Avoid accepting an obviously oversized multipart request before parsing.
        # Actual per-file and batch byte limits are checked while reading uploads.
        if request.url.path in {"/api/import/preview", "/api/library/import/preview"}:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_BATCH_BYTES + 1024 * 1024:
                        return protected(JSONResponse(status_code=413, content={"detail": "一次导入的数据集总大小不能超过 128 MB。"}))
                except ValueError:
                    return protected(JSONResponse(status_code=400, content={"detail": "无效的请求长度。"}))
        return protected(await call_next(request))

    @application.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @application.get("/api/meta")
    def meta():
        return runtime.store.meta()

    @application.get("/api/stats")
    def stats():
        return runtime.store.stats()

    @application.get("/api/records/{table}")
    def list_records(
        table: TableName,
        q: Annotated[str, Query(max_length=1000)] = "",
        offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        work_id: Annotated[str | None, Query(max_length=300)] = None,
        cliche_id: Annotated[str | None, Query(max_length=300)] = None,
    ):
        return runtime.store.list_records(table, q=q, offset=offset, limit=limit, work_id=work_id, cliche_id=cliche_id)

    @application.get("/api/records/{table}/{record_id}/relations")
    def relations(table: TableName, record_id: str):
        return runtime.store.relations(table, record_id)

    @application.get("/api/relations")
    def material_relations():
        return {"items": runtime.store.list_material_relations()}

    @application.post("/api/relations", status_code=201)
    def create_material_relation(body: CreateRelation):
        return runtime.store.create_material_relation(body.values)

    @application.patch("/api/relations/{identifier}")
    def update_material_relation(identifier: str, body: UpdateRelation):
        return runtime.store.update_material_relation(identifier, body.values, body.expected_revision)

    @application.delete("/api/relations/{identifier}")
    def delete_material_relation(identifier: str, expected_revision: Annotated[int, Query(ge=1)]):
        return runtime.store.delete_material_relation(identifier, expected_revision)

    @application.get("/api/records/{table}/{record_id}")
    def get_record(table: TableName, record_id: str):
        return runtime.store.get_record(table, record_id)

    @application.post("/api/records/{table}", status_code=201)
    def create_record(table: TableName, body: CreateRecord):
        return runtime.store.create_record(table, body.values)

    @application.patch("/api/records/{table}/{record_id}")
    def update_record(table: TableName, record_id: str, body: UpdateRecord):
        return runtime.store.update_record(table, record_id, body.values, body.expected_revision)

    @application.delete("/api/records/{table}/{record_id}")
    def delete_record(
        table: TableName,
        record_id: str,
        expected_revision: Annotated[int, Query(ge=1)],
        cascade: bool = False,
    ):
        if table == "works":
            with runtime.studio.lock:
                # Read active jobs first: a worker can commit a document and
                # complete between reads, but never complete before that commit.
                if any(item["status"] in {"queued", "running"} and item.get("options", {}).get("work_id") == record_id
                       for item in runtime.studio.ingestions.list()):
                    raise DataError(409, "此作品有正在处理的全文，请等待完成或取消后再删除。")
                if any(item.get("work_id") == record_id for item in runtime.studio.documents.list_documents()):
                    raise DataError(409, "此作品已关联全文，请保留其作品 ID；仍可编辑作品字段。")
                return runtime.store.delete_record(table, record_id, expected_revision, cascade=cascade)
        return runtime.store.delete_record(table, record_id, expected_revision, cascade=cascade)

    @application.post("/api/import/preview")
    async def preview(files: Annotated[list[UploadFile], File()]):
        try:
            if not files or len(files) > 32:
                raise DataError(400, "请选择 1 至 32 个 CSV 文件。")
            inputs: list[tuple[str, bytes]] = []
            total_bytes = 0
            for upload in files:
                filename = upload.filename or "upload.csv"
                if not filename.lower().endswith(".csv"):
                    raise DataError(400, "导入文件必须为 CSV。")
                raw = bytearray()
                while chunk := await upload.read(_CHUNK_BYTES):
                    raw.extend(chunk)
                    total_bytes += len(chunk)
                    if len(raw) > MAX_FILE_BYTES:
                        raise DataError(413, "单个 CSV 文件不能超过 32 MB。")
                    if total_bytes > MAX_BATCH_BYTES:
                        raise DataError(413, "一次导入的 CSV 总大小不能超过 128 MB。")
                inputs.append((filename, bytes(raw)))

            def prepare():
                # Parsing happens first; preview does not write application rows.
                batch = parse_files(inputs)
                with runtime.lock:
                    runtime.expire_batches()
                    result = dict(preview_import(runtime.store, batch))
                    batch_id = uuid.uuid4().hex
                    source_dir = _save_sources(runtime, batch_id, inputs)
                    runtime.pending[batch_id] = PendingBatch(batch, time.monotonic() + BATCH_TTL_SECONDS, source_dir)
                    result["batch_id"] = batch_id
                    return result

            return await run_in_threadpool(prepare)
        finally:
            for upload in files:
                await upload.close()

    @application.post("/api/import/commit")
    def commit(body: CommitRequest):
        with runtime.lock:
            runtime.expire_batches()
            pending = runtime.pending.get(body.batch_id)
            if pending is None or pending.kind != "legacy_csv":
                raise DataError(404, "CSV 预览已过期或不属于此导入流程，请重新选择 CSV 并预览。")
            # commit_import revalidates the batch against the latest data and
            # applies one transaction. Only successful commits consume the token.
            result = commit_import(runtime.store, pending.batch, body.policy)
            del runtime.pending[body.batch_id]
            return result

    @application.get("/api/export")
    def download_zip(mode: Literal["compatible", "full"] = "compatible"):
        return Response(
            export_zip(runtime.store, mode),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="inkreference_{mode}.zip"'},
        )

    @application.get("/api/export/{table}")
    def download_csv(table: TableName):
        filename = {"works": "works_library_v8.csv", "characters": "character_database_v8.csv", "cliches": "cliche_database_v8.csv"}[table]
        return Response(
            export_table(runtime.store, table),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @application.get("/api/library/formats")
    def library_formats():
        from .library_exchange import library_formats as formats
        return formats()

    @application.post("/api/library/import/preview")
    async def preview_library(files: Annotated[list[UploadFile], File()]):
        from .library_exchange import parse_library_files, preview_library_import
        try:
            if not files or len(files) > 32:
                raise DataError(400, "请选择一个 DB、Excel 或完整的 CSV 文件集。")
            inputs: list[tuple[str, bytes]] = []
            total = 0
            for uploaded in files:
                name = uploaded.filename or "upload"
                if Path(name).suffix.lower() not in {".db", ".sqlite", ".sqlite3", ".csv", ".xlsx", ".zip", ".json"}:
                    raise DataError(422, f"{name}：支持 DB、XLSX、完整 CSV 文件集或 CSV ZIP。")
                raw = bytearray()
                while chunk := await uploaded.read(_CHUNK_BYTES):
                    raw.extend(chunk)
                    total += len(chunk)
                    if len(raw) > MAX_BATCH_BYTES or total > MAX_BATCH_BYTES:
                        raise DataError(413, "一次素材库导入最多 128 MB，请拆分过大的数据集。")
                inputs.append((name, bytes(raw)))
            def prepare():
                batch = parse_library_files(inputs)
                with runtime.lock:
                    runtime.expire_batches()
                    result = dict(preview_library_import(runtime.store, batch))
                    batch_id = uuid.uuid4().hex
                    source_dir = _save_sources(runtime, batch_id, inputs)
                    runtime.pending[batch_id] = PendingBatch(batch, time.monotonic() + BATCH_TTL_SECONDS, source_dir, "library")
                    result["batch_id"] = batch_id
                    return result
            return await run_in_threadpool(prepare)
        finally:
            for uploaded in files:
                await uploaded.close()

    @application.post("/api/library/import/commit")
    def commit_library(body: CommitRequest):
        from .library_exchange import commit_library_import
        with runtime.lock:
            runtime.expire_batches()
            pending = runtime.pending.get(body.batch_id)
            if pending is None or pending.kind != "library":
                raise DataError(404, "素材库预览已过期或不属于此导入流程，请重新选择完整数据集。")
            result = commit_library_import(runtime.store, pending.batch, body.policy)
            del runtime.pending[body.batch_id]
            return result

    @application.get("/api/library/export")
    def export_library(format: Literal["db", "csv", "xlsx"] = "db"):
        from .library_exchange import export_library as export
        media, filename = {
            "db": ("application/vnd.sqlite3", "InkReference-library.db"),
            "csv": ("application/zip", "InkReference-library-csv.zip"),
            "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "InkReference-library.xlsx"),
        }[format]
        return Response(export(runtime.store, format=format), media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    from .studio import install_studio_routes
    install_studio_routes(application, runtime)

    # API typos must stay JSON 404s rather than falling through to the SPA.
    @application.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_api(path: str):
        raise DataError(404, "API 路径不存在。")

    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    frontend = Path(static_dir) if static_dir is not None else resource_root / "frontend" / "dist"
    if (frontend / "index.html").is_file():
        application.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    else:
        @application.get("/", response_class=HTMLResponse)
        def missing_frontend():
            return """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>InkReference</title>
            <body style="font-family:system-ui;max-width:720px;margin:72px auto;padding:24px">
            <h1>InkReference 后端已启动</h1><p>请先在项目的 frontend 目录构建界面：</p>
            <pre>npm install\nnpm run build</pre><p>构建完成后重启服务。开发时可运行 npm run dev。</p>
            <p><a href="/api/health">服务状态</a></p></body></html>"""

    return application


app = create_app()
