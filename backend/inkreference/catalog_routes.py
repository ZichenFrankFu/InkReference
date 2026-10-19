"""Lazy HTTP bridge between catalog works, cleaned text and reference analysis."""
from __future__ import annotations

import threading
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from .catalog_links import CatalogLinks


class NewCatalogWork(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=1000)


class CatalogLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_work_id: str | None = Field(..., min_length=1, max_length=200)
    expected_version_id: str = Field(min_length=1, max_length=200)
    work_id: str | None = Field(default=None, min_length=1, max_length=200)
    new_work: NewCatalogWork | None = None
    complete: bool = False


def install_catalog_routes(application, runtime):
    router = APIRouter(prefix="/api/studio")
    service = None
    creation_lock = threading.RLock()

    def catalog():
        nonlocal service
        with creation_lock:
            if service is None:
                service = CatalogLinks(runtime.store, runtime.studio.documents, lambda: runtime.studio.reference)
            return service

    application.state.catalog_links = catalog

    @router.get("/catalog/works")
    def works(q: str = "", offset: Annotated[int, Query(ge=0)] = 0,
              limit: Annotated[int, Query(ge=1, le=200)] = 100):
        return catalog().works(q, offset, limit)

    @router.get("/catalog/works/{work_id}")
    def detail(work_id: str):
        return catalog().detail(work_id)

    @router.get("/catalog/works/{work_id}/analysis")
    def analysis(work_id: str, document_id: str,
                 chapter: Annotated[int | None, Query(ge=0)] = None,
                 progress: Annotated[float | None, Query(ge=0, le=100)] = None,
                 schema_version: Annotated[int | None, Query(ge=2, le=4)] = None):
        return catalog().analysis(work_id, document_id, chapter, progress=progress, schema_version=schema_version)

    @router.get("/documents/{document_id}/catalog")
    def link(document_id: str):
        return catalog().get(document_id)

    @router.put("/documents/{document_id}/catalog")
    def save_link(document_id: str, body: CatalogLinkRequest):
        with runtime.studio.lock:
            return catalog().link(document_id, **body.model_dump())

    application.include_router(router)
