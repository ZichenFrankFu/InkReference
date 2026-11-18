"""Reviewed literal body replacement over immutable document versions.

The preview is stateless and bound to the exact version and replacement
parameters. Applying it uses the document store's single transactional CAS.
Existing chapter headings, boundary separators and original bytes are retained.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
from typing import Callable, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .chapter_layout import chapter_body_span, chapter_label
from .database import DataError
from .documents import DocumentStore, _prepare


class ReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_version_id: str = Field(min_length=1, max_length=200)
    search: str = Field(min_length=1)
    replacement: str
    scope: Literal["all", "chapter"] = "all"
    chapter_id: str | None = Field(default=None, min_length=1, max_length=200)
    body_only: Literal[True] = True


class ApplyReplaceRequest(ReplaceRequest):
    preview_id: str = Field(pattern=r"^[0-9a-f]{64}$")


def _validate(search: str, replacement: str, scope: str, chapter_id: str | None,
              body_only: bool) -> None:
    DocumentStore._valid_text(search)
    DocumentStore._valid_text(replacement)
    if not search:
        raise DataError(422, "请输入查找文本")
    if not isinstance(scope, str) or scope not in {"all", "chapter"}:
        raise DataError(422, "替换范围无效")
    if body_only is not True:
        raise DataError(422, "批量替换仅支持章节正文")
    if scope == "chapter" and (not isinstance(chapter_id, str) or not chapter_id):
        raise DataError(422, "请选择替换章节")
    if scope == "all" and chapter_id is not None:
        raise DataError(422, "全文替换不应指定章节")


def _candidate(store: DocumentStore, document_id: str, base_version_id: str,
               search: str, replacement: str, *, scope: str, chapter_id: str | None,
               body_only: bool) -> tuple[dict, dict, list[dict]]:
    _validate(search, replacement, scope, chapter_id, body_only)
    store._assert_current(document_id, base_version_id)
    base = store.get_version(document_id, base_version_id)
    selected = [chapter for chapter in base["chapters"]
                if (scope == "all" or chapter["id"] == chapter_id)
                and not chapter.get("placeholder") and not chapter.get("gap_range")]
    if scope == "chapter" and not selected:
        # Virtual missing chapters have no source body. They are legitimate
        # navigation entries, but replacement must never materialize them.
        placeholder = next((chapter for chapter in store.chapter_index(document_id, base_version_id)["chapters"]
                            if chapter["id"] == chapter_id and chapter.get("placeholder")), None)
        if placeholder is None:
            raise DataError(404, "章节不存在")
    affected = []
    replacements = []
    for chapter in selected:
        left, right = chapter_body_span(chapter)
        body = chapter["text"][left:right]
        count = body.count(search)
        if not count:
            continue
        affected.append({"chapter_id": chapter["id"], "title": chapter_label(chapter),
                         "match_count": count})
        replacements.append({"chapter_id": chapter["id"], "start": left, "end": right,
                             "text": body.replace(search, replacement)})
    preview = {"document_id": document_id, "base_version_id": base_version_id,
               "scope": scope, "chapter_id": chapter_id, "body_only": True,
               "match_count": sum(chapter["match_count"] for chapter in affected),
               "affected_chapter_count": len(affected), "chapters": affected,
               "input_characters": len(base["text"]),
               "output_characters": len(base["text"]) + sum(
                   len(item["text"]) - (item["end"] - item["start"]) for item in replacements)}
    identity = {"format": "InkReference-literal-replace-v1", "version_sha256": base["sha256"],
                "search": search, "replacement": replacement, **preview}
    preview["preview_id"] = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return base, preview, replacements


def preview_replace(store: DocumentStore, document_id: str, base_version_id: str,
                    search: str, replacement: str, *, scope: str = "all",
                    chapter_id: str | None = None, body_only: bool = True) -> dict:
    """Count non-overlapping exact matches without creating a version."""
    return _candidate(store, document_id, base_version_id, search, replacement,
                      scope=scope, chapter_id=chapter_id, body_only=body_only)[1]


def _prepared(store: DocumentStore, base: dict, replacements: list[dict]) -> dict:
    by_id = {item["chapter_id"]: item for item in replacements}
    chapters = []
    parts = []
    offset_delta = 0
    for chapter in base["chapters"]:
        current = dict(chapter)
        edit = by_id.get(chapter["id"])
        text = chapter["text"]
        if edit is not None:
            text = text[:edit["start"]] + edit["text"] + text[edit["end"]:]
        delta = len(text) - len(chapter["text"])
        current.update(text=text, start=chapter["start"] + offset_delta,
                       end=chapter["end"] + offset_delta + delta,
                       body_start=chapter["body_start"] + offset_delta)
        current["content"] = text[current["body_start"] - current["start"]:]
        chapters.append(current)
        parts.append(text)
        offset_delta += delta
    updated = "".join(parts)
    if "".join(chapter["text"] for chapter in base["chapters"]) != base["text"]:
        raise DataError(500, "正文覆盖检查失败，未保存不完整版本")
    analysis = {"chapters": chapters, "pattern": base.get("pattern"),
                "fallback_used": base.get("fallback_used", False),
                "heading_formats": base.get("heading_formats", []),
                "candidates": base.get("candidates", []),
                "coverage": {**base["coverage"], "characters": len(updated),
                             "covered_characters": len(updated), "contiguous": True,
                             "complete": True,
                             "reconstructed_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest()}}
    # Keep the pinned directory, including when inserted body text resembles a
    # heading. Literal replacement must not silently resegment the document.
    prepared = _prepare(updated, base["text"], store._version_options(base),
                        normalize=False, chapter_analysis=analysis)
    prepared["source_diagnostics"] = base.get("source_diagnostics", [])
    prepared["_storage_changed_ids"] = list(by_id)
    return prepared


def apply_replace(store: DocumentStore, document_id: str, base_version_id: str,
                  search: str, replacement: str, *, preview_id: str,
                  scope: str = "all", chapter_id: str | None = None,
                  body_only: bool = True) -> dict:
    """Apply the exact reviewed operation as one immutable derived version."""
    base, preview, replacements = _candidate(
        store, document_id, base_version_id, search, replacement,
        scope=scope, chapter_id=chapter_id, body_only=body_only)
    if (not isinstance(preview_id, str) or len(preview_id) != 64
            or any(character not in "0123456789abcdef" for character in preview_id)
            or not hmac.compare_digest(preview_id, preview["preview_id"])):
        raise DataError(409, "替换预览已变化，请重新预览")
    if not preview["match_count"]:
        raise DataError(422, "没有匹配的正文")
    if search == replacement:
        raise DataError(422, "替换文本与查找文本相同")
    prepared = _prepared(store, base, replacements)
    saved = store._commit_prepared(document_id, base, prepared,
                                   change={"stage": "literal_body_replace", "scope": scope,
                                           "chapter_id": chapter_id, "body_only": True,
                                           "count": preview["match_count"],
                                           "affected_chapter_count": preview["affected_chapter_count"],
                                           "preview_id": preview_id})
    return {**store.version_summary(saved), "replacement_preview": preview}


def create_router(store: DocumentStore | Callable[[], DocumentStore], lock=None) -> APIRouter:
    """Create routes using a store instance or the runtime's lazy store getter."""
    router = APIRouter(prefix="/api/studio/documents")

    def get_store() -> DocumentStore:
        return store() if callable(store) else store

    def mutation_lock():
        return (lock() if callable(lock) else lock) if lock is not None else contextlib.nullcontext()

    @router.post("/{document_id}/replace/preview")
    def preview(document_id: str, body: ReplaceRequest):
        return preview_replace(get_store(), document_id, **body.model_dump())

    @router.post("/{document_id}/replace/apply", status_code=201)
    def apply(document_id: str, body: ApplyReplaceRequest):
        with mutation_lock():
            return apply_replace(get_store(), document_id, **body.model_dump())

    return router
