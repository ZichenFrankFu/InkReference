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

    @staticmethod
    def _committed(row) -> dict:
        try:
            mapping = json.loads(row["result_json"])
            evidence = json.loads(row["evidence_json"])
            if not isinstance(mapping, dict) or not isinstance(evidence, dict) or not isinstance(evidence["results"], dict):
                raise ValueError("invalid commit")
            return {"materialization": mapping, "results": evidence["results"],
                    "document_id": evidence["document_id"], "version_id": evidence["version_id"],
                    "created_at": row["created_at"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(409, "已有入库记录无法校验，请检查项目备份；未覆盖或重复写入素材。") from exc

    def lookup(self, run_id: str) -> dict | None:
        """Read the authoritative commit, independently of workbench status."""
        with self.store.connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM analysis_commits WHERE run_id=?", (run_id,)).fetchone()
            return self._committed(row) if row is not None else None

    @staticmethod
    def _canonical(value) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise DataError(422, "审核结果必须是有限的 JSON 数据。") from exc

    def __call__(self, run: dict) -> dict:
        run_id = run["id"]
        results = run["results"]
        evidence = {"document_id": run["document_id"], "version_id": run["version_id"], "results": results}
        canonical_evidence = self._canonical(evidence)

        def digest(kind: str, index: int = 0) -> str:
            return hashlib.sha256(f"{run_id}:{kind}:{index}".encode()).hexdigest()[:16]

        with self.store.transaction() as connection:
            committed = connection.execute("SELECT * FROM analysis_commits WHERE run_id=?", (run_id,)).fetchone()
            if committed:
                original = self._committed(committed)
                original_evidence = {key: original[key] for key in ("document_id", "version_id", "results")}
                if canonical_evidence != self._canonical(original_evidence):
                    raise DataError(409, "此任务已按另一审核版本入库，不能用修改后的结果复用旧入库记录；请恢复已提交版本或创建新任务。")
                return original["materialization"]
            document = self.documents.get_document(run["document_id"])
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
            connection.execute("INSERT INTO analysis_commits VALUES (?,?,?,?)", (
                run_id, json.dumps(result, ensure_ascii=False), canonical_evidence, datetime.now(timezone.utc).isoformat(),
            ))
            return result
