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


from .application_paths import default_data_dir


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
                self._store.write_lock = self.lock
                self._store.character_sync = lambda connection, character_id, values: self.studio.character_bridge.sync_identity_from_catalog_connection(connection, character_id, values)
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


def _diagnostic_path(request: Request) -> str:
    route = request.scope.get("route")
    if getattr(route, "path", None):
        return route.path
    return "/assets/{asset}" if request.url.path.startswith("/assets/") else "/{unmatched}"


def create_app(data_dir: str | Path | None = None, static_dir: str | Path | None = None) -> FastAPI:
    runtime = Runtime(Path(data_dir).expanduser().resolve() if data_dir is not None else default_data_dir())

    def active_workers():
        workers = runtime._studio.active_threads() if runtime._studio else []
        repair_workers = getattr(application.state, "active_quick_repair_threads", None)
        return workers + (repair_workers() if repair_workers is not None else [])

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with workspace_lock(runtime.data_dir, pending_threads=active_workers):
            runtime.store
            try:
                yield
            finally:
                repair_shutdown = getattr(application.state, "shutdown_quick_repair", None)
                if repair_shutdown is not None:
                    await run_in_threadpool(repair_shutdown)
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
                    if runtime._studio._research is not None:
                        await run_in_threadpool(runtime._studio.research.shutdown)
                    if runtime._studio._volume_research is not None:
                        await run_in_threadpool(runtime._studio.volume_research.shutdown)
                    if runtime._studio._embedding_downloads is not None:
                        await run_in_threadpool(runtime._studio.embedding_downloads.shutdown)
                runtime.pending.clear()

    application = FastAPI(title="InkReference", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    application.state.runtime = runtime
    from .diagnostics import install_diagnostics, record_exception

    @application.exception_handler(DataError)
    async def handle_data_error(_: Request, exc: DataError):
        record_exception("http", "validation", exc, data_dir=runtime.data_dir, http_status=exc.status_code)
        return JSONResponse(status_code=exc.status_code, content={"detail": jsonable_encoder(exc.detail)})

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError):
        record_exception("http", "validation", exc, data_dir=runtime.data_dir, http_status=422)
        # FastAPI's default error includes rejected input, which can be a key.
        errors = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    @application.middleware("http")
    async def local_mutation_guard(request: Request, call_next):
        started = time.monotonic()

        def protected(response):
            response.headers.update(SECURITY_HEADERS)
            if request.url.path.startswith("/api/") or "text/html" in response.headers.get("content-type", ""):
                response.headers["Cache-Control"] = "no-store"
            try:
                repair_service(application).record_request(request.method, _diagnostic_path(request), response.status_code,
                                                           (time.monotonic() - started) * 1000)
            except Exception:
                # Diagnostics must not turn a completed operation into an error.
                pass
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
        try:
            response = await call_next(request)
        except Exception as exc:
            record_exception("http", "dispatch", exc, data_dir=runtime.data_dir, http_status=500)
            try:
                repair_service(application).record_exception(exc, method=request.method, path=_diagnostic_path(request))
            except Exception:
                pass
            response = JSONResponse(status_code=500, content={"detail": "程序内部错误。", "repair_available": True})
        return protected(response)

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
        category: Annotated[str | None, Query(max_length=300)] = None,
        seen: Annotated[str | None, Query(max_length=100)] = None,
        genre: Annotated[str | None, Query(max_length=300)] = None,
        style: Annotated[str | None, Query(max_length=300)] = None,
        role: Annotated[str | None, Query(max_length=300)] = None,
        character_trait: Annotated[str | None, Query(max_length=100)] = None,
        plot_mechanism: Annotated[str | None, Query(max_length=100)] = None,
        has_text: bool | None = None,
        has_analysis: bool | None = None,
        has_uploaded_text: bool | None = None,
        analysis_completed: bool | None = None,
        sort_by: Annotated[str | None, Query(max_length=100)] = None,
        sort_direction: Literal["asc", "desc"] = "asc",
        include_work_facets: bool = True,
    ):
        filters = {key: value for key, value in {"category": category, "seen": seen, "genre": genre, "style": style, "role": role,
            "character_trait": character_trait, "plot_mechanism": plot_mechanism}.items() if value is not None}
        listing = dict(q=q, offset=offset, limit=limit, work_id=work_id,
            cliche_id=cliche_id, filters=filters, sort_by=sort_by, sort_direction=sort_direction, include_work_facets=include_work_facets)
        if any(value is not None for value in (has_text, has_analysis, has_uploaded_text, analysis_completed)):
            if table != "works":
                raise DataError(422, "文本与分析筛选仅适用于作品")
            return application.state.catalog_links().list_records(table, has_text=has_text, has_analysis=has_analysis,
                has_uploaded_text=has_uploaded_text, analysis_completed=analysis_completed, **listing)
        return runtime.store.list_records(table, **listing)

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
                linked = [item for item in runtime.studio.documents.list_documents() if item.get("work_id") == record_id]
                if linked:
                    titles = "、".join(item.get("title") or item["id"] for item in linked[:3])
                    raise DataError(409, f"此作品关联全文“{titles}”，请先在作品详情中解除关联。")
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

    @application.get("/api/library/export/legacy")
    def export_legacy_library():
        from .csvio import export_legacy_zip
        return Response(export_legacy_zip(runtime.store), media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="InkReference-legacy-csv.zip"'})

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
    from .repair_routes import install_repair_routes, repair_service
    install_repair_routes(application, runtime)
    from .prompt_workbench import create_prompt_workbench_router, get_prompt_workbench
    application.include_router(create_prompt_workbench_router(lambda: runtime.studio, lambda: application.state.quick_repair()))
    from .document_replace import create_router as create_document_replace_router
    application.include_router(create_document_replace_router(lambda: runtime.studio.documents, lambda: runtime.studio.lock))
    from .task_routes import install_task_routes
    install_task_routes(application, runtime)
    from .catalog_routes import install_catalog_routes
    install_catalog_routes(application, runtime)
    from .character_routes import install_character_routes
    install_character_routes(application, runtime)
    from .research_api import create_research_router
    application.include_router(create_research_router(lambda: runtime.studio.research,
        lambda: get_prompt_workbench(runtime.studio, lambda: application.state.quick_repair())))
    from .embedding_download_api import create_embedding_download_router
    application.include_router(create_embedding_download_router(lambda: runtime.studio.embedding_downloads))
    from .dynamic_routes import create_dynamic_router
    application.include_router(create_dynamic_router())
    from .storage_health import StorageHealth, create_storage_router
    def require_idle_storage():
        if runtime._studio is not None:
            runtime.studio.require_idle()
    application.include_router(create_storage_router(lambda: StorageHealth(runtime.data_dir), require_idle_storage))

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

    install_diagnostics(application, runtime.data_dir)
    return application


app = create_app()
