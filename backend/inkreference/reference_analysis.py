"""Editable work-level reference analysis and independent chunk approvals.

Data shapes and merge rules are adapted from InkOctoBot (MIT), commit
51c82b6f01f6a5b5210e1bae9302be257b3b8083:
ui/frontend/src/components/reference/AnalysisEditors.tsx, referenceMerge.ts,
PureSettingPanel.tsx, and reference_pipeline/nlp_stats.py.
The original per-chunk section ledger, name/category merges and weighted style
aggregation are retained; revision checks and immutable approved contributions
prevent retries from doubling facts or overwriting a user's edits.
"""
from __future__ import annotations

import copy
import contextlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .database import DataError

ARRAY_SECTIONS = {"characters", "settings", "setting_features", "raw_entries"}
OBJECT_SECTIONS = {"plot", "style", "rhythm", "opening", "metadata"}
SECTIONS = ARRAY_SECTIONS | OBJECT_SECTIONS
DEFAULT_SECTIONS = {"plot": {"logline": "", "epochs": []}, "characters": [], "settings": [],
                    "style": {}, "rhythm": {}, "opening": {}, "metadata": {}, "setting_features": [], "raw_entries": []}
ANALYSIS_HISTORY_LIMIT = 10


def _dump(value: Any) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(rendered.encode("utf-8")) > 8_000_000:
            raise DataError(413, "分析数据超过 8 MB，请分段保存")
        return rendered
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise DataError(422, "分析内容必须是有限的 UTF-8 JSON 数据") from exc


def _bounded(value: Any, depth: int = 0) -> None:
    if depth > 14:
        raise DataError(422, "分析数据层级过深")
    if isinstance(value, dict):
        if len(value) > 100 or any(not isinstance(key, str) or len(key) > 150 for key in value):
            raise DataError(422, "分析字段无效")
        for child in value.values():
            _bounded(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 10000:
            raise DataError(422, "分析列表超过 10000 项")
        for child in value:
            _bounded(child, depth + 1)
    elif isinstance(value, str):
        if len(value) > 1_000_000:
            raise DataError(422, "分析文本过长")
    elif value is not None and type(value) not in (int, float, bool):
        raise DataError(422, "分析数据类型无效")
    elif type(value) is float and not math.isfinite(value):
        raise DataError(422, "分析数字必须有限")


def validate_section(section: str, data: Any) -> Any:
    if section not in SECTIONS:
        raise DataError(422, "未知分析分区")
    if section in ARRAY_SECTIONS:
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise DataError(422, "此分析分区需为对象数组")
    elif not isinstance(data, dict):
        raise DataError(422, "此分析分区需为对象")
    _bounded(data)
    def objects(value: Any, label: str) -> list[dict]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise DataError(422, f"{label} 需为对象数组")
        return value
    if section == "plot":
        for epoch in objects(data.get("epochs", []), "epochs"):
            for period in objects(epoch.get("periods", []), "periods"):
                objects(period.get("events", []), "events")
    elif section == "characters":
        for person in data:
            for field in ("appearance", "personality", "experiences"):
                for fact in objects(person.get(field, []), field):
                    if not isinstance(fact.get("text", ""), str) or not isinstance(fact.get("chapter", ""), str):
                        raise DataError(422, "角色事实的文本和章节需为字符串")
    elif section == "settings":
        for setting in data:
            objects(setting.get("updates", []), "updates")
    elif section == "style":
        for metric in ("dialogue_ratio", "description_density", "vocab_complexity", "info_density"):
            if metric in data and (type(data[metric]) not in (int, float) or not 0 <= data[metric] <= 1):
                raise DataError(422, f"{metric} 需为 0–1 的数值")
        for signal in objects(data.get("chapter_signals", []), "chapter_signals"):
            info = signal.get("info_density", 0)
            if type(info) not in (int, float) or not 0 <= info <= 1:
                raise DataError(422, "章节信息密度需为 0–1 的数值")
            types = signal.get("chapter_types", [])
            if not isinstance(types, list) or any(not isinstance(item, str) for item in types):
                raise DataError(422, "章节类型需为字符串数组")
            for field in ("hooks", "payoffs"):
                objects(signal.get(field, []), field)
    if section in ARRAY_SECTIONS:
        required = {"characters": ("name",), "settings": ("title",),
                    "setting_features": ("title",), "raw_entries": ("title", "content")}[section]
        for item in data:
            if any(not isinstance(item.get(key), str) or not item[key].strip() for key in required):
                raise DataError(422, "分析条目缺少名称或正文")
    _dump(data)
    return copy.deepcopy(data)


def validate_reference(data: Any, source: str | None = None, *, require_evidence: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) - SECTIONS:
        raise DataError(422, "reference 必须只含已定义的分析分区")
    result = {section: validate_section(section, value) for section, value in data.items()}
    if require_evidence:
        facts = [item for section in ("characters", "settings", "setting_features") for item in result.get(section, [])]
        facts.extend(event for epoch in result.get("plot", {}).get("epochs", []) for period in epoch.get("periods", []) for event in period.get("events", []))
        if any(not isinstance(fact.get("evidence"), str) or not fact["evidence"].strip() for fact in facts):
            raise DataError(422, "模型分析事实必须保留非空连续原文 evidence")
    def evidence(value: Any) -> None:
        if isinstance(value, dict):
            if "evidence" in value and (not isinstance(value["evidence"], str) or not value["evidence"].strip()
                                         or source is not None and value["evidence"] not in source):
                raise DataError(422, "分析条目的 evidence 不是输入原文中的连续片段")
            for child in value.values():
                evidence(child)
        elif isinstance(value, list):
            for child in value:
                evidence(child)
    evidence(result)
    return result


def _union(a: list[Any], b: list[Any]) -> list[Any]:
    seen, result = set(), []
    for item in a + b:
        key = _dump(item)
        if key not in seen:
            seen.add(key)
            result.append(copy.deepcopy(item))
    return result


def merge_named(existing: list[dict], incoming: list[dict], section: str) -> list[dict]:
    """Port of referenceMerge.ts name/category+title merge, retaining evidence."""
    result = copy.deepcopy(existing)
    def key(item: dict) -> tuple:
        return (item.get("name", "").strip(),) if section == "characters" else (item.get("category", "other"), item.get("title", "").strip())
    indices = {key(item): i for i, item in enumerate(result)}
    for item in incoming:
        k = key(item)
        if k not in indices:
            indices[k] = len(result)
            result.append(copy.deepcopy(item))
            continue
        old = result[indices[k]]
        for field, value in item.items():
            if isinstance(value, list) and isinstance(old.get(field, []), list):
                old[field] = _union(old.get(field, []), value)
            elif field not in old or old[field] in (None, "", [], {}):
                old[field] = copy.deepcopy(value)
            elif field == "first_chapter":
                a, b = re.search(r"\d+", str(old[field])), re.search(r"\d+", str(value))
                if b and (not a or int(b[0]) < int(a[0])):
                    old[field] = value
            # Existing edited scalar facts intentionally win. New chapter facts
            # remain in rich lists instead of replacing a user's description.
    return result


def _legacy_reference(result: dict, chunk: dict) -> dict:
    rich = copy.deepcopy(result.get("reference", {}))
    if "characters" not in rich and result.get("characters"):
        rich["characters"] = [{**item, "intro": item["description"]} for item in result["characters"]]
    if "plot" not in rich and result.get("events"):
        rich["plot"] = {"logline": "", "epochs": [{"title": "正文", "periods": [{"time": f"第 {chunk['index'] + 1} 段", "events": result["events"]}]}]}
    return rich


def aggregate_style(entries: list[dict]) -> dict:
    """Port of char-weighted style aggregation and per-chapter rhythm signals."""
    if not entries:
        return {}
    total = sum(entry["chars"] for entry in entries) or 1
    result: dict[str, Any] = {}
    keys = set().union(*(entry["data"].keys() for entry in entries))
    for key in keys:
        values = [(entry["data"][key], entry["chars"]) for entry in entries if key in entry["data"]]
        if all(type(value) in (int, float) for value, _ in values):
            result[key] = round(sum(value * weight for value, weight in values) / (sum(weight for _, weight in values) or total), 4)
        elif all(isinstance(value, dict) for value, _ in values) and key in {"pacing_profile", "punctuation_profile"}:
            result[key] = aggregate_style([{"data": value, "chars": weight} for value, weight in values])
        elif all(isinstance(value, list) for value, _ in values):
            result[key] = _union([], [item for value, _ in values for item in value])
        else:
            result[key] = copy.deepcopy(values[0][0])
    return result


def compute_nlp_style(text: str) -> dict:
    """Adapted from reference_pipeline/nlp_stats.py::compute_nlp_style.

    Uses the upstream deterministic Unicode fallback, avoiding an implicit
    tokenizer/model download. These metrics never masquerade as LLM judgments.
    """
    if not text:
        return {}
    sentences = [part.strip() for part in re.split(r'[。！？…]+["」』）]?', text) if len(part.strip()) >= 2]
    tokens = [char for char in text[:80000] if not char.isspace()]
    vocab = min(1.0, max(0.0, (len(set(tokens)) / math.sqrt(max(len(tokens), 1)) - 3) / 12))
    per_k = max(len(text) / 1000, 1)
    return {"avg_sentence_length": round(sum(map(len, sentences)) / len(sentences), 2) if sentences else 0,
            "vocab_complexity": round(vocab, 4),
            "punctuation_profile": {"ellipsis": round(len(re.findall(r"…+|\.{3,}", text)) / per_k, 3),
                                    "dash": round(len(re.findall(r"—+|--+", text)) / per_k, 3),
                                    "exclamation": round((text.count("！") + text.count("!")) / per_k, 3),
                                    "question": round((text.count("？") + text.count("?")) / per_k, 3),
                                    "comma": round((text.count("，") + text.count(",")) / per_k, 3)}}


def rhythm_from_style(style: dict, chars: int) -> dict:
    signals = style.get("chapter_signals", [])
    features, payoffs, climaxes, density = [], [], [], []
    for index, signal in enumerate(signals):
        if not isinstance(signal, dict):
            continue
        match = re.search(r"\d+", str(signal.get("chapter", "")))
        chapter = int(match[0]) if match else index + 1
        info = signal.get("info_density", 0)
        info = info if type(info) in (int, float) else 0
        types = signal.get("chapter_types", [])
        features.append({"chapter": chapter, "types": types, "info_density": info, "summary": signal.get("summary", ""), "hooks": signal.get("hooks", [])})
        density.append(info)
        if "高潮" in types:
            climaxes.append(chapter)
        payoffs.extend({"chapter": chapter, **payoff} for payoff in signal.get("payoffs", []) if isinstance(payoff, dict))
    features.sort(key=lambda item: item["chapter"])
    segments: list[dict] = []
    for feature in features:
        pacing = "fast" if feature["info_density"] >= .67 else "slow" if feature["info_density"] <= .33 else "medium"
        if segments and segments[-1]["pacing"] == pacing and segments[-1]["end"] + 1 == feature["chapter"]:
            segment = segments[-1]
            n = segment.pop("_count")
            segment["avg_info_density"] = (segment["avg_info_density"] * n + feature["info_density"]) / (n + 1)
            segment.update(end=feature["chapter"], _count=n + 1)
        else:
            segments.append({"start": feature["chapter"], "end": feature["chapter"], "pacing": pacing, "avg_info_density": feature["info_density"], "_count": 1})
    for segment in segments:
        segment.pop("_count")
        segment["avg_info_density"] = round(segment["avg_info_density"], 4)
    return {"coverage": {"chapters": len(features), "chars": chars}, "opening_pattern": style.get("opening_pattern", ""),
            "climax_positions": climaxes, "shuangdian": payoffs, "chapter_features": features,
            "info_density_curve": [item["info_density"] for item in features], "pacing_segments": segments}


class ReferenceAnalysisStore:
    def __init__(self, data_dir: str | Path, *, documents: Any = None):
        self.path = Path(data_dir) / "workbench.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.documents = documents
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS reference_analysis (document_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,structure_type TEXT NOT NULL,sections_json TEXT NOT NULL,updated_at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS reference_section_commits (document_id TEXT NOT NULL,run_id TEXT NOT NULL,chunk_id TEXT NOT NULL,section TEXT NOT NULL,data_json TEXT NOT NULL,chars INTEGER NOT NULL,committed_at TEXT NOT NULL,PRIMARY KEY(document_id,run_id,chunk_id,section))")
            columns = {row[1] for row in db.execute("PRAGMA table_info(reference_section_commits)")}
            if "source_version_id" not in columns:
                db.execute("ALTER TABLE reference_section_commits ADD COLUMN source_version_id TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE TABLE IF NOT EXISTS reference_section_overrides (document_id TEXT NOT NULL,section TEXT NOT NULL,data_json TEXT NOT NULL,PRIMARY KEY(document_id,section))")
            db.execute("CREATE TABLE IF NOT EXISTS reference_analysis_history (document_id TEXT NOT NULL,revision INTEGER NOT NULL,structure_type TEXT NOT NULL,sections_json TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(document_id,revision))")
            if "commits_json" not in {row[1] for row in db.execute("PRAGMA table_info(reference_analysis_history)")}:
                db.execute("ALTER TABLE reference_analysis_history ADD COLUMN commits_json TEXT NOT NULL DEFAULT '[]'")
            db.execute("""DELETE FROM reference_analysis_history WHERE rowid IN (
                SELECT rowid FROM (SELECT rowid,ROW_NUMBER() OVER (
                PARTITION BY document_id ORDER BY revision DESC) AS age FROM reference_analysis_history)
                WHERE age>?)""", (ANALYSIS_HISTORY_LIMIT,))

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure(self, db: sqlite3.Connection, document_id: str) -> None:
        if self.documents:
            self.documents.get_document(document_id)
        db.execute("INSERT OR IGNORE INTO reference_analysis VALUES (?,0,'narrative',?,?)", (document_id, _dump(DEFAULT_SECTIONS), datetime.now(timezone.utc).isoformat()))
        snapshot_exists = db.execute("SELECT 1 FROM reference_analysis_history h JOIN reference_analysis a ON a.document_id=h.document_id AND a.revision=h.revision WHERE a.document_id=?", (document_id,)).fetchone()
        if not snapshot_exists:
            db.execute("INSERT OR IGNORE INTO reference_analysis_history (document_id,revision,structure_type,sections_json,updated_at,commits_json) SELECT document_id,revision,structure_type,sections_json,updated_at,? FROM reference_analysis WHERE document_id=?", (self._ledger(db, document_id), document_id))

    @staticmethod
    def _ledger(db: sqlite3.Connection, document_id: str) -> str:
        return _dump({"commits": [list(row) for row in db.execute("SELECT document_id,run_id,chunk_id,section,data_json,chars,committed_at,source_version_id FROM reference_section_commits WHERE document_id=? ORDER BY committed_at,run_id,chunk_id,section", (document_id,))],
                      "overrides": [list(row) for row in db.execute("SELECT document_id,section,data_json FROM reference_section_overrides WHERE document_id=? ORDER BY section", (document_id,))]})

    def _write(self, db: sqlite3.Connection, document_id: str, sections: dict, structure_type: str) -> None:
        db.execute("UPDATE reference_analysis SET sections_json=?,structure_type=?,revision=revision+1,updated_at=? WHERE document_id=?", (_dump(sections), structure_type, datetime.now(timezone.utc).isoformat(), document_id))
        db.execute("INSERT INTO reference_analysis_history (document_id,revision,structure_type,sections_json,updated_at,commits_json) SELECT document_id,revision,structure_type,sections_json,updated_at,? FROM reference_analysis WHERE document_id=?", (self._ledger(db, document_id), document_id))
        # Only recovery snapshots are bounded. Current facts and the authoritative
        # per-chunk contribution ledger remain intact for idempotent approvals.
        db.execute("DELETE FROM reference_analysis_history WHERE document_id=? AND revision NOT IN (SELECT revision FROM reference_analysis_history WHERE document_id=? ORDER BY revision DESC LIMIT ?)", (document_id, document_id, ANALYSIS_HISTORY_LIMIT))

    def _get(self, db: sqlite3.Connection, document_id: str) -> dict:
        row = db.execute("SELECT * FROM reference_analysis WHERE document_id=?", (document_id,)).fetchone()
        return {"document_id": document_id, "revision": row[1], "structure_type": row[2], "sections": {**copy.deepcopy(DEFAULT_SECTIONS), **json.loads(row[3])}, "updated_at": row[4],
                "commits": [{"run_id": item[0], "chunk_id": item[1], "section": item[2], "committed_at": item[3], "source_version_id": item[4]} for item in db.execute("SELECT run_id,chunk_id,section,committed_at,source_version_id FROM reference_section_commits WHERE document_id=? ORDER BY committed_at", (document_id,))]}

    def get(self, document_id: str) -> dict:
        with self._connect() as db:
            self._ensure(db, document_id)
            return self._get(db, document_id)

    @staticmethod
    def _revision(state: dict, expected_revision: int) -> None:
        if type(expected_revision) is not int or state["revision"] != expected_revision:
            raise DataError(409, "作品分析已变化，请刷新后保存")

    def configure(self, document_id: str, structure_type: str, expected_revision: int) -> dict:
        if structure_type not in {"narrative", "setting_collection"}:
            raise DataError(422, "作品结构类型无效")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure(db, document_id)
            self._revision(self._get(db, document_id), expected_revision)
            state = self._get(db, document_id)
            self._write(db, document_id, state["sections"], structure_type)
            return self._get(db, document_id)

    def save_section(self, document_id: str, section: str, data: Any, expected_revision: int) -> dict:
        value = validate_section(section, data)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure(db, document_id)
            state = self._get(db, document_id)
            self._revision(state, expected_revision)
            state["sections"][section] = value
            if section in {"style", "rhythm", "opening"}:
                db.execute("INSERT INTO reference_section_overrides VALUES (?,?,?) ON CONFLICT(document_id,section) DO UPDATE SET data_json=excluded.data_json", (document_id, section, _dump(value)))
            self._write(db, document_id, state["sections"], state["structure_type"])
            return self._get(db, document_id)

    def commit_candidate(self, run: dict, chunk_id: str, sections: list[str], expected_revision: int) -> dict:
        if not isinstance(sections, list) or not sections or set(sections) - (SECTIONS - {"raw_entries"}):
            raise DataError(422, "请选择有效的分析分区")
        chunk = next((item for item in run["candidates"] if item["chunk_id"] == chunk_id), None)
        if chunk is None or chunk["status"] != "completed" or not chunk.get("result"):
            raise DataError(409, "此分段尚无可审核的候选")
        incoming = validate_reference(_legacy_reference(chunk["result"], chunk))
        if any(section not in incoming for section in sections):
            raise DataError(422, "候选未包含所选分析分区")
        document_id = run["document_id"]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure(db, document_id)
            state = self._get(db, document_id)
            already = {(item["run_id"], item["chunk_id"], item["section"]) for item in state["commits"]}
            pending = [section for section in dict.fromkeys(sections) if (run["id"], chunk_id, section) not in already]
            if not pending:
                return state
            self._revision(state, expected_revision)
            now = datetime.now(timezone.utc).isoformat()
            for section in pending:
                value = incoming[section]
                old = state["sections"][section]
                if section in {"characters", "settings", "setting_features"}:
                    state["sections"][section] = merge_named(old, value, section)
                elif section == "plot":
                    plot = copy.deepcopy(old)
                    plot["epochs"] = _union(plot.get("epochs", []), value.get("epochs", []))
                    if not plot.get("logline"):
                        plot["logline"] = value.get("logline", "")
                    state["sections"][section] = plot
                elif section == "style":
                    ledger = [{"data": json.loads(row[0]), "chars": row[1]} for row in db.execute("SELECT data_json,chars FROM reference_section_commits WHERE document_id=? AND section='style' ORDER BY committed_at", (document_id,))]
                    ledger.append({"data": value, "chars": chunk["end"] - chunk["start"]})
                    state["sections"][section] = aggregate_style(ledger)
                    overrides = {row[0]: json.loads(row[1]) for row in db.execute("SELECT section,data_json FROM reference_section_overrides WHERE document_id=?", (document_id,))}
                    state["sections"][section].update(overrides.get("style", {}))
                    state["sections"]["rhythm"] = overrides.get("rhythm", rhythm_from_style(state["sections"][section], sum(item["chars"] for item in ledger)))
                    state["sections"]["opening"] = overrides.get("opening", {"pattern": state["sections"][section].get("opening_pattern", "")})
                else:
                    state["sections"][section] = {**old, **value}
                db.execute("INSERT INTO reference_section_commits (document_id,run_id,chunk_id,section,data_json,chars,committed_at,source_version_id) VALUES (?,?,?,?,?,?,?,?)", (document_id, run["id"], chunk_id, section, _dump(value), chunk["end"] - chunk["start"], now, run["version_id"]))
            self._write(db, document_id, state["sections"], state["structure_type"])
            return self._get(db, document_id)

    def list_history(self, document_id: str) -> list[dict]:
        self.get(document_id)
        with self._connect() as db:
            return [{"revision": row[0], "structure_type": row[1], "updated_at": row[2]} for row in db.execute("SELECT revision,structure_type,updated_at FROM reference_analysis_history WHERE document_id=? ORDER BY revision DESC", (document_id,))]

    def restore(self, document_id: str, target_revision: int, expected_revision: int) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure(db, document_id)
            self._revision(self._get(db, document_id), expected_revision)
            row = db.execute("SELECT structure_type,sections_json,commits_json FROM reference_analysis_history WHERE document_id=? AND revision=?", (document_id, target_revision)).fetchone()
            if row is None:
                raise DataError(404, "分析历史版本不存在")
            sections = {**copy.deepcopy(DEFAULT_SECTIONS), **json.loads(row[1])}
            db.execute("DELETE FROM reference_section_commits WHERE document_id=?", (document_id,))
            projection = json.loads(row[2])
            contributions = projection.get("commits", []) if isinstance(projection, dict) else projection
            for contribution in contributions:
                db.execute("INSERT INTO reference_section_commits (document_id,run_id,chunk_id,section,data_json,chars,committed_at,source_version_id) VALUES (?,?,?,?,?,?,?,?)", contribution)
            db.execute("DELETE FROM reference_section_overrides WHERE document_id=?", (document_id,))
            for override in projection.get("overrides", []) if isinstance(projection, dict) else []:
                db.execute("INSERT INTO reference_section_overrides VALUES (?,?,?)", override)
            self._write(db, document_id, sections, row[0])
            return self._get(db, document_id)

    def build_source(self, document_id: str, expected_revision: int) -> dict:
        state = self.get(document_id)
        self._revision(state, expected_revision)
        sections = state["sections"]
        parts = ["# " + item["title"] + "\n" + item["content"] for item in sections["raw_entries"]]
        if sections["settings"]:
            parts.append("# 已有设定\n" + _dump(sections["settings"]))
        if sections["characters"]:
            parts.append("# 已有角色\n" + _dump(sections["characters"]))
        text = "\n\n".join(parts)
        if not text.strip():
            raise DataError(422, "请先添加原始文本、设定或角色")
        if len(text.encode("utf-8")) > 8_000_000:
            raise DataError(413, "设定提取材料超过 8 MB，请拆分作品")
        return {"text": text, "reference_revision": state["revision"], "structure_type": state["structure_type"]}

    def export_entries(self, document_id: str) -> bytes:
        """Length-prefixed UTF-8 plain text keeps multiline/marker content intact."""
        entries = self.get(document_id)["sections"]["raw_entries"]
        parts = ["InkReference entries v1\n"]
        for item in entries:
            parts.append("@@ " + json.dumps({"title": item["title"], "chars": len(item["content"])}, ensure_ascii=False) + "\n" + item["content"] + "\n")
        return "".join(parts).encode("utf-8")

    def export_collection(self, document_id: str) -> bytes:
        sections = self.get(document_id)["sections"]
        return ("InkReference collection v1\n" + json.dumps({key: sections[key] for key in ("raw_entries", "settings", "characters", "setting_features")}, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")

    def import_collection(self, document_id: str, text: str, expected_revision: int, *, mode: str = "merge", title: str = "导入文本") -> dict:
        if not isinstance(text, str) or len(text.encode("utf-8")) > 8_000_000 or mode not in {"merge", "replace"}:
            raise DataError(422, "导入文本或导入模式无效")
        prefix = "InkReference collection v1\n"
        if not text.startswith(prefix):
            return self.import_entries(document_id, text, expected_revision, mode=mode, title=title)
        try:
            from .skills import _load_json
            data = _load_json(text[len(prefix):].encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise DataError(422, "设定集文本格式损坏，未导入") from exc
        fields = {"raw_entries", "settings", "characters", "setting_features"}
        if not isinstance(data, dict) or set(data) != fields:
            raise DataError(422, "设定集文本需包含原始文本、设定、角色、设定特征四个分区")
        values = {key: validate_section(key, value) for key, value in data.items()}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure(db, document_id)
            state = self._get(db, document_id)
            self._revision(state, expected_revision)
            for key, value in values.items():
                old = state["sections"][key]
                state["sections"][key] = value if mode == "replace" else _union(old, value) if key == "raw_entries" else merge_named(old, value, key)
            self._write(db, document_id, state["sections"], state["structure_type"])
            return self._get(db, document_id)

    def import_entries(self, document_id: str, text: str, expected_revision: int, *, mode: str = "merge", title: str = "导入文本") -> dict:
        if not isinstance(text, str) or len(text.encode("utf-8")) > 8_000_000 or mode not in {"merge", "replace"}:
            raise DataError(422, "导入文本或导入模式无效")
        entries = []
        if text.startswith("InkReference entries v1\n"):
            cursor = len("InkReference entries v1\n")
            while cursor < len(text):
                end = text.find("\n", cursor)
                try:
                    if end < 0 or not text.startswith("@@ ", cursor):
                        raise ValueError("条目标记缺失")
                    header = json.loads(text[cursor + 3:end])
                    length = header["chars"]
                    if type(length) is not int or length < 1 or not isinstance(header["title"], str):
                        raise ValueError("条目长度无效")
                    start, stop = end + 1, end + 1 + length
                    if stop >= len(text) or text[stop] != "\n":
                        raise ValueError("条目正文不完整")
                    entries.append({"title": header["title"], "content": text[start:stop]})
                    cursor = stop + 1
                except (ValueError, TypeError, KeyError) as exc:
                    raise DataError(422, "InkReference 文本条目格式损坏，未导入") from exc
        elif text.strip():
            entries = [{"title": title, "content": text}]
        state = self.get(document_id)
        self._revision(state, expected_revision)
        return self.save_section(document_id, "raw_entries", _union(state["sections"]["raw_entries"], entries) if mode == "merge" else entries, expected_revision)
