"""Explicit links from the material catalog to versioned text and rich analysis.

Rich facts stay in ReferenceAnalysisStore rather than being copied to works.
The read projection follows InkOctoBot (MIT), 51c82b6f01f6a5b5210e1bae9302be257b3b8083,
CharactersAndSettingsExtraction.tsx: own experiences win; otherwise events whose
subject equals the person's name supply experiences. Chapter and story clocks
remain separate, and chapter filtering never mutates the approved source.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .database import DataError
from .text_processing import cn2int


def _objects(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _chapter(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"(?:第\s*)?([零〇一二两三四五六七八九十百千万0-9０-９]+)\s*(?:章|回|节|话)?", str(value or ""))
    return cn2int(match[1]) if match else None


def reference_projection(sections: dict, chapter: int | None = None) -> dict:
    from .analysis_projection import project_sections
    return project_sections(sections, chapter)


class CatalogLinks:
    def __init__(self, store, documents, reference):
        self.store, self.documents, self.reference = store, documents, reference
        with documents._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS catalog_completions (
                document_id TEXT PRIMARY KEY, work_id TEXT NOT NULL,
                version_id TEXT NOT NULL, completed_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_completion_work ON catalog_completions(work_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_doc_documents_work ON doc_documents(work_id)")

    @staticmethod
    def _work(record: dict) -> dict:
        return {"id": record["id"], "title": record["values"].get("作品", ""), "revision": record["revision"]}

    def works(self, q: str = "", offset: int = 0, limit: int = 100) -> dict:
        result = self.store.list_records("works", q=q, offset=offset, limit=limit)
        return {**result, "items": [self._work(item) for item in result["items"]]}

    def filter_work_ids(self, *, has_text: bool | None = None, has_analysis: bool | None = None) -> list[str]:
        """Filter metadata before catalog pagination; never hydrate text or facts."""
        with self.store.connect() as db:
            identifiers = {row[0] for row in db.execute("SELECT id FROM works")}
        with self.documents._connection() as db:
            text_ids = {row[0] for row in db.execute("SELECT DISTINCT work_id FROM doc_documents WHERE work_id IS NOT NULL")}
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reference_analysis'").fetchone()
            # Merely opening an empty analysis creates revision 0, not content.
            analysis_ids = {row[0] for row in db.execute("SELECT DISTINCT d.work_id FROM doc_documents d JOIN reference_analysis a ON a.document_id=d.id WHERE d.work_id IS NOT NULL AND a.revision>0")} if exists and has_analysis is not None else set()
        if has_text is not None:
            identifiers = identifiers & text_ids if has_text else identifiers - text_ids
        if has_analysis is not None:
            identifiers = identifiers & analysis_ids if has_analysis else identifiers - analysis_ids
        return sorted(identifiers)

    def get(self, document_id: str) -> dict:
        with self.documents._connection() as db:
            row = db.execute("SELECT id,work_id,latest_version_id FROM doc_documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise DataError(404, "文本不存在")
            completion = db.execute("SELECT version_id,completed_at,work_id FROM catalog_completions WHERE document_id=?", (document_id,)).fetchone()
        work, missing = None, False
        if row["work_id"]:
            try:
                work = self._work(self.store.get_record("works", row["work_id"]))
            except DataError as exc:
                if exc.status_code != 404:
                    raise
                missing = True
        return {"document_id": document_id, "work_id": row["work_id"], "work": work,
                "missing_work": missing, "latest_version_id": row["latest_version_id"],
                "completion": {"version_id": completion[0], "completed_at": completion[1]} if completion and completion[2] == row["work_id"] and not missing else None}

    def link(self, document_id: str, *, expected_work_id: str | None, expected_version_id: str,
             work_id: str | None = None, new_work: dict | None = None, complete: bool = False) -> dict:
        if new_work is not None and work_id is not None:
            raise DataError(422, "请选择已有作品或创建作品")
        if new_work is not None:
            title = str(new_work.get("title", "")).strip()
            if not title or len(title) > 1000:
                raise DataError(422, "作品名称需为 1–1000 个字符")
            work_id = "W" + uuid4().hex
        if complete and not work_id:
            raise DataError(422, "请先选择作品")
        # One SQLite connection commits the work and its explicit link together.
        # No title-based lookup can accidentally merge editions or adaptations.
        with self.store.transaction() as db:
            db.execute("ATTACH DATABASE ? AS texts", (str(self.documents.db_path),))
            row = db.execute("SELECT work_id,latest_version_id FROM texts.doc_documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise DataError(404, "文本不存在")
            if row[0] != expected_work_id or row[1] != expected_version_id:
                raise DataError(409, "文本版本或关联作品已变化，请刷新后重试")
            if new_work is not None:
                self.store._insert_raw(db, "works", {"作品ID": work_id, "作品": title})
            elif work_id is not None:
                self.store._row(db, "works", work_id)
            db.execute("UPDATE texts.doc_documents SET work_id=? WHERE id=?", (work_id, document_id))
            if work_id != row[0] or work_id is None:
                db.execute("DELETE FROM texts.catalog_completions WHERE document_id=?", (document_id,))
            if complete:
                db.execute("INSERT INTO texts.catalog_completions VALUES (?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET work_id=excluded.work_id,version_id=excluded.version_id,completed_at=excluded.completed_at", (document_id, work_id, expected_version_id, datetime.now(timezone.utc).isoformat()))
        return self.get(document_id)

    def detail(self, work_id: str) -> dict:
        work = self.store.get_record("works", work_id)
        with self.documents._connection() as db:
            rows = db.execute("""SELECT d.id,d.title,d.latest_version_id,
                COALESCE(v.characters,(SELECT CAST(json_extract(payload_json,'$.coverage.characters') AS INTEGER)
                    FROM doc_versions WHERE id=d.latest_version_id),length(d.source_text)) AS current_characters,
                c.version_id AS completed_version_id,c.completed_at
                FROM doc_documents d LEFT JOIN doc_version_views v ON v.version_id=d.latest_version_id
                LEFT JOIN catalog_completions c ON c.document_id=d.id AND c.work_id=d.work_id
                WHERE d.work_id=? ORDER BY d.created_at DESC,d.id""", (work_id,)).fetchall()
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reference_analysis'").fetchone()
            documents = []
            for row in rows:
                item = dict(row)
                analysis = db.execute("""SELECT revision,structure_type,updated_at,
                    COALESCE(json_array_length(sections_json,'$.characters'),0),
                    COALESCE(json_array_length(sections_json,'$.settings'),0),
                    (SELECT COUNT(*) FROM json_each(reference_analysis.sections_json,'$.plot.epochs') e,
                     json_each(e.value,'$.periods') p,json_each(p.value,'$.events')) + COALESCE(json_array_length(sections_json,'$.plot.events'),0)
                    FROM reference_analysis WHERE document_id=? AND revision>0""", (row["id"],)).fetchone() if exists else None
                item["analysis"] = None
                if analysis:
                    source_versions = [v[0] for v in db.execute("SELECT DISTINCT source_version_id FROM reference_section_commits WHERE document_id=? AND source_version_id<>''", (row["id"],))]
                    item["analysis"] = {"revision": analysis[0], "structure_type": analysis[1], "updated_at": analysis[2],
                                        "source_version_ids": source_versions,
                                        "stale": any(v != row["latest_version_id"] for v in source_versions),
                                        "counts": {"characters": analysis[3], "settings": analysis[4], "plot": analysis[5]}}
                documents.append(item)
        return {"work_id": work_id, "title": work["values"].get("作品", ""), "revision": work["revision"], "documents": documents,
                "counts": {"documents": len(documents), "analyses": sum(item["analysis"] is not None for item in documents)}}

    def analysis(self, work_id: str, document_id: str, chapter: int | None = None) -> dict:
        self.store.get_record("works", work_id)
        link = self.get(document_id)
        if link["work_id"] != work_id:
            raise DataError(409, "此文本未关联当前作品")
        state = self.reference().get(document_id)
        projection = reference_projection(state["sections"], chapter)
        if chapter is not None:
            state = {**state, "sections": projection}
        return {"work_id": work_id, "document_id": document_id, "latest_version_id": link["latest_version_id"],
                "reference": state, "projection": projection, "chapter": chapter}
