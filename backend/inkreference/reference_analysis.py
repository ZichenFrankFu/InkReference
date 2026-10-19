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
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .database import DataError
from .chronicle_schema import (is_v2, is_v3, is_modern, validate_v2_section, validate_v3_section, validate_links,
                               normalize_v3_section, adapt_v2_section)
from .analysis_projection import project_sections
from .language_analysis import compute_language_statistics

ARRAY_SECTIONS = {"characters", "settings", "setting_features", "raw_entries"}
OBJECT_SECTIONS = {"plot", "style", "rhythm", "opening", "metadata", "era_background"}
SECTIONS = ARRAY_SECTIONS | OBJECT_SECTIONS
DEFAULT_SECTIONS = {"plot": {"logline": "", "epochs": []}, "characters": [], "settings": [],
                    "style": {}, "rhythm": {}, "opening": {}, "metadata": {}, "setting_features": [], "raw_entries": [], "era_background": {}}
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
    if is_modern(section, data):
        if section in {"characters", "settings"}:
            result = []
            for original in data:
                item = copy.deepcopy(original)
                if item.get("schema_version") == 3:
                    item.pop("current_snapshot", None)
                    for snapshot in item.get("snapshots", []): snapshot.pop("events", None)
                    result.append(validate_v3_section(section, [item])[0])
                elif item.get("schema_version") == 2: result.append(validate_v2_section(section, [item])[0])
                else: result.append(validate_section(section, [item])[0])
            return result
        computed = {}
        candidate = copy.deepcopy(data)
        for key in ({"plot": ("epochs", "legacy", "views", "outlines"), "style": ("deterministic_statistics", "legacy"),
                     "rhythm": ("legacy",), "opening": ("legacy",), "era_background": ("statistics", "legacy")}.get(section, ())):
            if key in candidate:
                derived = candidate.pop(key)
                if candidate.get("schema_version") != 3 or key not in {"views", "outlines", "statistics"}: computed[key] = derived
        if section == "plot" and candidate.get("schema_version") == 3:
            for line in candidate.get("plotlines", []): line.pop("current_status", None)
        validator = validate_v3_section if candidate.get("schema_version") == 3 else validate_v2_section
        value = validator(section, candidate)
        if computed: value.update(computed)
        return value
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
        facts.extend(result.get("plot", {}).get("events", []))
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
        if item.get("id"):
            return ("id", item["id"])
        return (item.get("name", "").strip(),) if section == "characters" else (item.get("category", "other"), item.get("title", "").strip())
    indices = {key(item): i for i, item in enumerate(result)}
    for item in incoming:
        k = key(item)
        if k not in indices:
            indices[k] = len(result)
            result.append(copy.deepcopy(item))
            continue
        old = result[indices[k]]
        if old.get("schema_version") == 3 and item.get("schema_version") != 3:
            item = adapt_v2_section(section, [item])[0]
        if item.get("schema_version") == 3 and old.get("schema_version") != 3:
            old = adapt_v2_section(section, [old])[0]
            result[indices[k]] = old
        for field, value in item.items():
            if field == "initial_state" and isinstance(value, dict) and isinstance(old.get(field), dict):
                previous = old[field].get("narrative_progress")
                incoming = value.get("narrative_progress")
                if old.get("origin") != "manual" and type(previous) in (int, float) and type(incoming) in (int, float) and incoming < previous:
                    old[field] = copy.deepcopy(value)
                continue
            if isinstance(value, list) and isinstance(old.get(field, []), list):
                old[field] = merge_by_id(old.get(field, []), value) if field in {"states", "experiences", "snapshots"} else _union(old.get(field, []), value)
            elif field not in old or old[field] in (None, "", [], {}):
                old[field] = copy.deepcopy(value)
            elif field == "first_chapter":
                a, b = re.search(r"\d+", str(old[field])), re.search(r"\d+", str(value))
                if b and (not a or int(b[0]) < int(a[0])):
                    old[field] = value
            # Existing edited scalar facts intentionally win. New chapter facts
            # remain in rich lists instead of replacing a user's description.
    for entity in result:
        if entity.get("schema_version") != 3: continue
        snapshots = sorted(entity.get("snapshots", []), key=lambda state: (state.get("narrative_range", {}).get("start", 0), (state.get("revealed_at") or {}).get("offset") or 0, state.get("id", "")))
        for previous, following in zip(snapshots, snapshots[1:]):
            span = previous.get("narrative_range", {})
            point = following.get("narrative_range", {}).get("start")
            if previous.get("origin") != "manual" and span.get("end") is None and type(point) in (int, float) and point >= span.get("start", 0): span["end"] = point
    return result


def merge_by_id(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """ID-based idempotent union; immutable existing facts win scalar conflicts."""
    result = copy.deepcopy(existing)
    index = {item.get("id", item.get("event_id")): i for i, item in enumerate(result) if item.get("id", item.get("event_id"))}
    for item in incoming:
        identity = item.get("id", item.get("event_id"))
        if identity and identity in index:
            old = result[index[identity]]
            for key, value in item.items():
                if isinstance(value, list) and isinstance(old.get(key), list):
                    old[key] = merge_by_id(old[key], value) if value and all(isinstance(part, dict) for part in value) else _union(old[key], value)
                elif key not in old or old[key] in ("", None, [], {}):
                    old[key] = copy.deepcopy(value)
        else:
            if identity: index[identity] = len(result)
            if item not in result: result.append(copy.deepcopy(item))
    return result


def merge_object(old: dict, value: dict) -> dict:
    if value.get("schema_version") in (2, 3) and old and old.get("schema_version") != value.get("schema_version"):
        return {**copy.deepcopy(value), "legacy": copy.deepcopy(old)}
    result = copy.deepcopy(old)
    for key, child in value.items():
        if isinstance(child, list) and isinstance(result.get(key), list):
            result[key] = merge_by_id(result[key], child) if all(isinstance(item, dict) for item in child) else _union(result[key], child)
        elif key not in result or result[key] in ("", None, [], {}):
            result[key] = copy.deepcopy(child)
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
        self.catalog_sync = None
        self.write_lock = threading.RLock()
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
        # Acquire the workspace lock before any SQLite lock. Catalog bridges
        # attach this database in the reverse direction; one reentrant order
        # prevents catalog/reference concurrent writes from waiting on each other.
        with self.write_lock:
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

    @staticmethod
    def _modern_sections(sections: dict) -> dict:
        result = {}
        for name, value in sections.items():
            if name in {"characters", "settings"}:
                entries = [item for item in value if item.get("schema_version") in (2, 3)]
                if entries: result[name] = entries
            elif is_modern(name, value):
                # Saved compatibility snapshots are not new claimed evidence.
                result[name] = {key: item for key, item in value.items() if key not in {"legacy", "epochs", "deterministic_statistics"}}
        return result

    def _validate_sources(self, db: sqlite3.Connection, document_id: str, sections: dict, cache: dict | None = None) -> None:
        if not self.documents:
            return
        cache = {} if cache is None else cache
        has_versions = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='doc_versions'").fetchone()
        def source(version_id):
            if not version_id:
                raise DataError(422, "分析证据缺少正文版本 source_version_id")
            if version_id not in cache:
                if has_versions:
                    row = db.execute("SELECT payload_json FROM doc_versions WHERE document_id=? AND id=?", (document_id, version_id)).fetchone()
                    if row is None: raise DataError(422, "分析证据引用的正文版本不存在或不属于当前作品")
                    cache[version_id] = json.loads(row[0])
                else:
                    try: cache[version_id] = self.documents.get_version(document_id, version_id)
                    except DataError as exc: raise DataError(422, "分析证据引用的正文版本不存在或不属于当前作品") from exc
            return cache[version_id]
        def walk(node):
            if isinstance(node, dict):
                if "evidence" in node:
                    version = source(node.get("source_version_id", ""))
                    text = version["text"]
                    quote, anchor = node["evidence"], node["revealed_at"]
                    offset = anchor["offset"]
                    if offset is None:
                        # Unknown location remains unknown. A declared chapter
                        # must still contain the exact quote at a valid position.
                        chapter = anchor["chapter"]
                        candidates = [item for item in version.get("chapters", []) if isinstance(item, dict) and item.get("number") == chapter]
                        if chapter is not None and candidates:
                            matched = any(item["start"] <= text.find(quote, item["start"], item["end"] + len(quote)) < item["end"] for item in candidates)
                        else: matched = quote in text
                    else:
                        matched = text[offset:offset + len(quote)] == quote
                    if not matched:
                        raise DataError(422, "分析证据 evidence 与正文版本或 offset 不一致")
                    chapter = anchor["chapter"]
                    declared = [item for item in version.get("chapters", []) if isinstance(item, dict) and type(item.get("number")) is int]
                    if chapter is not None and declared:
                        matching = [item for item in declared if item["number"] == chapter]
                        if not matching or offset is not None and not any(item["start"] <= offset < item["end"] for item in matching):
                            raise DataError(422, "分析证据 revealed_at.chapter 与原文位置不一致")
                for child in node.values(): walk(child)
            elif isinstance(node, list):
                for child in node: walk(child)
        walk(self._modern_sections(sections))

    def _write(self, db: sqlite3.Connection, document_id: str, sections: dict, structure_type: str) -> None:
        validate_links(self._modern_sections(sections))
        sync = getattr(self, "catalog_sync", None)
        if sync is not None: sync(db, document_id, sections)
        db.execute("UPDATE reference_analysis SET sections_json=?,structure_type=?,revision=revision+1,updated_at=? WHERE document_id=?", (_dump(sections), structure_type, datetime.now(timezone.utc).isoformat(), document_id))
        db.execute("INSERT INTO reference_analysis_history (document_id,revision,structure_type,sections_json,updated_at,commits_json) SELECT document_id,revision,structure_type,sections_json,updated_at,? FROM reference_analysis WHERE document_id=?", (self._ledger(db, document_id), document_id))
        # Only recovery snapshots are bounded. Current facts and the authoritative
        # per-chunk contribution ledger remain intact for idempotent approvals.
        db.execute("DELETE FROM reference_analysis_history WHERE document_id=? AND revision NOT IN (SELECT revision FROM reference_analysis_history WHERE document_id=? ORDER BY revision DESC LIMIT ?)", (document_id, document_id, ANALYSIS_HISTORY_LIMIT))

    def _get(self, db: sqlite3.Connection, document_id: str) -> dict:
        row = db.execute("SELECT * FROM reference_analysis WHERE document_id=?", (document_id,)).fetchone()
        return {"document_id": document_id, "revision": row[1], "structure_type": row[2], "sections": {**copy.deepcopy(DEFAULT_SECTIONS), **json.loads(row[3])}, "updated_at": row[4],
                "commits": [{"run_id": item[0], "chunk_id": item[1], "section": item[2], "committed_at": item[3], "source_version_id": item[4]} for item in db.execute("SELECT run_id,chunk_id,section,committed_at,source_version_id FROM reference_section_commits WHERE document_id=? ORDER BY committed_at", (document_id,))]}

    def project(self, document_id: str, chapter: int | None = None, *, volumes: list[dict] | None = None, target_version: int | None = None, progress: float | None = None) -> dict:
        state = self.get(document_id)
        sources = {}
        ledger_sources = {item.get("source_version_id") for item in state["commits"] if item.get("source_version_id")}
        legacy_source = next(iter(ledger_sources)) if len(ledger_sources) == 1 else "" if ledger_sources else None
        if target_version == 3 and self.documents:
            def collect(node):
                if isinstance(node, dict):
                    identity = node.get("source_version_id")
                    if identity and identity not in sources:
                        try: sources[identity] = self.documents.get_version(document_id, identity)
                        except DataError: pass
                    for child in node.values(): collect(child)
                elif isinstance(node, list):
                    for child in node: collect(child)
            collect(state["sections"])
            if legacy_source and legacy_source not in sources:
                try: sources[legacy_source] = self.documents.get_version(document_id, legacy_source)
                except DataError: pass
        state["sections"] = project_sections(state["sections"], chapter, volumes=volumes, target_version=target_version, source_versions=sources, progress=progress, legacy_source_version_id=legacy_source)
        state["projection"] = {"chapter": chapter, "schema_version": target_version or 2, "progress": progress}
        return state

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
        value = validate_section(section, normalize_v3_section(section, data, manual=True) if is_v3(section, data) else data)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure(db, document_id)
            state = self._get(db, document_id)
            self._revision(state, expected_revision)
            if section == "style" and value.get("schema_version") in (2, 3):
                value.pop("deterministic_statistics", None)
                old_statistics = state["sections"]["style"].get("deterministic_statistics")
                if old_statistics: value["deterministic_statistics"] = copy.deepcopy(old_statistics)
            if is_v3(section, value):
                old_ids = {}
                def index_facts(node):
                    if isinstance(node, dict):
                        if node.get("id"): old_ids[node["id"]] = node
                        for child in node.values(): index_facts(child)
                    elif isinstance(node, list):
                        for child in node: index_facts(child)
                index_facts(state["sections"][section])
                def mark_manual(node):
                    if isinstance(node, dict):
                        if "origin" in node or "evidence" in node:
                            old = old_ids.get(node.get("id"))
                            business = lambda item: {key: child for key, child in (item or {}).items() if key not in {"origin", "revealed_at", "source_version_id", "evidence"}}
                            if old is None or business(old) != business(node): node["origin"] = "manual"
                        for child in node.values(): mark_manual(child)
                    elif isinstance(node, list):
                        for child in node: mark_manual(child)
                mark_manual(value)
            self._validate_sources(db, document_id, {section: value})
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
            source_cache = {}
            for section in pending:
                value = incoming[section]
                if is_v3(section, value): value = validate_v3_section(section, value, require_evidence=True)
                self._validate_sources(db, document_id, {section: value}, source_cache)
                if is_v3(section, value):
                    value = normalize_v3_section(section, value, source_versions=source_cache)
                    value = validate_section(section, value)
                old = state["sections"][section]
                if isinstance(old, dict) and old.get("schema_version") == 3 and isinstance(value, dict) and value.get("schema_version") == 2:
                    value = adapt_v2_section(section, value, source_versions=source_cache)
                if section in {"characters", "settings", "setting_features"}:
                    state["sections"][section] = merge_named(old, value, section)
                elif section == "plot":
                    plot = copy.deepcopy(old)
                    plot["epochs"] = _union(plot.get("epochs", []), value.get("epochs", []))
                    if value.get("schema_version") in (2, 3):
                        if value.get("schema_version") == 3 and plot.get("schema_version") != 3:
                            legacy = copy.deepcopy(plot)
                            plot = adapt_v2_section("plot", plot, source_versions=source_cache)
                            if legacy: plot["legacy"] = legacy
                        plot["schema_version"] = value["schema_version"]
                        plot["events"] = merge_by_id(plot.get("events", []), value.get("events", []))
                        plot["plotlines"] = merge_by_id(plot.get("plotlines", []), value.get("plotlines", []))
                    if not plot.get("logline"):
                        plot["logline"] = value.get("logline", "")
                    state["sections"][section] = plot
                elif section == "style":
                    ledger = [{"data": json.loads(row[0]), "chars": row[1]} for row in db.execute("SELECT data_json,chars FROM reference_section_commits WHERE document_id=? AND section='style' ORDER BY committed_at", (document_id,))]
                    ledger.append({"data": value, "chars": chunk["end"] - chunk["start"]})
                    modern = value.get("schema_version") in (2, 3)
                    selected_ledger = [entry for entry in ledger if entry["data"].get("schema_version") == value.get("schema_version")] if modern else ledger
                    state["sections"][section] = aggregate_style(selected_ledger)
                    if modern:
                        state["sections"][section]["schema_version"] = value["schema_version"]
                        for key in ("observations", "narrative_composition", "catchphrases"):
                            previous = adapt_v2_section("style", old, source_versions=source_cache) if value.get("schema_version") == 3 and old.get("schema_version") != 3 else old
                            state["sections"][section][key] = _union(previous.get(key, []), value.get(key, []))
                        if old and old.get("schema_version") != value.get("schema_version"):
                            state["sections"][section]["legacy"] = copy.deepcopy(old)
                        elif old.get("legacy"):
                            state["sections"][section]["legacy"] = copy.deepcopy(old["legacy"])
                    overrides = {row[0]: json.loads(row[1]) for row in db.execute("SELECT section,data_json FROM reference_section_overrides WHERE document_id=?", (document_id,))}
                    override = overrides.get("style", {})
                    if not modern or override.get("schema_version") == value.get("schema_version"):
                        state["sections"][section].update(override)
                    if not modern:
                        state["sections"]["rhythm"] = overrides.get("rhythm", rhythm_from_style(state["sections"][section], sum(item["chars"] for item in ledger)))
                        state["sections"]["opening"] = overrides.get("opening", {"pattern": state["sections"][section].get("opening_pattern", "")})

                else:
                    state["sections"][section] = merge_object(old, value) if is_modern(section, value) else {**old, **value}
                db.execute("INSERT INTO reference_section_commits (document_id,run_id,chunk_id,section,data_json,chars,committed_at,source_version_id) VALUES (?,?,?,?,?,?,?,?)", (document_id, run["id"], chunk_id, section, _dump(value), chunk["end"] - chunk["start"], now, run["version_id"]))
            if "style" in pending and self.documents and state["sections"]["style"].get("schema_version") in (2, 3):
                source = source_cache.get(run["version_id"]) or self.documents.get_version(document_id, run["version_id"])
                chapters = source.get("chapters", [])
                body_ranges = [(item.get("body_start"), item.get("end")) for item in chapters if isinstance(item, dict) and not item.get("placeholder")]
                bodies = [source["text"][start:end] for start, end in body_ranges if type(start) is int and type(end) is int and 0 <= start <= end <= len(source["text"])]
                structured = any(isinstance(item, dict) for item in chapters)
                measured_text = "\n".join(bodies) if structured else source["text"]
                measured_chapters = sum(bool(body.strip()) for body in bodies) if structured else len(chapters)
                state["sections"]["style"]["deterministic_statistics"] = compute_language_statistics(measured_text, source_version_id=run["version_id"], chapter_count=measured_chapters)
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
