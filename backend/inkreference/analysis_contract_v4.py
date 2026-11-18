"""Fourth, immutable reference content contract and copy-only legacy views.

Original source contributions and v2/v3 provider schemas remain unchanged. V4
uses named character phases, appended setting entries and writer-facing language
judgments. Proof fields are internal and stricter for model contributions.
"""
from __future__ import annotations

import copy
import hashlib
import math
from typing import Any

from .database import DataError
from .character_contract import CHARACTER_V4_SCHEMA, PROVENANCE, PROGRESS, STORY_TIME
from .chronicle_schema import (ID, IDS, NULL_TEXT, REFERENCE_V3_SCHEMA, arr, obj, text,
                                fact3, _validate, _progress3, _proof3, adapt_v2_section,
                                require_source_schema, validate_v3_section)
from .progress import prepare_progress_sources_v4 as prepare_progress_sources, narrative_progress_v4 as narrative_progress

COMPOSITION_KINDS = ("action", "description", "psychology", "exposition")
FOCALIZATIONS = ("第一人称视角", "有限第三人称视角", "全知第三人称视角")
EXAMPLE_JUDGMENT = obj({"description": text(), "examples": arr(text())})
TIME_NODE = fact3({"id": ID, "description": text(), "status": {"type": "string", "enum": ["open", "progressing", "resolved", "abandoned"]},
                   "event_ids": IDS, "narrative_progress": PROGRESS, "story_time": STORY_TIME})
SETTING_V4 = copy.deepcopy(REFERENCE_V3_SCHEMA["properties"]["settings"]["items"])
SETTING_V4["properties"]["schema_version"]["enum"] = [4]
SETTING_V4["properties"].pop("snapshots")
SETTING_V4["required"].remove("snapshots")
STYLE_V4 = obj({"schema_version": {"type": "integer", "enum": [4]},
                "focalization": {"type": ["string", "null"], "enum": [None, *FOCALIZATIONS]},
                "naming_style": text(), "rhetoric": arr(EXAMPLE_JUDGMENT),
                "dialogue_style": EXAMPLE_JUDGMENT,
                "narrative_composition": arr(fact3({"kind": {"type": "string", "enum": list(COMPOSITION_KINDS)},
                    "percentage": {"type": ["number", "null"], "minimum": 0, "maximum": 100}, "assessment": text()}))})
REFERENCE_V4_SCHEMA = copy.deepcopy(REFERENCE_V3_SCHEMA)
REFERENCE_V4_SCHEMA["properties"].update({
    "plot": obj({"schema_version": {"type": "integer", "enum": [4]}, "logline": text(),
                 "events": copy.deepcopy(REFERENCE_V3_SCHEMA["properties"]["plot"]["properties"]["events"]),
                 "plotlines": arr(obj({"id": ID, "title": text(1000, empty=False), "description": text(), "time_nodes": arr(TIME_NODE)}))}),
    "characters": arr(CHARACTER_V4_SCHEMA), "settings": arr(SETTING_V4), "style": STYLE_V4,
})
for _section in ("rhythm", "opening", "era_background"):
    REFERENCE_V4_SCHEMA["properties"][_section]["properties"]["schema_version"]["enum"] = [4]


def is_v4(section: str, value: Any) -> bool:
    return (isinstance(value, dict) and value.get("schema_version") == 4
            or section in {"characters", "settings"} and isinstance(value, list)
            and any(isinstance(item, dict) and item.get("schema_version") == 4 for item in value))


def source_schema_v4(schema: dict) -> dict:
    result = require_source_schema(schema)
    def walk(node):
        if not isinstance(node, dict): return
        props = node.get("properties", {})
        if "percentage" in props: props["percentage"]["type"] = "number"
        if "focalization" in props:
            props["focalization"]["type"] = "string"
            props["focalization"]["enum"] = list(FOCALIZATIONS)
        for child in props.values(): walk(child)
        if "items" in node: walk(node["items"])
    walk(result)
    return result


def _focalization(value: Any, person: str = "") -> str | None:
    if value in FOCALIZATIONS: return value
    value = str(value or "") + " " + str(person or "")
    if "第一" in value or "first" in value.lower(): return FOCALIZATIONS[0]
    if "全知" in value or "omniscient" in value.lower(): return FOCALIZATIONS[2]
    if "有限" in value or "limited" in value.lower(): return FOCALIZATIONS[1]
    return None


def adapt_v3_section(section: str, value: Any, *, source_versions: dict | None = None) -> Any:
    """Return a v4 copy. Never overwrite the original or manufacture judgments."""
    if is_v4(section, value):
        if section in {"characters", "settings"}:
            return [child for item in value for child in (copy.deepcopy([item]) if item.get("schema_version") == 4 else adapt_v3_section(section, [item], source_versions=source_versions))]
        return copy.deepcopy(value)
    old = adapt_v2_section(section, value, source_versions=source_versions)
    if section == "plot":
        result = copy.deepcopy(old)
        result["schema_version"] = 4
        result["plotlines"] = [{"id": line["id"], "title": line.get("title", ""), "description": line.get("goal", ""),
             "time_nodes": [{**{k: copy.deepcopy(v) for k, v in stage.items() if k != "goal"}, "description": stage.get("goal", "")} for stage in line.get("stages", [])]}
             for line in old.get("plotlines", [])]
        return result
    if section == "characters":
        result = copy.deepcopy(old)
        for person in result:
            person["schema_version"] = 4
            for snapshot in person.get("snapshots", []): snapshot.setdefault("name", "")
        return result
    if section == "settings":
        result, used = [], {item.get("id") for item in old}
        for original in old:
            item = copy.deepcopy(original)
            snapshots = item.pop("snapshots", [])
            item.pop("current_snapshot", None)
            item["schema_version"] = 4
            result.append(item)
            parent = item["id"]
            for index, snapshot in enumerate(snapshots):
                identity = "setting-append-" + hashlib.sha256(f"{item['id']}\0{snapshot.get('id', index)}".encode()).hexdigest()[:24]
                if identity in used: continue
                used.add(identity)
                point = snapshot.get("narrative_range", {}).get("start", item.get("narrative_progress", 0))
                result.append({"schema_version": 4, "id": identity, "title": item["title"], "category": item["category"],
                               "content": snapshot.get("content", ""), "earlier_setting_id": parent,
                               "narrative_progress": point, "story_time": snapshot.get("story_time"), **_proof3(snapshot)})
                parent = identity
        return result
    if section == "style":
        naming = old.get("naming", {})
        naming_parts = [naming.get("style", ""), *naming.get("patterns", []), *naming.get("examples", [])]
        composition = {item.get("kind"): item for item in old.get("narrative_composition", [])}
        result = {"schema_version": 4, "focalization": _focalization(old.get("focalization"), old.get("narrative_person", "")),
                  "naming_style": "\n".join(dict.fromkeys(item for item in naming_parts if isinstance(item, str) and item)),
                  "rhetoric": [{"description": item, "examples": []} for item in old.get("rhetoric", []) if isinstance(item, str)],
                  "dialogue_style": {"description": old.get("dialogue_style", ""), "examples": []},
                  "narrative_composition": [{"kind": kind, "percentage": None,
                      "assessment": composition.get(kind, {}).get("assessment", ""), **_proof3(composition.get(kind, {}))} for kind in COMPOSITION_KINDS]}
        if old.get("deterministic_statistics"): result["deterministic_statistics"] = copy.deepcopy(old["deterministic_statistics"])
        return result
    if section in {"rhythm", "opening", "era_background"}:
        result = copy.deepcopy(old)
        result["schema_version"] = 4
        return result
    return copy.deepcopy(value)


def normalize_v4_section(section: str, value: Any, *, manual: bool = False, source_versions: dict | None = None) -> Any:
    sources = prepare_progress_sources(source_versions)
    result = adapt_v3_section(section, value, source_versions=sources)
    schema = REFERENCE_V4_SCHEMA["properties"].get(section)
    if schema is None: return result
    def walk(node, spec):
        if isinstance(node, dict):
            props = spec.get("properties", {})
            if {"revealed_at", "source_version_id", "evidence"} <= props.keys():
                if manual and not node.get("evidence"):
                    for key in ("revealed_at", "source_version_id", "evidence"): node.pop(key, None)
                    node["origin"] = "manual"
                elif node.get("evidence"):
                    node.setdefault("origin", "source")
                    if not manual and sources and node.get("origin") != "manual":
                        source = sources.get(node.get("source_version_id"))
                        if source:
                            point = narrative_progress(source, node.get("revealed_at") or {})
                            if point is None:
                                quote, body = node["evidence"], str(source.get("text", ""))
                                offset = body.find(quote)
                                if offset >= 0 and body.find(quote, offset + 1) < 0: point = narrative_progress(source, {"offset": offset})
                            if point is None: raise DataError(422, "原文定位不唯一，无法确定叙事进度")
                            if "narrative_progress" in node: node["narrative_progress"] = point
                            if "narrative_range" in node: node["narrative_range"]["start"] = point
                            if "initial_state" in node: node["initial_state"]["narrative_progress"] = point
            for key, child in node.items():
                if key in props: walk(child, props[key])
        elif isinstance(node, list):
            for child in node: walk(child, spec.get("items", {}))
    walk(result, schema)
    if section == "characters":
        for person in result:
            person.setdefault("aliases", [])
            for key in ("role_tag", "temperament", "visual_design"): person.setdefault(key, "")
            initial = person.setdefault("initial_state", {})
            initial.setdefault("gender", None)
            for key in ("age", "identity", "ability", "personality", "appearance"): initial.setdefault(key, "")
            initial.setdefault("narrative_progress", 0)
            initial.setdefault("story_time", None)
            initial.setdefault("relationships", [])
            previous = copy.deepcopy(initial["relationships"])
            for state in sorted(person.get("snapshots", []), key=lambda item: (item.get("narrative_range", {}).get("start", 0), item.get("id", ""))):
                state.setdefault("name", "")
                for key in ("identity", "ability", "personality", "appearance", "visual_design"): state.setdefault(key, "")
                state.setdefault("event_ids", [])
                state.setdefault("story_time", None)
                if "relationships" not in state: state["relationships"] = copy.deepcopy(previous)
                previous = copy.deepcopy(state["relationships"])
    return result


def validate_v4_section(section: str, value: Any, source_text: str | None = None, *, source_version_id: str = "", offset_base: int = 0, require_evidence: bool = False) -> Any:
    schema = REFERENCE_V4_SCHEMA["properties"].get(section)
    if schema is None: raise DataError(422, "未知分析分区")
    _validate(value, source_schema_v4(schema) if require_evidence else schema, section)
    result = copy.deepcopy(value)
    # Reuse v3's exact proof/offset and stable fact ID validation without changing
    # its public strict shape: the generic walk is factored through a v3 event.
    def walk(node):
        if isinstance(node, dict):
            if "evidence" in node:
                event = {"id": "proof", "name": "proof", "theme": "", "description": "", "narrative_progress": 0,
                         "story_time": None, "participants": [], "setting_ids": [], "causes": [], "results": [], "plotline_ids": [], "volume": None,
                         **{key: copy.deepcopy(node[key]) for key in PROVENANCE if key in node}}
                checked = validate_v3_section("plot", {"schema_version": 3, "logline": "", "events": [event], "plotlines": []}, source_text,
                           source_version_id=source_version_id, offset_base=offset_base, require_evidence=require_evidence)["events"][0]
                for key in PROVENANCE:
                    if key in checked: node[key] = checked[key]
            if "narrative_range" in node:
                span = node["narrative_range"]
                if span["end"] is not None and span["end"] < span["start"]: raise DataError(422, "阶段叙事进度的结束不能早于开始")
            for child in node.values(): walk(child)
        elif isinstance(node, list):
            ids = [item["id"] for item in node if isinstance(item, dict) and "id" in item]
            if len(ids) != len(set(ids)): raise DataError(422, "同一事实列表中 ID 重复")
            for child in node: walk(child)
    walk(result)
    if section == "style":
        composition = result["narrative_composition"]
        if len(composition) != 4 or {item["kind"] for item in composition} != set(COMPOSITION_KINDS): raise DataError(422, "叙述构成需包含四类且不可重复")
        values = [item["percentage"] for item in composition]
        if not all(item is None for item in values):
            if any(item is None for item in values) or not math.isclose(sum(values), 100, abs_tol=.0001): raise DataError(422, "四类叙述构成百分比合计需为100%")
        elif require_evidence: raise DataError(422, "模型需提供四类叙述构成百分比")
        for judgment in [result["dialogue_style"], *result["rhetoric"]]:
            if require_evidence and judgment["description"].strip() and not judgment["examples"]: raise DataError(422, "对白风格与修辞习惯需提供原文示例")
            if source_text is not None and any(not example.strip() or example not in source_text for example in judgment["examples"]): raise DataError(422, "语言风格示例必须为输入原文中的非空连续片段")
    return result
