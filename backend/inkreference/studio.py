"""HTTP integration for documents, durable analysis, configuration and projects."""
from __future__ import annotations

import threading
import sqlite3
from contextlib import closing
import re
from pathlib import Path
from typing import Annotated, Any, Literal
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
from .volumes import VolumeStore
from .reference_analysis import ReferenceAnalysisStore


class NewVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_version_id: str = Field(min_length=1, max_length=200)
    options: dict[str, Any] = Field(default_factory=dict)
    manual_text: str | None = None


class ChapterRange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start_chapter: StrictInt = Field(ge=1)
    end_chapter: StrictInt = Field(ge=1)


class NewRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    profile_id: str | None = Field(default=None, min_length=1, max_length=100)
    workflow_id: str = "default"
    workflow_version: str = "4.0.0"
    start: bool = True
    manual: bool = False
    interaction_mode: Literal["automatic", "semi", "manual"] | None = None
    remote_consent: bool = False
    expected_model_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    expected_stage_fingerprints: dict[str, str] | None = None
    chapter_ranges: list[ChapterRange] | None = Field(default=None, max_length=100)
    reuse_existing: bool = True


class ComparisonVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(min_length=1, max_length=40)
    profile_id: str = Field(min_length=1, max_length=100)
    remote_consent: bool = False
    expected_model_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    expected_stage_fingerprints: dict[str, str] | None = None


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str | None = Field(default=None, min_length=1, max_length=200)
    workflow_id: str = "default"
    workflow_version: str = "4.0.0"
    chapter_ranges: list[ChapterRange] | None = Field(default=None, max_length=100)
    variants: list[ComparisonVariant] = Field(min_length=2, max_length=4)
    start: bool = False
    reuse_existing: bool = True


class ComparisonSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=200)
    expected_revision: StrictInt = Field(ge=1)


class ConnectionConsent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    remote_consent: bool = False


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=1)


class Review(Revision):
    results: dict[str, Any]


class ChapterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_version_id: str = Field(min_length=1, max_length=200)
    chapter_id: str = Field(min_length=1, max_length=200)
    body_text: str
    title: str | None = Field(default=None, max_length=500)


class ChapterSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_version_id: str = Field(min_length=1, max_length=200)
    chapter_id: str = Field(min_length=1, max_length=200)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=0)
    text: str
    expected_text: str
    title: str | None = Field(default=None, max_length=500)


class ChapterAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_version_id: str = Field(min_length=1, max_length=200)
    chapter_id: str | None = None
    chapter_ids: list[str] = Field(default_factory=list)
    after_chapter_id: str | None = None
    title: str = ""
    heading: str = ""
    body_text: str = ""
    target_version_id: str | None = None
    heading_formats: list[str] = Field(default_factory=list)
    custom_formats: list[dict[str, Any]] = Field(default_factory=list)
    author_keywords: list[str] | None = None
    garble_rules: list[dict[str, Any]] | None = None
    selections: list[dict[str, Any]] = Field(default_factory=list)
    conflict_policy: Literal["keep_old", "use_new"] | None = None
    preview_token: str | None = Field(default=None, min_length=1, max_length=200)
    repair_duplicates: bool = False


class VolumePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=0)
    volumes: list[dict[str, Any]]
    coordinate_version: Literal[1, 2] = 1


class VolumeResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: str = Field(min_length=1, max_length=100)
    remote_consent: bool = False
    expected_model_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    expected_search_fingerprint: str = Field(min_length=64, max_length=64)
    base_revision: StrictInt | None = Field(default=None, ge=0)


class PastedContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(max_length=2_000_000)


class VolumePaste(PastedContent):
    contract_version: Literal[1, 2] = 1


class ReferenceRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=0)


class ReferenceSection(ReferenceRevision):
    data: Any


class ReferencePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sections: dict[str, Any] = Field(max_length=20)
    source_version_id: str | None = Field(default=None, min_length=1, max_length=200)


class BookMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    values: dict[str, Any]
    expected_version_id: str = Field(min_length=1, max_length=200)
    expected_revision: StrictInt | None = Field(default=None, ge=0)


class ReferenceConfiguration(ReferenceRevision):
    structure_type: str


class NewCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)


class ChunkPaste(Revision):
    content: str = Field(max_length=2_000_000)


class ChunkCommit(ReferenceRevision):
    sections: list[str]
    comparison_id: str | None = Field(default=None, min_length=1, max_length=100)


class ComparisonApproval(Revision):
    comparison_id: str | None = Field(default=None, min_length=1, max_length=100)


class ModelAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: str = Field(min_length=1, max_length=100)
    remote_consent: bool = False
    expected_model_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


class ReferenceTransform(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: str
    profile_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    remote_consent: bool = False
    expected_model_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


class ReferenceRestore(ReferenceRevision):
    target_revision: StrictInt = Field(ge=0)


class ReferenceImport(ReferenceRevision):
    text: str = Field(max_length=8_000_000)
    mode: str = "merge"
    title: str = Field(default="导入文本", min_length=1, max_length=200)


class Studio:
    def __init__(self, runtime):
        self.lock = runtime.lock
        self.data_dir = runtime.data_dir
        self._runtime = runtime
        self.documents = DocumentStore(self.data_dir)
        self._volumes = self._reference = self._models = None
        self._skills = self._workflows = self._projects = self._runner = None
        self._conversions = self._ingestions = None
        self._research = self._character_bridge = self._volume_research = self._embedding_downloads = None
        self._index_workbench()

    def _service(self, name, factory):
        with self.lock:
            attribute = "_" + name
            instance = getattr(self, attribute)
            if instance is None:
                instance = factory()
                self._index_workbench()
                setattr(self, attribute, instance)
            return instance

    def _index_workbench(self):
        from .storage_utils import ensure_growth_indexes
        path = self.data_dir / "workbench.sqlite3"
        if path.is_file():
            with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
                ensure_growth_indexes(connection, "workbench")

    @property
    def volumes(self):
        return self._service("volumes", lambda: VolumeStore(self.data_dir, self.documents))

    @property
    def reference(self):
        def create():
            reference = ReferenceAnalysisStore(self.data_dir, documents=self.documents)
            reference.write_lock = self.lock
            reference.catalog_sync = lambda connection, document_id, sections: self.character_bridge.sync_bound_from_reference_connection(connection, document_id, sections)
            return reference
        return self._service("reference", create)

    @property
    def character_bridge(self):
        from .character_bridge_v11 import CharacterBridge
        return self._service("character_bridge", lambda: CharacterBridge(self._runtime.store, self.documents, lambda: self.reference))

    @property
    def research(self):
        from .research import ResearchService
        return self._service("research", lambda: ResearchService(self.data_dir, self.models,
            catalog_snapshot=self._runtime.store.catalog_snapshot, catalog_commit=self._runtime.store.apply_research))

    @property
    def volume_research(self):
        from .volume_research import VolumeResearch
        return self._service("volume_research", lambda: VolumeResearch(self.documents, self.volumes, self.models, self.research.sources))

    @property
    def embedding_downloads(self):
        from .embedding_downloads import EmbeddingDownloads
        return self._service("embedding_downloads", lambda: EmbeddingDownloads(self.data_dir, self.models))

    @property
    def models(self):
        return self._service("models", lambda: ModelService(self.data_dir))

    @property
    def skills(self):
        return self._service("skills", lambda: SkillStore(self.data_dir))

    @property
    def workflows(self):
        return self._service("workflows", lambda: WorkflowStore(self.data_dir))

    @property
    def projects(self):
        return self._service("projects", lambda: ProjectStore(self.data_dir))

    @property
    def runner(self):
        def create():
            from .workflows import WorkflowRunner
            return WorkflowRunner(self.data_dir, documents=self.documents, models=self.models,
                                  skills=self.skills, workflows=self.workflows,
                                  materializer=Materializer(self._runtime.store, self.documents))
        return self._service("runner", create)

    @property
    def conversions(self):
        with self.lock:
            if self._conversions is None:
                from .conversions import ConversionStore
                self._conversions = ConversionStore(self.data_dir)
            return self._conversions

    @property
    def ingestions(self):
        with self.lock:
            if self._ingestions is None:
                from .ingestions import IngestionStore
                self._ingestions = IngestionStore(self.data_dir, self.documents, self.conversions)
            return self._ingestions

    def active_threads(self) -> list[threading.Thread]:
        workers = self._runner.active_threads() if self._runner is not None else []
        if self._ingestions is not None:
            workers += self._ingestions.active_threads()
        if self._conversions is not None:
            workers += self._conversions.active_threads()
        if self._research is not None:
            workers += self._research.active_threads()
        if self._volume_research is not None:
            workers += self._volume_research.active_threads()
        if self._embedding_downloads is not None:
            workers += self._embedding_downloads.active_threads()
        return workers

    def require_idle(self):
        if self._embedding_downloads is not None and self._embedding_downloads.has_active_runs():
            raise DataError(409, "请先完成或取消正在下载的 embedding 模型。")
        if self._volume_research is not None and self._volume_research.has_active_runs():
            raise DataError(409, "请先完成或取消正在运行的分卷检索。")
        if self._research is not None and self._research.has_active_runs():
            raise DataError(409, "请先完成或暂停正在运行的 AI 更新，再导出项目。")
        if self._ingestions is not None and any(item["status"] in {"queued", "running"} for item in self.ingestions.list()):
            raise DataError(409, "请先完成或取消正在处理的全文上传，再导出项目。")
        if self.runner.has_active_runs():
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


def _catalog_document_metadata(runtime, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach catalog labels in one small query without hydrating text/analysis."""
    identifiers = list(dict.fromkeys(item["work_id"] for item in items if item.get("work_id")))
    if not identifiers:
        return items
    import json
    with runtime.store.connect() as connection:
        rows = connection.execute(
            "SELECT id,json_extract(values_json,'$.类别') category,json_extract(values_json,'$.作者') author "
            "FROM works WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(identifiers),))
        labels = {row["id"]: {"work_category": row["category"] or "", "catalog_author": row["author"] or ""} for row in rows}
    return [{**item, **labels.get(item.get("work_id"), {})} for item in items]


def install_studio_routes(application, runtime):
    router = APIRouter(prefix="/api/studio", tags=["studio"])

    @router.get("/documents")
    def documents():
        return {"items": _catalog_document_metadata(runtime, runtime.studio.documents.list_documents())}

    @router.get("/ingestions")
    def ingestions():
        return {"items": runtime.studio.ingestions.list()}

    @router.post("/ingestions", status_code=201)
    async def ingest_document(file: Annotated[UploadFile, File()], title: Annotated[str, Form()] = "",
                              encoding: Annotated[str, Form()] = "auto", work_id: Annotated[str | None, Form()] = None,
                              mode: Annotated[str, Form()] = "new", target_document_id: Annotated[str | None, Form()] = None,
                              base_version_id: Annotated[str | None, Form()] = None, options: Annotated[str, Form()] = "{}",
                              author: Annotated[str, Form()] = "", earliest_update_at: Annotated[str, Form()] = "",
                              book_type: Annotated[str, Form()] = ""):
        import json
        raw = await _upload(file, MAX_UPLOAD_BYTES)
        try:
            settings = json.loads(options)
        except (ValueError, TypeError):
            raise DataError(422, "转换设置必须是 JSON 对象。") from None
        if not isinstance(settings, dict):
            raise DataError(422, "转换设置必须是 JSON 对象。")
        def create():
            with runtime.studio.lock:
                if work_id:
                    runtime.store.get_record("works", work_id)
                item = runtime.studio.ingestions.create(file.filename or "upload.txt", raw, title=title,
                    encoding=encoding, work_id=work_id or None, mode=mode, target_document_id=target_document_id or None,
                    base_version_id=base_version_id or None, conversion_options=settings,
                    book_metadata={key: value for key, value in {"author": author, "earliest_update_at": earliest_update_at,
                        "book_type": book_type}.items() if value.strip()} or None)
                return runtime.studio.ingestions.start(item["id"])
        return await run_in_threadpool(create)

    @router.get("/ingestions/{identifier}")
    def ingestion(identifier: str):
        return runtime.studio.ingestions.get(identifier)

    @router.post("/ingestions/{identifier}/cancel")
    def cancel_ingestion(identifier: str):
        return runtime.studio.ingestions.cancel(identifier)

    @router.post("/ingestions/{identifier}/retry")
    def retry_ingestion(identifier: str):
        with runtime.studio.lock:
            item = runtime.studio.ingestions.get(identifier)
            if item.get("options", {}).get("work_id"):
                runtime.store.get_record("works", item["options"]["work_id"])
            return runtime.studio.ingestions.retry(identifier)

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
        return _catalog_document_metadata(runtime, [runtime.studio.documents.get_document(document_id)])[0]

    @router.get("/documents/{document_id}/book-metadata")
    def book_metadata(document_id: str):
        return runtime.studio.documents.get_book_metadata(document_id)

    @router.put("/documents/{document_id}/book-metadata")
    def update_book_metadata(document_id: str, body: BookMetadataUpdate):
        with runtime.studio.lock:
            return runtime.studio.documents.update_book_metadata(document_id, body.values,
                expected_version_id=body.expected_version_id, expected_revision=body.expected_revision)

    @router.get("/documents/{document_id}/chapter-files")
    def chapter_files(document_id: str, verify: bool = False):
        return runtime.studio.documents.storage_status(document_id, verify=verify)

    @router.post("/documents/{document_id}/chapter-files")
    def synchronize_chapter_files(document_id: str):
        with runtime.studio.lock:
            return runtime.studio.documents.materialize_chapters(document_id)

    @router.get("/documents/{document_id}/export/chapters")
    def export_chapter_files(document_id: str, version_id: str | None = None):
        metadata = runtime.studio.documents.get_book_metadata(document_id)
        raw = runtime.studio.documents.chapter_archive(document_id, version_id)
        return _file_response(raw, f"{metadata['book_title']}-chapters.zip", "application/zip")

    @router.get("/documents/{document_id}/versions/{version_id}")
    def version(document_id: str, version_id: str, include_text: bool = True, summary: bool = False):
        if summary:
            return runtime.studio.documents.get_version_summary(document_id, version_id)
        if not include_text:
            return runtime.studio.documents.get_version_navigation(document_id, version_id)
        return runtime.studio.documents.get_version(document_id, version_id)

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

    @router.get("/documents/{document_id}/sources")
    def document_sources(document_id: str):
        return {"items": runtime.studio.documents.list_sources(document_id)}

    @router.get("/documents/{document_id}/sources/{source_id}/download")
    def document_source(document_id: str, source_id: str):
        raw, filename = runtime.studio.documents.get_source_original(document_id, source_id)
        return _file_response(raw, filename, "application/octet-stream")

    @router.post("/documents/{document_id}/append", status_code=201)
    async def append_document(document_id: str, file: Annotated[UploadFile, File()],
                              base_version_id: Annotated[str, Form()], encoding: Annotated[str, Form()] = "auto"):
        raw = await _upload(file, MAX_UPLOAD_BYTES)
        def save():
            with runtime.studio.lock:
                return runtime.studio.documents.append(document_id, base_version_id, file.filename or "append.txt", raw, encoding=encoding)
        return await run_in_threadpool(save)

    @router.get("/documents/{document_id}/versions/{version_id}/chapters")
    def chapter_index(document_id: str, version_id: str):
        return runtime.studio.documents.chapter_index(document_id, version_id)

    @router.get("/documents/{document_id}/versions/{version_id}/findings")
    def findings(document_id: str, version_id: str, chapter_id: str | None = None,
                 offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int | None, Query(ge=1, le=10000)] = None):
        return runtime.studio.documents.findings(document_id, version_id, chapter_id=chapter_id, start=offset, limit=limit)

    @router.get("/documents/{document_id}/versions/{version_id}/chapters/{chapter_id}")
    def chapter(document_id: str, version_id: str, chapter_id: str,
                start: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100000)] = 20000,
                body_only: bool = False):
        return runtime.studio.documents.get_chapter(document_id, version_id, chapter_id, start=start, limit=limit, body_only=body_only)

    @router.post("/documents/{document_id}/chapters/edit", status_code=201)
    def edit_chapter(document_id: str, body: ChapterEdit, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.edit_chapter(document_id, body.base_version_id, body.chapter_id, body.body_text, title=body.title)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/edit-slice", status_code=201)
    def edit_chapter_slice(document_id: str, body: ChapterSlice, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.edit_chapter_slice(document_id, body.base_version_id, body.chapter_id,
                body.start, body.end, body.text, body.expected_text, title=body.title)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/resegment/preview")
    def preview_resegment(document_id: str, body: ChapterAction):
        with runtime.studio.lock:
            return runtime.studio.documents.preview_resegment(
                document_id, body.base_version_id, body.heading_formats, body.custom_formats,
                repair_duplicates=body.repair_duplicates)

    @router.post("/documents/{document_id}/chapters/resegment", status_code=201)
    def resegment(document_id: str, body: ChapterAction, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.resegment(
                document_id, body.base_version_id, body.heading_formats, body.custom_formats,
                conflict_policy=body.conflict_policy, preview_token=body.preview_token,
                repair_duplicates=body.repair_duplicates, require_confirmation=True)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/clean", status_code=201)
    def clean_chapters(document_id: str, body: ChapterAction, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.apply_chapter_cleaning(document_id, body.base_version_id, body.selections)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/rename", status_code=201)
    def rename_chapter(document_id: str, body: ChapterAction, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.rename_chapter(document_id, body.base_version_id, body.chapter_id, body.title)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/insert", status_code=201)
    def insert_chapter(document_id: str, body: ChapterAction, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.insert_chapter(document_id, body.base_version_id, body.after_chapter_id, body.heading or body.title, body.body_text)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/delete", status_code=201)
    def delete_chapters(document_id: str, body: ChapterAction, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.delete_chapters(document_id, body.base_version_id, body.chapter_ids)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/restore", status_code=201)
    def restore_version(document_id: str, body: ChapterAction, summary: bool = False):
        if not body.target_version_id:
            raise DataError(422, "请选择要恢复的全文版本。")
        with runtime.studio.lock:
            result = runtime.studio.documents.restore_version(document_id, body.base_version_id, body.target_version_id)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.post("/documents/{document_id}/chapters/settings", status_code=201)
    def chapter_settings(document_id: str, body: ChapterAction, summary: bool = False):
        with runtime.studio.lock:
            result = runtime.studio.documents.update_preprocess_settings(document_id, body.base_version_id, author_keywords=body.author_keywords, garble_rules=body.garble_rules)
            return runtime.studio.documents.version_summary(result) if summary else result

    @router.get("/documents/{document_id}/versions/{version_id}/volumes")
    def volumes(document_id: str, version_id: str, coordinate_version: Annotated[int, Query(ge=1, le=2)] = 1):
        return runtime.studio.volumes.get(document_id, version_id, coordinate_version=coordinate_version)

    @router.put("/documents/{document_id}/versions/{version_id}/volumes")
    def save_volumes(document_id: str, version_id: str, body: VolumePlan):
        with runtime.studio.lock:
            return runtime.studio.volumes.save(document_id, version_id, body.volumes, body.expected_revision, coordinate_version=body.coordinate_version)

    @router.get("/documents/{document_id}/versions/{version_id}/volumes/prompt")
    def volume_prompt(document_id: str, version_id: str):
        return runtime.studio.volumes.web_prompt(document_id, version_id)

    @router.post("/documents/{document_id}/versions/{version_id}/volumes/detect")
    def detect_volume_plan(document_id: str, version_id: str, coordinate_version: Annotated[int, Query(ge=1, le=2)] = 1):
        return runtime.studio.volumes.detect(document_id, version_id, coordinate_version=coordinate_version)

    @router.post("/documents/{document_id}/versions/{version_id}/volumes/paste")
    def paste_volume_plan(document_id: str, version_id: str, body: VolumePaste):
        count = runtime.studio.documents.get_version_summary(document_id, version_id)["chapter_count"]
        from .volume_research import parse_web_volume_reply
        if body.contract_version == 2:
            current = runtime.studio.volumes.web_prompt(document_id, version_id)
            return parse_web_volume_reply(body.content, current["chapter_count"], expected_request_id=current["request_id"], title=current["title"], author=current["author"])
        return parse_web_volume_reply(body.content, count)

    @router.post("/documents/{document_id}/versions/{version_id}/volumes/research", status_code=202)
    def research_volumes(document_id: str, version_id: str, body: VolumeResearchRequest):
        with runtime.studio.lock:
            return runtime.studio.volume_research.create(document_id, version_id, **body.model_dump())

    @router.get("/documents/{document_id}/versions/{version_id}/volumes/research/{job_id}")
    def volume_research_job(document_id: str, version_id: str, job_id: str):
        return runtime.studio.volume_research.get(job_id, document_id, version_id)

    @router.post("/documents/{document_id}/versions/{version_id}/volumes/research/{job_id}/cancel")
    def cancel_volume_research(document_id: str, version_id: str, job_id: str):
        return runtime.studio.volume_research.cancel(job_id, document_id, version_id)

    @router.post("/documents/{document_id}/versions/{version_id}/volumes/model")
    def model_volume_plan(document_id: str, version_id: str, body: ModelAction):
        # ModelService captures the request in the same usage ledger as extraction.
        return runtime.studio.volumes.model_detect(document_id, version_id, runtime.studio.models, **body.model_dump())

    @router.get("/documents/{document_id}/versions/{version_id}/download")
    def cleaned(document_id: str, version_id: str, format: str = "raw"):
        if format not in {"raw", "clean"}:
            raise DataError(422, "导出格式请选择 raw 或 clean。")
        if format == "clean":
            text = runtime.studio.documents.formatted_export(document_id, version_id)
            document = runtime.studio.documents.get_document(document_id)
            title = document.get("title") or Path(document["filename"]).stem
            title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title).strip(" .")[:160] or "正文"
            if title.lower().endswith(".txt"):
                title = title[:-4] or "正文"
            return _file_response(text.encode("utf-8-sig"), f"{title}.txt", "text/plain; charset=utf-8")
        result = runtime.studio.documents.get_version(document_id, version_id)
        return _file_response(result["text"].encode("utf-8-sig"), f"cleaned-{version_id[:8]}.txt", "text/plain; charset=utf-8")

    @router.get("/jobs")
    def jobs(summary: bool = False, document_id: str | None = None, version_id: str | None = None,
             limit: Annotated[int | None, Query(ge=1, le=1000)] = None):
        if summary:
            return {"items": runtime.studio.runner.list_run_summaries(document_id=document_id, version_id=version_id, limit=limit or 100)}
        items = runtime.studio.runner.list_runs()
        if document_id is not None:
            items = [item for item in items if item["document_id"] == document_id]
        if version_id is not None:
            items = [item for item in items if item["version_id"] == version_id]
        return {"items": items[:limit] if limit is not None else items}

    @router.get("/documents/{document_id}/reference")
    def reference(document_id: str):
        return runtime.studio.reference.get(document_id)

    @router.get("/documents/{document_id}/reference/projection")
    def project_reference(document_id: str, chapter: Annotated[int | None, Query(ge=1)] = None,
                          progress: Annotated[float | None, Query(ge=0, le=100)] = None,
                          schema_version: Annotated[int | None, Query(ge=2, le=4)] = None):
        state = runtime.studio.reference.get(document_id)
        events = state["sections"].get("plot", {}).get("events", [])
        sources = {item.get("source_version_id") for item in events if isinstance(item, dict) and item.get("source_version_id")}
        # A saved volume plan belongs to its exact text version. Mixed-version
        # analysis keeps each event's recorded volume rather than applying a
        # new plan to older chapter numbers.
        volumes = None
        if len(sources) == 1:
            version_id = next(iter(sources))
            volumes = runtime.studio.volumes.get(document_id, version_id)["volumes"]
        return runtime.studio.reference.project(document_id, chapter, volumes=volumes,
                                               target_version=schema_version, progress=progress)

    @router.post("/documents/{document_id}/reference/preview")
    def preview_reference(document_id: str, body: ReferencePreview):
        from .analysis_projection import project_sections
        from .reference_analysis import _bounded, _dump
        runtime.studio.documents.get_document(document_id)
        _bounded(body.sections)
        _dump(body.sections)
        sources = {}
        identifiers = set()
        def source_ids(value):
            if isinstance(value, dict):
                if isinstance(value.get("source_version_id"), str) and value["source_version_id"]:
                    identifiers.add(value["source_version_id"])
                for item in value.values(): source_ids(item)
            elif isinstance(value, list):
                for item in value: source_ids(item)
        source_ids(body.sections)
        if body.source_version_id:
            identifiers.add(body.source_version_id)
        for identifier in identifiers:
            sources[identifier] = runtime.studio.documents.get_version(document_id, identifier)
        return {"sections": project_sections(body.sections, target_version=4, source_versions=sources)}

    @router.post("/reference-collections", status_code=201)
    def new_collection(body: NewCollection):
        with runtime.studio.lock:
            document = runtime.studio.documents.create_collection(body.title)
            runtime.studio.reference.configure(document["id"], "setting_collection", 0)
            return document

    @router.post("/documents/{document_id}/reference/prepare-source", status_code=201)
    def prepare_reference_source(document_id: str, body: ReferenceRevision):
        with runtime.studio.lock:
            source = runtime.studio.reference.build_source(document_id, body.expected_revision)
            if source["structure_type"] != "setting_collection":
                raise DataError(422, "只有纯设定资料集需要准备条目正文。")
            document = runtime.studio.documents.get_document(document_id)
            base = runtime.studio.documents.get_version(document_id)
            text = source["text"]
            if base["text"] == text:
                return {"document": document, "version": base, "reference_revision": source["reference_revision"]}
            version = runtime.studio.documents.create_literal_version(document_id, base["id"], text)
            return {"document": runtime.studio.documents.get_document(document_id), "version": version,
                    "reference_revision": source["reference_revision"]}

    @router.get("/documents/{document_id}/reference/export")
    def export_reference_entries(document_id: str):
        return _file_response(runtime.studio.reference.export_collection(document_id), "InkReference-collection.txt", "text/plain; charset=utf-8")

    @router.post("/documents/{document_id}/reference/import")
    def import_reference_entries(document_id: str, body: ReferenceImport):
        with runtime.studio.lock:
            return runtime.studio.reference.import_collection(document_id, body.text, body.expected_revision, mode=body.mode, title=body.title)

    @router.get("/documents/{document_id}/reference/history")
    def reference_history(document_id: str):
        return {"items": runtime.studio.reference.list_history(document_id)}

    @router.post("/documents/{document_id}/reference/restore")
    def restore_reference(document_id: str, body: ReferenceRestore):
        with runtime.studio.lock:
            return runtime.studio.reference.restore(document_id, body.target_revision, body.expected_revision)

    @router.put("/documents/{document_id}/reference")
    def configure_reference(document_id: str, body: ReferenceConfiguration):
        with runtime.studio.lock:
            return runtime.studio.reference.configure(document_id, body.structure_type, body.expected_revision)

    @router.post("/documents/{document_id}/reference/transform")
    def transform_reference(document_id: str, body: ReferenceTransform):
        return runtime.studio.runner.reference_transform(document_id, **body.model_dump())

    @router.put("/documents/{document_id}/reference/{section}")
    def save_reference_section(document_id: str, section: str, body: ReferenceSection):
        with runtime.studio.lock:
            return runtime.studio.reference.save_section(document_id, section, body.data, body.expected_revision)

    @router.post("/jobs", status_code=201)
    def create_job(body: NewRun):
        with runtime.studio.lock:
            result = runtime.studio.runner.create_run(body.document_id, body.version_id, workflow_id=body.workflow_id, workflow_version=body.workflow_version, profile_id=body.profile_id, remote_consent=body.remote_consent, expected_model_fingerprint=body.expected_model_fingerprint, expected_stage_fingerprints=body.expected_stage_fingerprints, manual=body.manual, chapter_ranges=[item.model_dump() for item in body.chapter_ranges] if body.chapter_ranges is not None else None, reuse_existing=body.reuse_existing, interaction_mode=body.interaction_mode)
            return runtime.studio.runner.start(result["id"], remote_consent=body.remote_consent) if body.start and not body.manual and result["status"] == "queued" else result

    @router.post("/jobs/comparisons", status_code=201)
    def create_comparison(body: ComparisonCreate):
        with runtime.studio.lock:
            return runtime.studio.runner.create_comparison(**body.model_dump())

    @router.get("/jobs/comparisons")
    def list_comparisons(document_id: str | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 100):
        return {"items": runtime.studio.runner.list_comparisons(document_id=document_id, limit=limit)}

    @router.get("/jobs/comparisons/{comparison_id}")
    def get_comparison(comparison_id: str):
        return runtime.studio.runner.get_comparison(comparison_id)

    @router.post("/jobs/comparisons/{comparison_id}/select")
    def select_comparison(comparison_id: str, body: ComparisonSelection):
        with runtime.studio.lock:
            return runtime.studio.runner.select_comparison(comparison_id, **body.model_dump())

    @router.get("/jobs/{run_id}")
    def job(run_id: str, summary: bool = False):
        if summary:
            items = runtime.studio.runner.list_run_summaries(run_id=run_id, limit=1)
            if not items:
                raise DataError(404, "提取任务不存在")
            return items[0]
        return runtime.studio.runner.get_run(run_id)

    @router.get("/jobs/{run_id}/chunks/{chunk_id}/prompt")
    def chunk_prompt(run_id: str, chunk_id: str):
        return runtime.studio.runner.chunk_prompt(run_id, chunk_id)

    @router.get("/jobs/{run_id}/chunks/{chunk_id}/template")
    def manual_chunk_template(run_id: str, chunk_id: str):
        from .manual_templates import extraction_template
        return extraction_template(runtime.studio.runner, run_id, chunk_id)

    @router.post("/jobs/{run_id}/chunks/{chunk_id}/paste")
    def paste_chunk(run_id: str, chunk_id: str, body: ChunkPaste):
        with runtime.studio.lock:
            return runtime.studio.runner.paste_chunk(run_id, chunk_id, body.content, body.expected_revision)

    @router.post("/jobs/{run_id}/chunks/{chunk_id}/start")
    def start_chunk(run_id: str, chunk_id: str, body: ConnectionConsent = ConnectionConsent()):
        with runtime.studio.lock:
            return runtime.studio.runner.start_chunk(run_id, chunk_id, remote_consent=body.remote_consent)

    @router.post("/jobs/{run_id}/chunks/{chunk_id}/commit")
    def commit_chunk(run_id: str, chunk_id: str, body: ChunkCommit):
        with runtime.studio.lock:
            runtime.studio.runner.assert_comparison_selection(run_id, body.comparison_id)
            return runtime.studio.reference.commit_candidate(runtime.studio.runner.get_run(run_id), chunk_id, body.sections, body.expected_revision)

    @router.post("/jobs/{run_id}/resume")
    def resume_job(run_id: str, body: ConnectionConsent = ConnectionConsent()):
        with runtime.studio.lock:
            return runtime.studio.runner.resume(run_id, remote_consent=body.remote_consent)

    @router.post("/jobs/{run_id}/start")
    def start_job(run_id: str, body: ConnectionConsent = ConnectionConsent()):
        with runtime.studio.lock:
            return runtime.studio.runner.start(run_id, remote_consent=body.remote_consent)

    @router.post("/jobs/{run_id}/cancel")
    def cancel_job(run_id: str):
        return runtime.studio.runner.cancel(run_id)

    @router.post("/jobs/{run_id}/pause")
    def pause_job(run_id: str):
        return runtime.studio.runner.pause(run_id)

    @router.post("/jobs/{run_id}/terminate")
    def terminate_job(run_id: str):
        return runtime.studio.runner.terminate(run_id)

    @router.put("/jobs/{run_id}/results")
    def review_job(run_id: str, body: Review):
        with runtime.studio.lock:
            return runtime.studio.runner.edit_result(run_id, body.results, body.expected_revision)

    @router.post("/jobs/{run_id}/approve")
    def approve_job(run_id: str, body: ComparisonApproval):
        with runtime.studio.lock:
            return runtime.studio.runner.approve(run_id, body.expected_revision, body.comparison_id)

    @router.get("/models")
    def models():
        return {"items": runtime.studio.models.list_profiles()}

    @router.get("/models/security")
    def model_security():
        return runtime.studio.models.security_status()

    @router.post("/models/test-draft")
    def test_model_draft(body: dict[str, Any]):
        with runtime.studio.lock:
            return runtime.studio.models.test_draft(body)

    @router.post("/models/discover")
    def discover_models(body: dict[str, Any]):
        with runtime.studio.lock:
            return runtime.studio.models.discover_models(body)

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

    @router.delete("/models/{profile_id}/credentials")
    def clear_model_credentials(profile_id: str):
        with runtime.studio.lock:
            return runtime.studio.models.clear_credentials(profile_id)

    @router.post("/models/{profile_id}/test")
    def test_model(profile_id: str, body: ConnectionConsent = ConnectionConsent()):
        with runtime.studio.lock:
            return runtime.studio.models.test_profile(profile_id, allow_remote=body.remote_consent)

    @router.get("/usage")
    def usage(run_id: str | None = None, limit: Annotated[int | None, Query(ge=1, le=200)] = None, cursor: str | None = None):
        page = runtime.studio.models.list_usage_page(run_id, limit=limit or 50, cursor=cursor) if limit is not None or cursor is not None else {"items": runtime.studio.models.list_usage(run_id)}
        return {**page, "summary": runtime.studio.models.usage_summary(run_id)}

    @router.get("/skills")
    def skills():
        return {"items": runtime.studio.skills.list()}

    @router.post("/skills", status_code=201)
    def new_skill(body: dict[str, Any]):
        with runtime.studio.lock:
            return runtime.studio.skills.save(body)

    @router.post("/skills/upload", status_code=201)
    async def upload_skill(file: Annotated[UploadFile, File()], contract_version: Annotated[int | None, Query(ge=2, le=4)] = None):
        raw = await _upload(file, 2 * 1024 * 1024)
        def save():
            with runtime.studio.lock:
                return runtime.studio.skills.upload(file.filename or "skill.json", raw, require_contract_version=contract_version)
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
