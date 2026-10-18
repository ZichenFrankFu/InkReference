"""Commit an explicitly reviewed analysis with the existing library transaction."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .database import DataError, DataStore, character_version, work_label


class Materializer:
    def __init__(self, store: DataStore, documents):
        self.store, self.documents = store, documents
        with store.transaction() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS analysis_commits (run_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL)")

    def __call__(self, run: dict) -> dict:
        run_id = run["id"]
        document = self.documents.get_document(run["document_id"])
        results = run["results"]

        def digest(kind: str, index: int = 0) -> str:
            return hashlib.sha256(f"{run_id}:{kind}:{index}".encode()).hexdigest()[:16]

        with self.store.transaction() as connection:
            committed = connection.execute("SELECT result_json FROM analysis_commits WHERE run_id=?", (run_id,)).fetchone()
            if committed:
                return json.loads(committed[0])
            # A linked work is never rewritten: its manually maintained fields
            # retain their contents. Relation changes still bump revisions.
            work_id = document.get("work_id")
            created_work = False
            if work_id:
                work = self.store._render(connection, "works", self.store._row(connection, "works", work_id))["values"]
            else:
                work_id = "WAI" + digest("work")
                events = "\n".join(f"{item['name']}：{item['description']}" for item in results.get("events", []))
                work = {
                    "作品ID": work_id, "作品": document.get("title") or document.get("filename") or "未命名作品",
                    "版本与原作关系": "全文分析·" + run_id[:8],
                    "两句话概括": results.get("summary", ""), "剧情补充说明": events,
                }
                self.store._insert_raw(connection, "works", work)
                work = self.store._render(connection, "works", self.store._row(connection, "works", work_id))["values"]
                created_work = True
            character_ids, cliche_ids = [], []
            for index, item in enumerate(results.get("characters", [])):
                identifier = "KAI" + digest("character", index)
                self.store._insert_raw(connection, "characters", {
                    "人物ID": identifier, "作品ID": work_id, "作品": work["作品"],
                    "版本": character_version(work.get("版本与原作关系", "")),
                    "角色": item["name"], "人物补充说明": item["description"],
                })
                character_ids.append(identifier)
            for index, item in enumerate(results.get("cliches", [])):
                identifier = "C" + str(int(digest("cliche", index), 16))
                appearance = work_label(work) + f" ⟦ID:{work_id}⟧"
                self.store._insert_raw(connection, "cliches", {
                    "cliché内容": f"{identifier}｜{item['name']}\n{item['description']}",
                    "出现作品": appearance,
                })
                self.store._sync_cliche(connection, identifier, appearance)
                cliche_ids.append(identifier)
            result = {"work_id": work_id, "created_work": created_work, "character_ids": character_ids, "cliche_ids": cliche_ids}
            evidence = {"document_id": run["document_id"], "version_id": run["version_id"], "results": results}
            connection.execute("INSERT INTO analysis_commits VALUES (?,?,?,?)", (
                run_id, json.dumps(result, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False), datetime.now(timezone.utc).isoformat(),
            ))
            return result
