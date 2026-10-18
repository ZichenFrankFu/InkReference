"""HTTP contracts for local diagnostics, explicit repair review and recovery."""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .quick_repair import MAX_REPLY, QuickRepair


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    description: str = Field(min_length=1, max_length=6000)
    topic: str = "app"
    frontend_events: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    context: str = Field(default="", max_length=12_000)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reply: str = Field(max_length=MAX_REPLY)
    session_id: str | None = Field(default=None, min_length=1, max_length=100)


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preview_id: str = Field(min_length=1, max_length=100)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=100)


class CacheRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str | None = Field(default=None, min_length=1, max_length=200)


def repair_service(application) -> QuickRepair:
    return application.state.quick_repair()


def install_repair_routes(application, runtime):
    router = APIRouter(prefix="/api/studio/repair")
    lock = threading.RLock()
    service = None

    def get_service():
        nonlocal service
        with lock:
            if service is None:
                def rebuild(document_id, version_id):
                    return runtime.studio.documents.rebuild_read_views(document_id, version_id)
                service = QuickRepair(runtime.data_dir, cache_rebuilder=rebuild)
            return service

    application.state.quick_repair = get_service

    def shutdown():
        if service is not None:
            service.shutdown()
    application.state.shutdown_quick_repair = shutdown
    application.state.active_quick_repair_threads = lambda: service.active_threads() if service is not None else []

    @router.get("/capabilities")
    def capabilities():
        return get_service().capabilities()

    @router.post("/prompt")
    def prompt(body: PromptRequest):
        return get_service().prompt(body.description, body.topic, body.frontend_events, body.context)

    @router.post("/preview")
    def preview(body: PreviewRequest):
        return get_service().preview(body.reply, body.session_id)

    @router.post("/apply")
    def apply(body: ApplyRequest):
        return get_service().apply(body.preview_id)

    @router.get("/operations/{operation_id}")
    def operation(operation_id: str):
        return get_service().operation(operation_id)

    @router.get("/operations")
    def operations():
        return get_service().recent_operations()

    @router.post("/rollback")
    def rollback(body: RollbackRequest):
        return get_service().rollback(body.operation_id)

    @router.post("/export")
    def export(body: ApplyRequest):
        return Response(get_service().export(body.preview_id), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="InkReference-repair.json"'})

    @router.post("/cache-rebuild")
    def cache_rebuild(body: CacheRequest):
        return get_service().rebuild_cache(body.document_id, body.version_id)

    application.include_router(router)
