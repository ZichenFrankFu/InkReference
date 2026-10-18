"""Read-only two-clock views over approved reference facts.

The upstream chronicle/character-experience mechanism is retained, with stable
IDs and disclosure-aware projection. No derived view changes the saved source.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .text_processing import cn2int
from .language_analysis import era_statistics


def objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def chapter_number(value: Any) -> int | None:
    if isinstance(value, bool): return None
    if isinstance(value, int): return value
    match = re.search(r"(?:第\s*)?([零〇一二两三四五六七八九十百千万0-9０-９]+)\s*(?:章|回|节|话)?", str(value or ""))
    return cn2int(match[1]) if match else None


def _disclosure(item: dict, fallback: str = "first_chapter") -> int | None:
    anchor = item.get("revealed_at")
    return chapter_number(anchor.get("chapter")) if isinstance(anchor, dict) else chapter_number(item.get(fallback))


def _visible(item, chapter, fallback="first_chapter", strict=False):
    number = _disclosure(item, fallback)
    return chapter is None or number is not None and number <= chapter or number is None and not strict


def _narrative_key(item):
    anchor = item.get("revealed_at") or {}
    number = _disclosure(item)
    offset = anchor.get("offset")
    return (number if number is not None else float("inf"), offset if offset is not None else float("inf"), item.get("id", ""))


def _story_key(item):
    if "story_time" not in item:
        # Legacy epochs/periods already encode their explicit story order.
        return (True, "", ())
    key = (item.get("story_time") or {}).get("sort_key")
    return (key is None, key or "", _narrative_key(item))


def _states(values: list[dict], chapter: int | None):
    states = sorted([copy.deepcopy(item) for item in values if _visible(item, chapter, strict=True)], key=_narrative_key)
    hidden = {identity for item in states for identity in item.get("supersedes", [])}
    current = {}
    for item in states:
        if item.get("id") not in hidden:
            current[item.get("attribute", "")] = copy.deepcopy(item)
    return states, current


def _outline(events: list[dict]) -> str:
    return "\n".join(f"{item.get('name', '')}：{item.get('description', '')}".rstrip("：") for item in events)


def project_sections(sections: dict, chapter: int | None = None, *, volumes: list[dict] | None = None) -> dict:
    if chapter is not None and (type(chapter) is not int or chapter < 1):
        from .database import DataError
        raise DataError(422, "章节需为正整数")
    result = copy.deepcopy(sections)
    plot = result.setdefault("plot", {})
    v2 = plot.get("schema_version") == 2
    events = []
    epochs = []
    for epoch in objects(plot.get("epochs")):
        periods = []
        for period in objects(epoch.get("periods")):
            legacy = [event for event in objects(period.get("events")) if _visible(event, chapter)]
            if legacy or chapter is None:
                periods.append({**period, "events": legacy})
                events.extend(legacy)
        if periods or chapter is None: epochs.append({**epoch, "periods": periods})
    if "epochs" in plot: plot["epochs"] = epochs
    if v2:
        events += [item for item in objects(plot.get("events")) if _visible(item, chapter, strict=True)]
        visible_ids = {item.get("id") for item in events if item.get("id")}
        plot["events"] = [item for item in events if item.get("id")]
        lines = []
        for line in objects(plot.get("plotlines")):
            stages = [stage for stage in objects(line.get("stages")) if _visible(stage, chapter, strict=True)]
            if not stages and chapter is not None: continue
            for stage in stages:
                stage["event_ids"] = [identity for identity in stage.get("event_ids", []) if identity in visible_ids]
            lines.append({**line, "title": stages[-1].get("goal", "") if chapter is not None and stages else line.get("title", ""), "goal": stages[-1].get("goal", "") if chapter is not None and stages else line.get("goal", ""), "stages": stages,
                          "current_status": stages[-1].get("status", "open") if stages else "open"})
        plot["plotlines"] = lines
        line_ids = {line.get("id") for line in lines}
        for event in events:
            for key in ("causes", "results"):
                event[key] = [identity for identity in event.get(key, []) if identity in visible_ids]
            event["plotline_ids"] = [identity for identity in event.get("plotline_ids", []) if identity in line_ids]
    event_index = {item["id"]: item for item in events if item.get("id")}
    for event in events:
        for identity in event.get("causes", []):
            if identity in event_index and event.get("id"):
                event_index[identity]["results"] = list(dict.fromkeys([*event_index[identity].get("results", []), event["id"]]))
        for identity in event.get("results", []):
            if identity in event_index and event.get("id"):
                event_index[identity]["causes"] = list(dict.fromkeys([*event_index[identity].get("causes", []), event["id"]]))
    # Stage 1 cannot invent IDs owned by later extraction stages. Explicit
    # character experiences and setting states provide safe reverse joins.
    for section, target in (("characters", "participants"), ("settings", "setting_ids")):
        for entity in objects(result.get(section)):
            if not entity.get("id") or not _visible(entity, chapter, strict=entity.get("schema_version") == 2): continue
            facts = [*objects(entity.get("states")), *objects(entity.get("experiences"))]
            for fact in facts:
                event_id = fact.get("event_id")
                if event_id in event_index and _visible(fact, chapter, strict=True):
                    event_index[event_id][target] = list(dict.fromkeys([*event_index[event_id].get(target, []), entity["id"]]))
    narrative = sorted(events, key=_narrative_key)
    story = sorted(events, key=_story_key)
    plot["views"] = {"narrative": copy.deepcopy(narrative), "story": copy.deepcopy(story)}
    grouped = {}
    for event in narrative:
        volume = event.get("volume") or "未分卷"
        if volumes:
            number = _disclosure(event)
            for candidate in volumes:
                start = candidate.get("start_chapter", candidate.get("start"))
                end = candidate.get("end_chapter", candidate.get("end"))
                if number is not None and isinstance(start, int) and isinstance(end, int) and start <= number <= end:
                    volume = candidate.get("title", "未分卷")
                    break
        grouped.setdefault(volume, []).append(event)
    plot["outlines"] = {"book": _outline(narrative), "volumes": [{"volume": key, "outline": _outline(items), "event_ids": [item["id"] for item in items if item.get("id")]} for key, items in grouped.items()]}
    if chapter is not None: plot["logline"] = _outline(narrative)
    events_by_subject = {}
    events_by_id = {event["id"]: event for event in events if event.get("id")}
    for event in events:
        text = str(event.get("description") or event.get("name") or "").strip()
        if event.get("subject") and text:
            events_by_subject.setdefault(str(event["subject"]).strip(), []).append({"chapter": str(event.get("first_chapter") or ""), "text": text})
    people = []
    for original in objects(result.get("characters")):
        modern = original.get("schema_version") == 2
        if not _visible(original, chapter, strict=modern): continue
        person = copy.deepcopy(original)
        owns = bool(objects(person.get("experiences")))
        for key in ("appearance", "personality"):
            if key in person or not modern:
                person[key] = [item for item in objects(person.get(key)) if _visible(item, chapter, "chapter")]
        if modern:
            experiences = [item for item in objects(person.get("experiences")) if _visible(item, chapter, strict=True) and item.get("event_id") in events_by_id]
            all_states = objects(person.get("states"))
            for item in experiences:
                item["changes"] = [change for change in objects(item.get("changes")) if _visible(change, chapter, strict=True)]
                all_states.extend(item["changes"])
                item["event"] = copy.deepcopy(events_by_id[item["event_id"]])
            dedup = {item["id"]: item for item in all_states}
            person["states"], person["current_state"] = _states(list(dedup.values()), chapter)
            person["experiences"] = experiences
            if chapter is not None:
                for key in ("intro", "temperament", "visual_design"):
                    person[key] = person["current_state"].get(key, {}).get("value", "")
        else:
            person["experiences"] = [item for item in objects(person.get("experiences")) if _visible(item, chapter, "chapter")]
            if not owns: person["experiences"] = copy.deepcopy(events_by_subject.get(str(person.get("name") or "").strip(), []))
            if chapter is not None: person["intro"] = "\n".join(item.get("text", "") for item in person["experiences"])
        if chapter is not None:
            for key in ("speech_samples", "role_tag"):
                if key in person: person[key] = [] if key == "speech_samples" else ""
        people.append(person)
    result["characters"] = people
    settings = []
    for original in objects(result.get("settings")):
        modern = original.get("schema_version") == 2
        if not _visible(original, chapter, strict=modern): continue
        setting = copy.deepcopy(original)
        if modern:
            setting["states"], setting["current_state"] = _states(objects(setting.get("states")), chapter)
            if chapter is not None: setting["content"] = "\n".join(f"{key}：{item['value']}" for key, item in setting["current_state"].items())
        else:
            setting["updates"] = [item for item in objects(setting.get("updates")) if _visible(item, chapter, "chapter")]
            if chapter is not None: setting["content"] = "\n".join(item.get("text", "") for item in setting["updates"])
        settings.append(setting)
    result["settings"] = settings
    person_index = {person["id"]: person for person in people if person.get("id")}
    for person in person_index.values():
        person["setting_links"] = []
        for fact in person.get("states", []):
            for setting_id in fact.get("setting_ids", []):
                person["setting_links"].append({"setting_id": setting_id, "event_id": fact.get("event_id"), "attribute": fact.get("attribute"), "revealed_at": copy.deepcopy(fact.get("revealed_at"))})
    for setting in settings:
        if not setting.get("id"): continue
        for fact in setting.get("states", []):
            for character_id in fact.get("character_ids", []):
                if character_id in person_index:
                    link = {"setting_id": setting["id"], "event_id": fact.get("event_id"), "attribute": fact.get("attribute"), "revealed_at": copy.deepcopy(fact.get("revealed_at"))}
                    if link not in person_index[character_id]["setting_links"]:
                        person_index[character_id]["setting_links"].append(link)
    if chapter is not None:
        character_ids = {item["id"] for item in people if item.get("id")}
        setting_ids = {item["id"] for item in settings if item.get("id")}
        def prune_links(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if key in {"participants", "character_ids"} and isinstance(child, list):
                        node[key] = [identity for identity in child if identity in character_ids]
                    elif key == "setting_id" and isinstance(child, str) and child not in setting_ids:
                        node[key] = None
                    elif key == "setting_ids" and isinstance(child, list):
                        node[key] = [identity for identity in child if identity in setting_ids]
                    else: prune_links(child)
            elif isinstance(node, list):
                for child in node: prune_links(child)
        prune_links(result)
    # Global style statements have no safe chapter interpretation. Only anchored
    # judgments and chapter signals survive an as-of view; no full-book fallback.
    if chapter is not None:
        style = result.get("style", {})
        if style.get("schema_version") == 2:
            style = {"schema_version": 2, "observations": [item for item in objects(style.get("observations")) if _visible(item, chapter, strict=True)],
                     "narrative_composition": [item for item in objects(style.get("narrative_composition")) if _visible(item, chapter, strict=True)],
                     "catchphrases": [{**item, "occurrences": [hit for hit in objects(item.get("occurrences")) if _visible(hit, chapter, strict=True)]} for item in objects(style.get("catchphrases"))]}
            result["style"] = style
        else:
            result["style"] = {"chapter_signals": [item for item in objects(style.get("chapter_signals")) if _visible(item, chapter, "chapter", strict=True)]}
        rhythm = result.get("rhythm", {})
        result["rhythm"] = {"schema_version": 2, "chapter_analysis": [item for item in objects(rhythm.get("chapter_analysis")) if _visible(item, chapter, "chapter", strict=True)]} if rhythm.get("schema_version") == 2 else {}
        opening = result.get("opening", {})
        result["opening"] = {"observations": [item for item in objects(opening.get("observations")) if _visible(item, chapter, strict=True)]}
        result.pop("raw_entries", None)
        result.pop("metadata", None)
        result.pop("setting_features", None)
    era = result.get("era_background")
    if isinstance(era, dict):
        items = []
        for item in objects(era.get("items")):
            occurrences = [hit for hit in objects(item.get("occurrences")) if _visible(hit, chapter, strict=True)]
            if occurrences or chapter is None: items.append({**item, "occurrences": occurrences})
        era["items"] = items
        coverage = result.get("style", {}).get("deterministic_statistics", {}).get("coverage", {}).get("characters", 0)
        era["statistics"] = era_statistics(era, people, character_count=coverage)
    return result
