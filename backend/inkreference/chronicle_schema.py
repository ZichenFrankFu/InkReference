"""Versioned reference chronicle contract.

Extends InkOctoBot's MIT referenceMerge.ts / AnalysisEditors.tsx chronicle,
chapter facts and setting updates (51c82b6f01f6a5b5210e1bae9302be257b3b8083).
Stable identities, explicit corrections and two independent clocks are local
adaptations. Reading old records never fabricates dates or rewrites evidence.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .database import DataError

SCHEMA_VERSION = 2


def obj(properties: dict, required: tuple | list | None = None) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties if required is None else required)}


def arr(item: dict, maximum: int = 10000) -> dict:
    return {"type": "array", "items": item, "maxItems": maximum}


def text(maximum: int = 10000, *, empty: bool = True) -> dict:
    return {"type": "string", "minLength": 0 if empty else 1, "maxLength": maximum}


ID = text(160, empty=False)
IDS = arr(ID)
NULL_TEXT = {"type": ["string", "null"], "maxLength": 1000}
ANCHOR = obj({"chapter": {"type": ["integer", "null"], "minimum": 1},
              "offset": {"type": ["integer", "null"], "minimum": 0}})
STORY_TIME = obj({"label": text(1000), "sort_key": NULL_TEXT})
PROVENANCE = {"revealed_at": ANCHOR, "source_version_id": text(160), "evidence": text(10000, empty=False)}
STATE = obj({"id": ID, "attribute": text(120, empty=False), "value": text(),
             "change_type": {"type": "string", "enum": ["addition", "change", "revelation", "correction"]},
             "supersedes": IDS, "event_id": NULL_TEXT, "setting_ids": IDS, "character_ids": IDS,
             "story_time": STORY_TIME, **PROVENANCE})
EVENT = obj({"id": ID, "name": text(1000, empty=False), "description": text(), "participants": IDS,
             "setting_ids": IDS, "causes": IDS, "results": IDS, "plotline_ids": IDS,
             "story_time": STORY_TIME, "volume": NULL_TEXT, **PROVENANCE})
STAGE = obj({"id": ID, "goal": text(), "status": {"type": "string", "enum": ["open", "progressing", "resolved", "abandoned"]},
             "event_ids": IDS, **PROVENANCE})
PLOTLINE = obj({"id": ID, "title": text(1000, empty=False), "goal": text(), "stages": arr(STAGE)})
EXPERIENCE = obj({"event_id": ID, "changes": arr(STATE), **PROVENANCE})
CHARACTER = obj({"schema_version": {"type": "integer", "enum": [2]}, "id": ID, "name": text(1000, empty=False),
                 "intro": text(), "temperament": text(), "visual_design": text(), "first_chapter": text(120),
                 "states": arr(STATE), "experiences": arr(EXPERIENCE),
                 "appearance": arr(obj({"chapter": text(120), "text": text()})),
                 "personality": arr(obj({"chapter": text(120), "text": text()})),
                 "speech_samples": arr(text()), "role_tag": text(200), **PROVENANCE},
                ("schema_version", "id", "name", "intro", "temperament", "visual_design", "states", "experiences", *PROVENANCE))
SETTING = obj({"schema_version": {"type": "integer", "enum": [2]}, "id": ID, "title": text(1000, empty=False),
               "category": {"type": "string", "enum": ["power_system", "factions", "geography", "social_rules", "history", "worldview", "other"]},
               "content": text(), "states": arr(STATE), "first_chapter": text(120), **PROVENANCE},
              ("schema_version", "id", "title", "category", "content", "states", *PROVENANCE))
OCCURRENCE = obj({"character_id": NULL_TEXT, "context": text(),
                  "usage": {"type": "string", "enum": ["dialogue", "thought", "narration", "reference"]}, **PROVENANCE})
ERA_ITEM = obj({"id": ID, "kind": {"type": "string", "enum": ["meme", "real_world_event"]},
               "expression": text(1000, empty=False), "meaning": text(),
               "time_range": obj({"start": NULL_TEXT, "end": NULL_TEXT,
                                  "precision": {"type": "string", "enum": ["unknown", "approximate", "year", "month", "day"]},
                                  "verification": {"type": "string", "enum": ["unverified", "source_stated"]}}),
               "occurrences": arr(OCCURRENCE)})
STYLE = obj({"schema_version": {"type": "integer", "enum": [2]},
             "narrative_person": text(), "focalization": text(), "register": text(),
             "colloquialism": text(), "rhetoric": arr(text()), "dialogue_style": text(),
             "catchphrases": arr(obj({"character_id": NULL_TEXT, "expression": text(), "occurrences": arr(OCCURRENCE)})),
             "naming": obj({"patterns": arr(text()), "style": text(), "examples": arr(text())}),
             "narrative_composition": arr(obj({"kind": {"type": "string", "enum": ["action", "description", "psychology", "exposition"]},
                                               "assessment": text(), **PROVENANCE})),
             "observations": arr(obj({"aspect": text(), "finding": text(), **PROVENANCE}))})
SIGNAL = obj({"chapter": {"type": ["integer", "null"], "minimum": 1}, "climax": text(), "payoffs": arr(text()),
              "foreshadowing": arr(text()), "pacing": text(), "emotion": text(), **PROVENANCE})
REFERENCE_V2_SCHEMA = obj({
    "plot": obj({"schema_version": {"type": "integer", "enum": [2]}, "logline": text(), "events": arr(EVENT), "plotlines": arr(PLOTLINE)}),
    "characters": arr(CHARACTER), "settings": arr(SETTING), "style": STYLE,
    "rhythm": obj({"schema_version": {"type": "integer", "enum": [2]}, "chapter_analysis": arr(SIGNAL)}),
    "opening": obj({"schema_version": {"type": "integer", "enum": [2]}, "pattern": text(), "observations": arr(obj({"finding": text(), **PROVENANCE}))}),
    "era_background": obj({"schema_version": {"type": "integer", "enum": [2]}, "items": arr(ERA_ITEM)}),
})
STAGE_SECTIONS = {"plot": ("plot",), "characters": ("characters",), "settings": ("settings",),
                  "language": ("style", "rhythm", "opening", "era_background")}


def canonical_stage_schema(stage: str, version: int = 3) -> dict:
    if stage not in STAGE_SECTIONS:
        raise DataError(422, "未知固定提取阶段")
    if version not in (2, 3):
        raise DataError(422, "未知分析内容合同版本")
    contract = REFERENCE_V2_SCHEMA if version == 2 else REFERENCE_V3_SCHEMA
    schema = obj({name: copy.deepcopy(contract["properties"][name]) for name in STAGE_SECTIONS[stage]})
    return schema if version == 2 else require_source_schema(schema)


def _validate(value: Any, schema: dict, path: str) -> None:
    kind = schema["type"]
    kinds = kind if isinstance(kind, list) else [kind]
    valid = ((value is None and "null" in kinds) or (type(value) is bool and "boolean" in kinds)
             or (type(value) is int and "integer" in kinds) or (type(value) in (int, float) and "number" in kinds)
             or (isinstance(value, str) and "string" in kinds) or (isinstance(value, list) and "array" in kinds)
             or (isinstance(value, dict) and "object" in kinds))
    if not valid:
        raise DataError(422, f"{path} 数据类型无效")
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        raise DataError(422, f"{path} 选项无效")
    if isinstance(value, dict):
        fields = schema["properties"]
        missing, extra = set(schema.get("required", [])) - value.keys(), value.keys() - fields.keys()
        if missing or extra:
            detail = "缺少 " + ",".join(sorted(missing)) if missing else "未知 " + ",".join(sorted(extra))
            raise DataError(422, f"{path}：{detail}")
        for key, child in value.items():
            _validate(child, fields[key], path + "." + key)
    elif isinstance(value, list):
        if len(value) > schema.get("maxItems", 10000):
            raise DataError(422, f"{path} 条目过多")
        for index, child in enumerate(value):
            _validate(child, schema["items"], f"{path}[{index}]")
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1000000):
            raise DataError(422, f"{path} 文本长度无效")
    elif type(value) in (int, float) and ((type(value) is float and not math.isfinite(value)) or value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf"))):
        raise DataError(422, f"{path} 数值范围无效")


def is_v2(section: str, value: Any) -> bool:
    return (isinstance(value, dict) and value.get("schema_version") == 2
            or section in {"characters", "settings"} and isinstance(value, list)
            and any(isinstance(item, dict) and item.get("schema_version") == 2 for item in value))


def validate_v2_section(section: str, value: Any, source_text: str | None = None, *, source_version_id: str = "", offset_base: int = 0) -> Any:
    if section not in REFERENCE_V2_SCHEMA["properties"]:
        raise DataError(422, "未知分析分区")
    _validate(value, REFERENCE_V2_SCHEMA["properties"][section], section)
    result = copy.deepcopy(value)
    def walk(node):
        if isinstance(node, dict):
            if "evidence" in node:
                quote = node["evidence"]
                if not quote.strip() or source_text is not None and quote not in source_text:
                    raise DataError(422, "evidence 必须为输入原文中的非空连续片段")
                anchor = node["revealed_at"]
                if source_text is not None and anchor["offset"] is not None and (anchor["offset"] < offset_base or source_text[anchor["offset"] - offset_base:anchor["offset"] - offset_base + len(quote)] != quote):
                    raise DataError(422, "evidence 与 revealed_at.offset 不一致")
                if source_version_id:
                    if node["source_version_id"] not in ("", source_version_id):
                        raise DataError(422, "分析证据的 source_version_id 与当前正文不一致")
                    node["source_version_id"] = source_version_id
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(result)
    state_registry = {}
    def unique(node):
        if isinstance(node, dict):
            if "attribute" in node and "id" in node:
                previous = state_registry.get(node["id"])
                if previous is not None and previous != node:
                    raise DataError(422, "同一状态 ID 对应不同事实，请为变化分配新 ID")
                state_registry[node["id"]] = node
            if "story_time" in node and not node["story_time"]["label"].strip() and node["story_time"]["sort_key"] is not None:
                raise DataError(422, "未知故事时间不能填写排序时间")
            for child in node.values(): unique(child)
        elif isinstance(node, list):
            identities = [item["id"] for item in node if isinstance(item, dict) and "id" in item]
            if len(identities) != len(set(identities)):
                raise DataError(422, "同一事实列表中 ID 重复")
            for child in node: unique(child)
    unique(result)
    return result


def validate_stage_result(stage: str, value: Any, source_text: str, source_version_id: str = "", known_entities: dict | None = None, *, offset_base: int = 0) -> dict:
    versions = {item.get("schema_version") for child in value.values() for item in (child if isinstance(child, list) else [child]) if isinstance(item, dict) and item.get("schema_version") in (2, 3)} if isinstance(value, dict) else set()
    if len(versions) > 1: raise DataError(422, "同一阶段不能混用分析合同版本")
    version = next(iter(versions), 3)
    _validate(value, canonical_stage_schema(stage, version), stage)
    validator = validate_v2_section if version == 2 else validate_v3_section
    kwargs = {} if version == 2 else {"require_evidence": True}
    result = {key: validator(key, item, source_text, source_version_id=source_version_id, offset_base=offset_base, **kwargs) for key, item in value.items()}
    validate_links(result, known_entities or {})
    return result


def _ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value)
    return {item if isinstance(item, str) else item.get("id", "") for item in value or [] if isinstance(item, (str, dict))} - {""}


def validate_links(sections: dict, known: dict | None = None) -> None:
    """References must resolve inside this candidate or its explicitly supplied context."""
    known = known or {}
    plot = sections.get("plot", {})
    pools = {"events": _ids(known.get("events")) | _ids(plot.get("events")),
             "plotlines": _ids(known.get("plotlines")) | _ids(plot.get("plotlines")),
             "characters": _ids(known.get("characters")) | _ids(sections.get("characters")),
             "settings": _ids(known.get("settings")) | _ids(sections.get("settings")), "states": set()}
    for key, seq in (("events", plot.get("events", [])), ("plotlines", plot.get("plotlines", [])),
                     ("characters", sections.get("characters", [])), ("settings", sections.get("settings", []))):
        ids = [item["id"] for item in seq if item.get("id") and item.get("schema_version", 2) in (2, 3)]
        if len(ids) != len(set(ids)):
            raise DataError(422, f"{key} ID 重复")
    def collect(node):
        if isinstance(node, dict):
            if "attribute" in node and "id" in node:
                pools["states"].add(node["id"])
            for item in node.values(): collect(item)
        elif isinstance(node, list):
            for item in node: collect(item)
    collect(sections)
    pools["states"] |= _ids(known.get("states"))
    links = {"participants": "characters", "character_ids": "characters", "character_id": "characters", "setting_ids": "settings",
             "causes": "events", "results": "events", "event_ids": "events", "event_id": "events", "plotline_ids": "plotlines", "supersedes": "states", "earlier_setting_id": "settings"}
    def check(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in links:
                    values = child if isinstance(child, list) else [child] if child else []
                    if any(item not in pools[links[key]] for item in values):
                        raise DataError(422, f"{key} 引用了未知 ID")
                    if node.get("id") in values and key in {"causes", "results", "supersedes", "earlier_setting_id"}:
                        raise DataError(422, f"{key} 不可引用自身")
                check(child)
        elif isinstance(node, list):
            for child in node: check(child)
    check(sections)
    known_settings = known.get("settings", [])
    all_settings = list(known_settings.values()) if isinstance(known_settings, dict) else [item for item in known_settings if isinstance(item, dict)]
    all_settings = {item["id"]: item for item in [*all_settings, *sections.get("settings", [])] if isinstance(item, dict) and item.get("id")}
    validate_setting_ancestry(list(all_settings.values()))
    # An event may have multiple causes, but cannot ultimately cause itself.
    edges = {event["id"]: set(event.get("results", [])) for event in plot.get("events", [])}
    for event in plot.get("events", []):
        for cause in event.get("causes", []): edges.setdefault(cause, set()).add(event["id"])
    from collections import deque
    indegree = {identity: 0 for identity in edges}
    for children in edges.values():
        for child in children: indegree[child] = indegree.get(child, 0) + 1
    queue = deque(identity for identity, count in indegree.items() if count == 0)
    visited = 0
    while queue:
        identity = queue.popleft()
        visited += 1
        for child in edges.get(identity, []):
            indegree[child] -= 1
            if indegree[child] == 0: queue.append(child)
    if visited != len(indegree): raise DataError(422, "剧情因果关联形成循环")

# Version 3 keeps source anchors internal, while all visible temporal fields use
# a single narrative-progress coordinate. V2 schemas remain immutable above.
from .character_contract import (CHARACTER_SCHEMA as CHARACTER_V3, PROVENANCE as PROVENANCE_V3,
                                 PROGRESS, STORY_TIME as STORY_TIME_V3, RANGE as PROGRESS_RANGE)
from .progress import narrative_progress, finite_progress, prepare_progress_sources
import hashlib
import math


def fact3(fields: dict) -> dict:
    return obj({**fields, **PROVENANCE_V3}, tuple(fields))


EVENT_V3 = fact3({"id": ID, "name": text(1000, empty=False), "theme": text(1000), "description": text(),
                 "narrative_progress": PROGRESS, "story_time": STORY_TIME_V3, "participants": IDS,
                 "setting_ids": IDS, "causes": IDS, "results": IDS, "plotline_ids": IDS, "volume": NULL_TEXT})
STAGE_V3 = fact3({"id": ID, "goal": text(), "status": {"type": "string", "enum": ["open", "progressing", "resolved", "abandoned"]},
                 "event_ids": IDS, "narrative_progress": PROGRESS, "story_time": STORY_TIME_V3})
SETTING_SNAPSHOT_V3 = fact3({"id": ID, "narrative_range": PROGRESS_RANGE, "story_time": STORY_TIME_V3,
                            "event_ids": IDS, "content": text()})
SETTING_V3 = fact3({"schema_version": {"type": "integer", "enum": [3]}, "id": ID,
                   "title": text(1000, empty=False), "category": copy.deepcopy(SETTING["properties"]["category"]),
                   "content": text(), "earlier_setting_id": NULL_TEXT, "narrative_progress": PROGRESS,
                   "story_time": STORY_TIME_V3, "snapshots": arr(SETTING_SNAPSHOT_V3)})
APPEARANCE_V3 = fact3({"narrative_progress": PROGRESS, "story_time": STORY_TIME_V3, "event_ids": IDS,
                       "character_ids": IDS, "context": text(), "usage": copy.deepcopy(OCCURRENCE["properties"]["usage"])})
ERA_ITEM_V3 = obj({"id": ID, "kind": copy.deepcopy(ERA_ITEM["properties"]["kind"]),
                  "title": text(1000, empty=False), "content": text(), "approximate_time": text(1000),
                  "appearances": arr(APPEARANCE_V3)})


def _language_schema3(schema: dict) -> dict:
    result = copy.deepcopy(schema)
    def walk(node):
        if not isinstance(node, dict): return
        properties = node.get("properties", {})
        if "schema_version" in properties: properties["schema_version"]["enum"] = [3]
        if set(("revealed_at", "source_version_id", "evidence")) <= properties.keys():
            properties["narrative_progress"] = copy.deepcopy(PROGRESS)
            properties["story_time"] = copy.deepcopy(STORY_TIME_V3)
            properties["origin"] = copy.deepcopy(PROVENANCE_V3["origin"])
            node["required"] = [key for key in node.get("required", []) if key not in PROVENANCE_V3 and key != "chapter"] + ["narrative_progress", "story_time"]
            # Chapter remains part of source proof, not a second UI time field.
            properties.pop("chapter", None)
        for child in properties.values(): walk(child)
        if "items" in node: walk(node["items"])
    walk(result)
    return result


REFERENCE_V3_SCHEMA = obj({
    "plot": obj({"schema_version": {"type": "integer", "enum": [3]}, "logline": text(), "events": arr(EVENT_V3),
                 "plotlines": arr(obj({"id": ID, "title": text(1000, empty=False), "goal": text(), "stages": arr(STAGE_V3)}))}),
    "characters": arr(CHARACTER_V3), "settings": arr(SETTING_V3),
    "style": _language_schema3(STYLE),
    "rhythm": _language_schema3(REFERENCE_V2_SCHEMA["properties"]["rhythm"]),
    "opening": _language_schema3(REFERENCE_V2_SCHEMA["properties"]["opening"]),
    "era_background": obj({"schema_version": {"type": "integer", "enum": [3]}, "items": arr(ERA_ITEM_V3)}),
})
SCHEMA_VERSION = 3


def require_source_schema(schema: dict) -> dict:
    """Provider contracts require proofs; human business records can omit them."""
    result = copy.deepcopy(schema)
    def walk(node):
        if not isinstance(node, dict): return
        properties = node.get("properties", {})
        if {"revealed_at", "source_version_id", "evidence"} <= properties.keys():
            node["required"] = list(dict.fromkeys([*node.get("required", []), "revealed_at", "source_version_id", "evidence"]))
        for child in properties.values(): walk(child)
        if "items" in node: walk(node["items"])
    walk(result)
    return result


def is_v3(section: str, value: Any) -> bool:
    return (isinstance(value, dict) and value.get("schema_version") == 3
            or section in {"characters", "settings"} and isinstance(value, list)
            and any(isinstance(item, dict) and item.get("schema_version") == 3 for item in value))


def is_modern(section: str, value: Any) -> bool:
    return is_v2(section, value) or is_v3(section, value)


def _proof3(node: dict) -> dict:
    return {key: copy.deepcopy(node[key]) for key in PROVENANCE_V3 if key in node and (key != "evidence" or node[key])}


def _stable_legacy_id(kind, node, label, index=0):
    return node.get("id") or f"{kind}-legacy-" + hashlib.sha256(f"{label}\0{index}".encode()).hexdigest()[:20]


def _progress3(node: dict, sources: dict | None = None) -> float:
    source = (sources or {}).get(node.get("source_version_id"))
    anchor = node.get("revealed_at")
    derived = narrative_progress(source, anchor) if source else None
    if derived is None and source and node.get("evidence"):
        quote, body = node["evidence"], str(source.get("text", ""))
        offset = body.find(quote)
        if offset >= 0 and body.find(quote, offset + 1) < 0:
            derived = narrative_progress(source, {"chapter": None, "offset": offset})
    return derived if derived is not None else float(node.get("narrative_progress", 0)) if finite_progress(node.get("narrative_progress")) else 0.0


def _time3(value):
    return (value.get("label") or None) if isinstance(value, dict) else value if isinstance(value, str) and value.strip() else None


def _fact3(node: dict, sources=None) -> dict:
    return {"narrative_progress": _progress3(node, sources), "story_time": _time3(node.get("story_time")), **_proof3(node)}


def adapt_v2_section(section: str, value: Any, *, source_versions: dict | None = None) -> Any:
    """Lossless at the storage/history layer: this is only a new view/copy.

    Unknown dates, ages, categories and identities are left empty. Existing
    source anchors survive migration as hidden audit fields.
    """
    if is_v3(section, value): return copy.deepcopy(value)
    sources = prepare_progress_sources(source_versions)
    if section == "plot":
        events = list(value.get("events", []))
        for epoch in value.get("epochs", []):
            for period in epoch.get("periods", []):
                for original in period.get("events", []):
                    event = copy.deepcopy(original)
                    event.setdefault("story_time", event.get("time_marker") or period.get("time"))
                    event.setdefault("volume", epoch.get("title"))
                    events.append(event)
        converted = []
        for index, node in enumerate(events):
            label = node.get("name") or node.get("title") or node.get("subject") or "事件"
            converted.append({"id": _stable_legacy_id("event", node, label, index), "name": label,
                              "theme": node.get("theme") or node.get("category") or "", "description": node.get("description") or node.get("text") or "",
                              "participants": node.get("participants", []), "setting_ids": node.get("setting_ids", []),
                              "causes": node.get("causes", []), "results": node.get("results", []), "plotline_ids": node.get("plotline_ids", []),
                              "volume": node.get("volume"), **_fact3(node, sources)})
        lines = []
        for line in value.get("plotlines", []):
            lines.append({key: copy.deepcopy(line[key]) for key in ("id", "title", "goal") if key in line} | {
                "stages": [{key: copy.deepcopy(stage[key]) for key in ("id", "goal", "status", "event_ids")} | _fact3(stage, sources) for stage in line.get("stages", [])]})
        return {"schema_version": 3, "logline": value.get("logline", ""), "events": converted, "plotlines": lines}
    if section == "characters":
        from .character_contract import TEMPERAMENTS
        result = []
        for index, node in enumerate(value):
            if node.get("schema_version") == 3: result.append(copy.deepcopy(node)); continue
            name = node.get("name") or "未命名角色"
            initial = {"gender": node.get("gender") if node.get("gender") in ("男", "女", "特殊") else None,
                       "age": str(node.get("age") or ""), "identity": "", "ability": "", "personality": "", "appearance": "",
                       "relationships": [], "narrative_progress": _progress3(node, sources), "story_time": _time3(node.get("story_time"))}
            facts = list(node.get("states", []))
            for experience in node.get("experiences", []):
                facts.extend(experience.get("changes", []))
            seen, snapshots, latest = set(), [], {key: copy.deepcopy(initial[key]) for key in ("identity", "ability", "personality", "appearance", "relationships")}
            latest["visual_design"] = node.get("visual_design", "")
            for order, fact in enumerate(sorted(facts, key=lambda item: (_progress3(item, sources), (item.get("revealed_at") or {}).get("chapter") or 0, (item.get("revealed_at") or {}).get("offset") or 0))):
                sid = _stable_legacy_id("snapshot", fact, name, order)
                if sid in seen: continue
                seen.add(sid)
                attribute = fact.get("attribute")
                field = {"personality_tendency": "personality", "appearance": "appearance", "visual_design": "visual_design", "ability": "ability", "identity": "identity", "personality": "personality", "goal": "identity", "faction": "identity"}.get(attribute)
                if field: latest[field] = fact.get("value", "")
                if attribute in ("relationship", "relationships"):
                    latest["relationships"] = [{"character_id": cid, "relationship": fact.get("value", "")} for cid in fact.get("character_ids", [])]
                snapshots.append({"id": sid, "narrative_range": {"start": _progress3(fact, sources), "end": None},
                                  "story_time": _time3(fact.get("story_time")), "event_ids": [fact["event_id"]] if fact.get("event_id") else [], **copy.deepcopy(latest), **_proof3(fact)})
            for experience in node.get("experiences", []):
                if not experience.get("event_id"): continue
                matching = next((item for item in snapshots if experience["event_id"] in item["event_ids"]), None)
                if matching is None:
                    snapshots.append({"id": _stable_legacy_id("experience", experience, name, len(snapshots)),
                                      "narrative_range": {"start": _progress3(experience, sources), "end": None}, "story_time": _time3(experience.get("story_time")),
                                      "event_ids": [experience["event_id"]], **copy.deepcopy(latest), **_proof3(experience)})
            snapshots.sort(key=lambda item: item["narrative_range"]["start"])
            for current, following in zip(snapshots, snapshots[1:]): current["narrative_range"]["end"] = following["narrative_range"]["start"]
            result.append({"schema_version": 3, "id": _stable_legacy_id("character", node, name, index), "name": name,
                           "aliases": node.get("aliases", []), "role_tag": node.get("role_tag", ""),
                           "temperament": node.get("temperament", "") if node.get("temperament") in TEMPERAMENTS else "",
                           "visual_design": node.get("visual_design", ""), "initial_state": initial, "snapshots": snapshots, **_proof3(node)})
        return result
    if section == "settings":
        result = []
        for index, node in enumerate(value):
            title = node.get("title") or "未命名设定"
            if node.get("schema_version") == 3: result.append(copy.deepcopy(node)); continue
            snapshots = []
            for order, fact in enumerate(node.get("states", node.get("updates", []))):
                snapshots.append({"id": _stable_legacy_id("setting-snapshot", fact, title, order),
                                  "narrative_range": {"start": _progress3(fact, sources), "end": None}, "story_time": _time3(fact.get("story_time")),
                                  "event_ids": [fact["event_id"]] if fact.get("event_id") else [], "content": fact.get("value") or fact.get("text") or "", **_proof3(fact)})
            snapshots.sort(key=lambda item: item["narrative_range"]["start"])
            for current, following in zip(snapshots, snapshots[1:]): current["narrative_range"]["end"] = following["narrative_range"]["start"]
            result.append({"schema_version": 3, "id": _stable_legacy_id("setting", node, title, index), "title": title,
                           "category": node.get("category", "other"), "content": node.get("content", ""), "earlier_setting_id": None,
                           "snapshots": snapshots, **_fact3(node, sources)})
        return result
    if section == "era_background":
        items = []
        for node in value.get("items", []):
            time_range = node.get("time_range", {})
            times = [time_range.get("start"), time_range.get("end")]
            items.append({"id": node["id"], "kind": node.get("kind", "meme"), "title": node.get("expression", ""),
                          "content": node.get("meaning", ""), "approximate_time": " — ".join(dict.fromkeys(item for item in times if item)),
                          "appearances": [{"event_ids": [], "character_ids": [hit["character_id"]] if hit.get("character_id") else [],
                                           "context": hit.get("context", ""), "usage": hit.get("usage", "reference"), **_fact3(hit, sources)} for hit in node.get("occurrences", [])]})
        return {"schema_version": 3, "items": items}
    if section in ("style", "rhythm", "opening"):
        result = copy.deepcopy(value)
        if value.get("schema_version") != 2:
            if section == "style":
                result = {"schema_version": 3, "narrative_person": "", "focalization": "", "register": "", "colloquialism": "",
                          "rhetoric": [], "dialogue_style": "", "catchphrases": [], "naming": {"patterns": [], "style": "", "examples": []},
                          "narrative_composition": [], "observations": []}
            elif section == "rhythm": result = {"schema_version": 3, "chapter_analysis": []}
            else: result = {"schema_version": 3, "pattern": value.get("pattern", ""), "observations": []}
            if value: result["legacy"] = copy.deepcopy(value)
        def walk(node):
            if isinstance(node, dict):
                if "schema_version" in node: node["schema_version"] = 3
                if "evidence" in node:
                    node.update(_fact3(node, sources)); node.pop("chapter", None)
                for key, child in list(node.items()):
                    if key not in {"legacy", "deterministic_statistics"}: walk(child)
            elif isinstance(node, list):
                for child in node: walk(child)
        walk(result)
        if result: result["schema_version"] = 3
        return result
    return copy.deepcopy(value)


def normalize_v3_section(section: str, value: Any, *, manual: bool = False, source_versions: dict | None = None) -> Any:
    source_versions = prepare_progress_sources(source_versions)
    result = adapt_v2_section(section, value, source_versions=source_versions)
    schema = REFERENCE_V3_SCHEMA["properties"].get(section)
    if schema is None: return result
    def walk(node, spec):
        if isinstance(node, dict):
            properties = spec.get("properties", {})
            if {"revealed_at", "source_version_id", "evidence"} <= properties.keys():
                if manual and not node.get("evidence"):
                    for field in ("revealed_at", "source_version_id", "evidence"): node.pop(field, None)
                    node["origin"] = "manual"
                elif node.get("evidence"):
                    node.setdefault("origin", "source")
                    if not manual and source_versions and node.get("origin") != "manual":
                        source = source_versions.get(node.get("source_version_id"))
                        if source:
                            anchor = node.get("revealed_at") or {}
                            point = narrative_progress(source, anchor)
                            if point is None and node.get("evidence"):
                                quote, body = node["evidence"], str(source.get("text", ""))
                                start = body.find(quote)
                                if start >= 0 and body.find(quote, start + 1) < 0: point = narrative_progress(source, {"offset": start})
                            if point is None: raise DataError(422, "原文定位不唯一，无法确定叙事进度")
                        progress = _progress3(node, source_versions)
                        if "narrative_progress" in node: node["narrative_progress"] = progress
                        if "narrative_range" in node: node["narrative_range"]["start"] = progress
            for key, child in list(node.items()):
                if key in properties: walk(child, properties[key])
        elif isinstance(node, list):
            for child in node: walk(child, spec.get("items", {}))
    walk(result, schema)
    if section == "characters" and not manual and source_versions:
        for person in result:
            if person.get("origin") != "manual" and person.get("source_version_id") in source_versions:
                person["initial_state"]["narrative_progress"] = _progress3(person, source_versions)
    return result


def validate_v3_section(section: str, value: Any, source_text: str | None = None, *, source_version_id: str = "", offset_base: int = 0, require_evidence: bool = False) -> Any:
    schema = REFERENCE_V3_SCHEMA["properties"].get(section)
    if schema is None: raise DataError(422, "未知分析分区")
    _validate(value, require_source_schema(schema) if require_evidence else schema, section)
    result = copy.deepcopy(value)
    def walk(node):
        if isinstance(node, dict):
            if "evidence" in node:
                quote = node["evidence"]
                if not quote.strip() or source_text is not None and quote not in source_text:
                    raise DataError(422, "evidence 必须为输入原文中的非空连续片段")
                anchor = node.get("revealed_at")
                if not isinstance(anchor, dict): raise DataError(422, "分析证据缺少内部原文定位")
                if source_text is not None and anchor["offset"] is not None and (anchor["offset"] < offset_base or source_text[anchor["offset"] - offset_base:anchor["offset"] - offset_base + len(quote)] != quote):
                    raise DataError(422, "evidence 与 revealed_at.offset 不一致")
                if source_version_id:
                    if node.get("source_version_id") not in ("", source_version_id): raise DataError(422, "分析证据的 source_version_id 与当前正文不一致")
                    node["source_version_id"] = source_version_id
                if require_evidence: node["origin"] = "source"
            if "narrative_range" in node:
                span = node["narrative_range"]
                if span["end"] is not None and span["end"] < span["start"]: raise DataError(422, "阶段叙事进度的结束不能早于开始")
            for child in node.values(): walk(child)
        elif isinstance(node, list):
            identities = [item["id"] for item in node if isinstance(item, dict) and "id" in item]
            if len(identities) != len(set(identities)): raise DataError(422, "同一事实列表中 ID 重复")
            for child in node: walk(child)
    walk(result)
    return result


def validate_setting_ancestry(settings: list[dict]) -> None:
    parents = {item["id"]: item.get("earlier_setting_id") for item in settings if item.get("id")}
    positions = {item["id"]: item.get("narrative_progress") for item in settings if item.get("id")}
    for identity, parent in parents.items():
        if parent in positions and finite_progress(positions.get(identity)) and finite_progress(positions[parent]) and positions[parent] > positions[identity]:
            raise DataError(422, "追加设定必须关联更早出现的设定")
    for identity in parents:
        seen, cursor = set(), identity
        while cursor in parents and cursor is not None:
            if cursor in seen: raise DataError(422, "追加设定关联形成循环")
            seen.add(cursor)
            cursor = parents[cursor]
