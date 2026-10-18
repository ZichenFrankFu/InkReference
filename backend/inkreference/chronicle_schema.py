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


def canonical_stage_schema(stage: str) -> dict:
    if stage not in STAGE_SECTIONS:
        raise DataError(422, "未知固定提取阶段")
    return obj({name: copy.deepcopy(REFERENCE_V2_SCHEMA["properties"][name]) for name in STAGE_SECTIONS[stage]})


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
    elif type(value) in (int, float) and value < schema.get("minimum", -float("inf")):
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
    _validate(value, canonical_stage_schema(stage), stage)
    result = {key: validate_v2_section(key, item, source_text, source_version_id=source_version_id, offset_base=offset_base) for key, item in value.items()}
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
        ids = [item["id"] for item in seq if item.get("id") and item.get("schema_version", 2) == 2]
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
             "causes": "events", "results": "events", "event_ids": "events", "event_id": "events", "plotline_ids": "plotlines", "supersedes": "states"}
    def check(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in links:
                    values = child if isinstance(child, list) else [child] if child else []
                    if any(item not in pools[links[key]] for item in values):
                        raise DataError(422, f"{key} 引用了未知 ID")
                    if node.get("id") in values and key in {"causes", "results", "supersedes"}:
                        raise DataError(422, f"{key} 不可引用自身")
                check(child)
        elif isinstance(node, list):
            for child in node: check(child)
    check(sections)
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
