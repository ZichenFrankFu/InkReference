"""Revision-checked HTTP access to shared character identities and work snapshots."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class CharacterContextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    character: dict[str, Any]
    expected_revision: StrictInt = Field(ge=0)
    expected_catalog_revision: str = Field(min_length=1, max_length=200)
    expected_reference_revision: StrictInt | None = Field(default=None, ge=0)


class CharacterBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    character_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_reference_revision: StrictInt = Field(ge=0)
    expected_catalog_revision: str = Field(min_length=1, max_length=200)


def install_character_routes(application, runtime):
    router = APIRouter(prefix="/api")

    @router.get("/library/revision")
    def revision():
        with runtime.store.connect() as connection:
            return {"revision": runtime.store._catalog_revision(connection)}

    @router.get("/library/characters/{character_id}/contexts")
    def contexts(character_id: str):
        return runtime.studio.character_bridge.contexts(character_id)

    @router.get("/studio/documents/{document_id}/reference/characters/catalog")
    def character_bindings(document_id: str):
        runtime.studio.documents.get_document(document_id)
        with runtime.store.connect() as connection:
            rows = connection.execute("SELECT extracted_character_id,character_id,work_id FROM character_contexts WHERE document_id=? ORDER BY character_id", (document_id,))
            return {"catalog_revision": runtime.store._catalog_revision(connection),
                    "links": [dict(row) for row in rows]}

    @router.put("/library/characters/{character_id}/contexts/{work_id}")
    def save_context(character_id: str, work_id: str, body: CharacterContextUpdate):
        with runtime.studio.lock:
            return runtime.studio.character_bridge.save_context(character_id, work_id, **body.model_dump())

    @router.post("/studio/documents/{document_id}/reference/characters/{extracted_id}/catalog")
    def bind_character(document_id: str, extracted_id: str, body: CharacterBinding):
        with runtime.studio.lock:
            return runtime.studio.character_bridge.import_character(document_id, extracted_id, **body.model_dump())

    application.include_router(router)
