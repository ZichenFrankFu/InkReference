"""HTTP integration for documents, durable analysis, configuration and projects."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool

from .database import DataError
from .documents import DocumentStore, MAX_UPLOAD_BYTES
from .materialization import Materializer
from .models import ModelService
from .projects import MAX_BACKUP_BYTES, ProjectStore
from .skills import SkillStore
from .workflow_definitions import WorkflowStore


class NewVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_version_id: str = Field(min_length=1, max_length=200)
    options: dict[str, Any] = Field(default_factory=dict)
    manual_text: str | None = None


class NewRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    profile_id: str = Field(min_length=1, max_length=100)
    workflow_id: str = "default"
    workflow_version: str = "1.0.0"
    start: bool = True


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)


class Review(Revision):
    results: dict[str, Any]


class Studio:
    def __init__(self, runtime):
        from .workflows import WorkflowRunner
        self.lock = threading.RLock()
        self.documents = DocumentStore(runtime.data_dir)
        self.models = ModelService(runtime.data_dir)
        self.skills = SkillStore(runtime.data_dir)
        self.workflows = WorkflowStore(runtime.data_dir)
        self.projects = ProjectStore(runtime.data_dir)
        self.data_dir = runtime.data_dir
        self._conversions = None
        self.runner = WorkflowRunner(
            runtime.data_dir, documents=self.documents, models=self.models,
            skills=self.skills, workflows=self.workflows,
            materializer=Materializer(runtime.store, self.documents),
        )

    @property
    def conversions(self):
        with self.lock:
            if self._conversions is None:
                from .conversions import ConversionStore
                self._conversions = ConversionStore(self.data_dir)
            return self._conversions

    def require_idle(self):
        if any(run["status"] in {"queued", "running", "cancelling", "approving"} for run in self.runner.list_runs()):
            raise DataError(409, "请先暂停或完成正在运行的分析，再导出项目。")
        if any(item["status"] in {"queued", "running"} for item in self.conversions.list()):
            raise DataError(409, "请先完成或取消格式转换，再导出项目。")


async def _upload(file: UploadFile, maximum: int) -> bytes:
    try:
        result = bytearray()
        while chunk := await file.read(1024 * 1024):
            result.extend(chunk)
            if len(result) > maximum:
                raise DataError(413, "上传文件超过大小上限。")
        if not result:
            raise DataError(422, "请选择非空文件。")
        return bytes(result)
    finally:
        await file.close()


def _file_response(raw: bytes, filename: str, media_type: str) -> Response:
    return Response(raw, media_type=media_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})


def install_studio_routes(application, runtime):
    router = APIRouter(prefix="/api/studio", tags=["studio"])

    @router.get("/documents")
    def documents():
        return {"items": runtime.studio.documents.list_documents()}

    @router.post("/documents", status_code=201)
    async def upload_document(file: Annotated[UploadFile, File()], title: Annotated[str, Form()] = "", encoding: Annotated[str, Form()] = "auto", work_id: Annotated[str | None, Form()] = None):
        raw = await _upload(file, MAX_UPLOAD_BYTES)
        def save():
            with runtime.studio.lock:
                if work_id:
                    runtime.store.get_record("works", work_id)
                return runtime.studio.documents.upload(file.filename or "upload.txt", raw, encoding=encoding, title=title, work_id=work_id or None)
        return await run_in_threadpool(save)

    @router.get("/documents/{document_id}")
    def document(document_id: str):
        return runtime.studio.documents.get_document(document_id)

    @router.get("/documents/{document_id}/versions/{version_id}")
    def version(document_id: str, version_id: str, include_text: bool = True):
        result = runtime.studio.documents.get_version(document_id, version_id)
        if not include_text:
            result = dict(result)
            result["characters"] = len(result["text"])
            result.pop("text", None)
            result.pop("source_text", None)
            result.pop("diff", None)
            # Span metadata suffices for navigation; original text is paged.
            for key in ("chapters", "chunks"):
                result[key] = [{k: v for k, v in item.items() if k not in {"text", "content"}} for item in result.get(key, [])]
        return result

    @router.get("/documents/{document_id}/versions/{version_id}/text")
    def text(document_id: str, version_id: str, start: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100000)] = 20000, kind: str = "cleaned"):
        if kind not in {"cleaned", "source"}:
            raise DataError(422, "未知文本版本类型。")
        result = runtime.studio.documents.get_version(document_id, version_id)
        source = result["text" if kind == "cleaned" else "source_text"]
        return {"text": source[start:start + limit], "start": start, "total": len(source), "limit": limit, "kind": kind}

    @router.get("/documents/{document_id}/versions/{version_id}/diff")
    def diff(document_id: str, version_id: str):
        result = runtime.studio.documents.get_version(document_id, version_id)
        return {"diff": result.get("diff", ""), "diff_truncated": result.get("diff_truncated", False)}

    @router.post("/documents/{document_id}/versions", status_code=201)
    def new_version(document_id: str, body: NewVersion):
        with runtime.studio.lock:
            return runtime.studio.documents.create_version(document_id, body.base_version_id, body.options, manual_text=body.manual_text)

    @router.get("/documents/{document_id}/original/download")
    def original(document_id: str):
        raw, filename = runtime.studio.documents.get_original(document_id)
        return _file_response(raw, filename, "application/octet-stream")

    @router.get("/documents/{document_id}/versions/{version_id}/download")
    def cleaned(document_id: str, version_id: str):
        result = runtime.studio.documents.get_version(document_id, version_id)
        return _file_response(result["text"].encode("utf-8-sig"), f"cleaned-{version_id[:8]}.txt", "text/plain; charset=utf-8")

    @router.get("/jobs")
    def jobs():
        return {"items": runtime.studio.runner.list_runs()}

    @router.post("/jobs", status_code=201)
    def create_job(body: NewRun):
        with runtime.studio.lock:
            result = runtime.studio.runner.create_run(body.document_id, body.version_id, workflow_id=body.workflow_id, workflow_version=body.workflow_version, profile_id=body.profile_id)
            return runtime.studio.runner.start(result["id"]) if body.start else result

    @router.get("/jobs/{run_id}")
    def job(run_id: str):
        return runtime.studio.runner.get_run(run_id)

    @router.post("/jobs/{run_id}/resume")
    def resume_job(run_id: str):
        with runtime.studio.lock:
            return runtime.studio.runner.resume(run_id)

    @router.post("/jobs/{run_id}/start")
    def start_job(run_id: str):
        with runtime.studio.lock:
            return runtime.studio.runner.start(run_id)

    @router.post("/jobs/{run_id}/cancel")
    def cancel_job(run_id: str):
        return runtime.studio.runner.cancel(run_id)

    @router.put("/jobs/{run_id}/results")
    def review_job(run_id: str, body: Review):
        with runtime.studio.lock:
            return runtime.studio.runner.edit_result(run_id, body.results, body.expected_revision)

    @router.post("/jobs/{run_id}/approve")
    def approve_job(run_id: str, body: Revision):
        with runtime.studio.lock:
            return runtime.studio.runner.approve(run_id, body.expected_revision)

    @router.get("/models")
    def models():
        return {"items": runtime.studio.models.list_profiles()}

    @router.post("/models", status_code=201)
    def new_model(body: dict[str, Any]):
        with runtime.studio.lock:
            return runtime.studio.models.save_profile(body)

    @router.put("/models/{profile_id}")
    def update_model(profile_id: str, body: dict[str, Any]):
        with runtime.studio.lock:
            return runtime.studio.models.save_profile(body, profile_id=profile_id)

    @router.delete("/models/{profile_id}")
    def delete_model(profile_id: str):
        with runtime.studio.lock:
            return runtime.studio.models.delete_profile(profile_id)

    @router.post("/models/{profile_id}/test")
    def test_model(profile_id: str):
        with runtime.studio.lock:
            return runtime.studio.models.test_profile(profile_id)

    @router.get("/usage")
    def usage(run_id: str | None = None):
        return {"items": runtime.studio.models.list_usage(run_id), "summary": runtime.studio.models.usage_summary(run_id)}

    @router.get("/skills")
    def skills():
        return {"items": runtime.studio.skills.list()}

    @router.post("/skills", status_code=201)
    def new_skill(body: dict[str, Any]):
        with runtime.studio.lock:
            return runtime.studio.skills.save(body)

    @router.post("/skills/upload", status_code=201)
    async def upload_skill(file: Annotated[UploadFile, File()]):
        raw = await _upload(file, 2 * 1024 * 1024)
        def save():
            with runtime.studio.lock:
                return runtime.studio.skills.upload(file.filename or "skill.json", raw)
        return await run_in_threadpool(save)

    @router.get("/skills/{identifier}/versions/{version}")
    def get_skill(identifier: str, version: str):
        return runtime.studio.skills.get(identifier, version)

    @router.get("/workflows")
    def workflows():
        return {"items": runtime.studio.workflows.list()}

    @router.post("/workflows", status_code=201)
    def new_workflow(body: dict[str, Any]):
        with runtime.studio.lock:
            runtime.studio.skills.get(body.get("skill_id", "unified-reference"), body.get("skill_version", "1.0.0"))
            return runtime.studio.workflows.save(body)

    @router.get("/workflows/{identifier}/versions/{version}")
    def get_workflow(identifier: str, version: str):
        return runtime.studio.workflows.get(identifier, version)

    @router.get("/workflows/{identifier}/versions/{version}/layout")
    def get_layout(identifier: str, version: str):
        return runtime.studio.workflows.get_layout(identifier, version)

    @router.put("/workflows/{identifier}/versions/{version}/layout")
    def save_layout(identifier: str, version: str, body: dict[str, Any]):
        with runtime.studio.lock:
            # Layout coordinates are presentation only; execution is versioned
            # through a separately validated workflow definition.
            layout = body.get("layout", body)
            return runtime.studio.workflows.save_layout(identifier, version, layout, expected_revision=body.get("expected_revision"))

    @router.get("/projects/export")
    def export_project():
        with runtime.studio.lock:
            runtime.studio.require_idle()
            return _file_response(runtime.studio.projects.export(), "InkReference-project.zip", "application/zip")

    @router.get("/conversions")
    def conversions():
        return {"items": runtime.studio.conversions.list()}

    @router.post("/conversions", status_code=201)
    async def convert(file: Annotated[UploadFile, File()], options: Annotated[str, Form()] = "{}"):
        import json
        raw = await _upload(file, MAX_UPLOAD_BYTES)
        try:
            settings = json.loads(options)
        except (ValueError, TypeError) as exc:
            raise DataError(422, "转换选项必须为 JSON 对象。") from exc
        def create():
            with runtime.studio.lock:
                if isinstance(settings, dict) and settings.get("work_id"):
                    runtime.store.get_record("works", settings["work_id"])
                result = runtime.studio.conversions.create(file.filename or "upload.epub", raw, options=settings)
                return runtime.studio.conversions.start(result["id"])
        return await run_in_threadpool(create)

    @router.get("/conversions/{identifier}")
    def conversion(identifier: str):
        return runtime.studio.conversions.get(identifier)

    @router.post("/conversions/{identifier}/start")
    def start_conversion(identifier: str):
        with runtime.studio.lock:
            return runtime.studio.conversions.start(identifier)

    @router.post("/conversions/{identifier}/cancel")
    def cancel_conversion(identifier: str):
        return runtime.studio.conversions.cancel(identifier)

    @router.post("/conversions/{identifier}/retry")
    def retry_conversion(identifier: str):
        with runtime.studio.lock:
            return runtime.studio.conversions.retry(identifier)

    @router.post("/conversions/{identifier}/import", status_code=201)
    def import_conversion(identifier: str):
        with runtime.studio.lock:
            conversion = runtime.studio.conversions.get(identifier)
            if conversion.get("options", {}).get("work_id"):
                runtime.store.get_record("works", conversion["options"]["work_id"])
            return runtime.studio.conversions.import_to_document(identifier, runtime.studio.documents)

    @router.get("/conversions/{identifier}/output")
    def conversion_output(identifier: str, format: str = "markdown"):
        if format not in {"markdown", "text"}:
            raise DataError(422, "输出格式必须为 markdown 或 text。")
        return {"text": runtime.studio.conversions.get_output(identifier, format=format), "format": format}

    @router.get("/conversions/{identifier}/original/download")
    def conversion_original(identifier: str):
        raw, filename = runtime.studio.conversions.get_original(identifier)
        return _file_response(raw, filename, "application/octet-stream")

    @router.get("/conversions/{identifier}/download")
    def download_conversion(identifier: str, format: str = "md"):
        if format not in {"md", "txt"}:
            raise DataError(422, "下载格式必须为 md 或 txt。")
        result = runtime.studio.conversions.get(identifier)
        output = runtime.studio.conversions.get_output(identifier, format="markdown" if format == "md" else "text")
        filename = Path(result["filename"]).stem + "." + format
        return _file_response(output.encode("utf-8-sig"), filename, "text/markdown; charset=utf-8" if format == "md" else "text/plain; charset=utf-8")

    @router.get("/projects/tables/export")
    def export_tables():
        with runtime.studio.lock:
            runtime.studio.require_idle()
            return _file_response(runtime.studio.projects.export_tables(), "InkReference-tables.zip", "application/zip")

    @router.post("/projects/restore", status_code=201)
    async def restore_project(file: Annotated[UploadFile, File()]):
        raw = await _upload(file, MAX_BACKUP_BYTES)
        return await run_in_threadpool(runtime.studio.projects.restore, raw)

    application.include_router(router)
