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


def project_sections(sections: dict, chapter: int | None = None, *, volumes: list[dict] | None = None,
                     target_version: int | None = None, source_versions: dict | None = None,
                     progress: float | None = None, legacy_source_version_id: str | None = None) -> dict:
    if target_version == 4 or (target_version is None and any(
        isinstance(value, dict) and value.get("schema_version") == 4 or isinstance(value, list) and any(isinstance(item, dict) and item.get("schema_version") == 4 for item in value)
        for value in sections.values())):
        return _project_v4(sections, chapter, volumes=volumes, source_versions=source_versions, progress=progress, legacy_source_version_id=legacy_source_version_id)
    if target_version == 3 or (target_version is None and any(
        isinstance(value, dict) and value.get("schema_version") == 3 or isinstance(value, list) and any(isinstance(item, dict) and item.get("schema_version") == 3 for item in value)
        for value in sections.values())):
        return _project_v3(sections, chapter, volumes=volumes, source_versions=source_versions, progress=progress, legacy_source_version_id=legacy_source_version_id)
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


def _project_v3(sections: dict, chapter: int | None = None, *, volumes=None, source_versions=None, progress=None, legacy_source_version_id=None) -> dict:
    from .chronicle_schema import adapt_v2_section, normalize_v3_section
    from .progress import finite_progress, ordered_story_events, narrative_progress, prepare_progress_sources
    from .database import DataError
    if chapter is not None and (type(chapter) is not int or chapter < 1): raise DataError(422, "章节需为正整数")
    if progress is not None and not finite_progress(progress): raise DataError(422, "叙事进度需为0–100的数值")
    sources = prepare_progress_sources(source_versions)
    if progress is None and chapter is not None:
        cutoffs = [narrative_progress(source, {"chapter": chapter, "offset": None}) for source in sources.values()]
        cutoffs = [item for item in cutoffs if item is not None]
        if cutoffs: progress = max(cutoffs)
    # Preserve legacy chapter filtering when no immutable source is available.
    base = sections if chapter is None or sources else project_sections(sections, chapter, volumes=volumes, target_version=2)
    result = copy.deepcopy(base)
    legacy_source = sources.get(legacy_source_version_id) if legacy_source_version_id is not None else next(iter(sources.values())) if len(sources) == 1 else None
    def locate_legacy(node, modern=False):
        if isinstance(node, dict):
            modern = modern or node.get("schema_version") == 3
            if not modern and "narrative_progress" not in node:
                anchor = node.get("revealed_at")
                if not isinstance(anchor, dict):
                    number = chapter_number(node.get("first_chapter", node.get("chapter")))
                    anchor = {"chapter": number, "offset": None} if number else None
                source = sources.get(node.get("source_version_id")) if node.get("source_version_id") else legacy_source
                point = narrative_progress(source, anchor) if source and anchor else None
                if point is not None: node["narrative_progress"] = point
            for child in node.values(): locate_legacy(child, modern)
        elif isinstance(node, list):
            for child in node: locate_legacy(child, modern)
    locate_legacy(result)
    for name in ("plot", "characters", "settings", "style", "rhythm", "opening", "era_background"):
        if name in result:
            result[name] = normalize_v3_section(name, result[name], source_versions=sources)
    def visible(node):
        point = node.get("narrative_progress")
        if point is None and isinstance(node.get("narrative_range"), dict): point = node["narrative_range"].get("start")
        return progress is None or finite_progress(point) and point <= progress
    plot = result.setdefault("plot", {"schema_version": 3, "logline": "", "events": [], "plotlines": []})
    events = [event for event in objects(plot.get("events")) if visible(event)]
    event_ids = {event.get("id") for event in events}
    lines = []
    for line in objects(plot.get("plotlines")):
        stages = [stage for stage in objects(line.get("stages")) if visible(stage)]
        if progress is not None and not stages: continue
        for stage in stages: stage["event_ids"] = [eid for eid in stage.get("event_ids", []) if eid in event_ids]
        lines.append({**line, "stages": stages, "current_status": stages[-1].get("status", "open") if stages else "open"})
    line_ids = {line.get("id") for line in lines}
    index = {event["id"]: event for event in events if event.get("id")}
    for event in events:
        for key in ("causes", "results"): event[key] = [identity for identity in event.get(key, []) if identity in event_ids]
        event["plotline_ids"] = [identity for identity in event.get("plotline_ids", []) if identity in line_ids]
        for cause in event.get("causes", []): index[cause]["results"] = list(dict.fromkeys([*index[cause].get("results", []), event["id"]]))
        for effect in event.get("results", []): index[effect]["causes"] = list(dict.fromkeys([*index[effect].get("causes", []), event["id"]]))
    people = []
    for original in objects(result.get("characters")):
        initial = original.get("initial_state", {})
        if not visible(initial): continue
        person = copy.deepcopy(original)
        snapshots = [item for item in objects(person.get("snapshots")) if visible(item)]
        snapshots.sort(key=lambda item: (item.get("narrative_range", {}).get("start", 0), (item.get("revealed_at") or {}).get("offset") or 0, item.get("id", "")))
        for snapshot in snapshots:
            snapshot["event_ids"] = [eid for eid in snapshot.get("event_ids", []) if eid in event_ids]
            snapshot["events"] = [copy.deepcopy(index[eid]) for eid in snapshot["event_ids"]]
        person["snapshots"] = snapshots
        active = [item for item in snapshots if progress is None or item["narrative_range"].get("end") is None or progress <= item["narrative_range"]["end"]]
        person["current_snapshot"] = copy.deepcopy(active[-1]) if active else None
        people.append(person)
        for snapshot in snapshots:
            for eid in snapshot["event_ids"]: index[eid]["participants"] = list(dict.fromkeys([*index[eid].get("participants", []), person["id"]]))
    settings = []
    for original in objects(result.get("settings")):
        if not visible(original): continue
        setting = copy.deepcopy(original)
        snapshots = [item for item in objects(setting.get("snapshots")) if visible(item)]
        snapshots.sort(key=lambda item: (item.get("narrative_range", {}).get("start", 0), (item.get("revealed_at") or {}).get("offset") or 0, item.get("id", "")))
        for snapshot in snapshots:
            snapshot["event_ids"] = [eid for eid in snapshot.get("event_ids", []) if eid in event_ids]
            for eid in snapshot["event_ids"]: index[eid]["setting_ids"] = list(dict.fromkeys([*index[eid].get("setting_ids", []), setting["id"]]))
        setting["snapshots"] = snapshots
        active = [item for item in snapshots if progress is None or item["narrative_range"].get("end") is None or progress <= item["narrative_range"]["end"]]
        setting["current_snapshot"] = copy.deepcopy(active[-1]) if active else None
        settings.append(setting)
    result["characters"], result["settings"] = people, settings
    character_ids = {person["id"] for person in people}
    setting_ids = {setting["id"] for setting in settings}
    if progress is not None:
        for person in people:
            for fact in [person.get("initial_state", {}), *person.get("snapshots", [])]:
                fact["relationships"] = [item for item in fact.get("relationships", []) if item.get("character_id") in character_ids]
        for event in events:
            event["participants"] = [cid for cid in event.get("participants", []) if cid in character_ids]
            event["setting_ids"] = [sid for sid in event.get("setting_ids", []) if sid in setting_ids]
        for setting in settings:
            if setting.get("earlier_setting_id") not in setting_ids: setting["earlier_setting_id"] = None
    narrative = sorted(events, key=lambda item: (item.get("narrative_progress", 0), (item.get("revealed_at") or {}).get("offset") or 0, item.get("id", "")))
    plot["events"], plot["plotlines"] = events, lines
    plot["views"] = {"narrative": copy.deepcopy(narrative), "story": copy.deepcopy(ordered_story_events(events))}
    grouped = {}
    for event in narrative:
        volume = event.get("volume") or "未分卷"
        if volumes:
            number = _disclosure(event)
            for candidate in volumes:
                start, end = candidate.get("start_chapter", candidate.get("start")), candidate.get("end_chapter", candidate.get("end"))
                if number is not None and isinstance(start, int) and isinstance(end, int) and start <= number <= end:
                    volume = candidate.get("title", "未分卷"); break
        grouped.setdefault(volume, []).append(event)
    plot["outlines"] = {"book": _outline(narrative), "volumes": [{"volume": key, "outline": _outline(items), "event_ids": [item["id"] for item in items]} for key, items in grouped.items()]}
    if progress is not None:
        plot["logline"] = _outline(narrative)
        style = result.get("style", {})
        result["style"] = {"schema_version": 3, "observations": [item for item in objects(style.get("observations")) if visible(item)],
                           "narrative_composition": [item for item in objects(style.get("narrative_composition")) if visible(item)],
                           "catchphrases": [{**item, "occurrences": [hit for hit in objects(item.get("occurrences")) if visible(hit)]} for item in objects(style.get("catchphrases"))]}
        rhythm = result.get("rhythm", {})
        result["rhythm"] = {"schema_version": 3, "chapter_analysis": [item for item in objects(rhythm.get("chapter_analysis")) if visible(item)]}
        opening = result.get("opening", {})
        result["opening"] = {"schema_version": 3, "observations": [item for item in objects(opening.get("observations")) if visible(item)]}
        for field in ("metadata", "raw_entries", "setting_features"): result.pop(field, None)
    era = result.get("era_background", {})
    if isinstance(era, dict):
        era["items"] = [{**item, "appearances": [hit for hit in objects(item.get("appearances")) if visible(hit)]} for item in objects(era.get("items")) if progress is None or any(visible(hit) for hit in objects(item.get("appearances")))]
        if progress is not None:
            for item in era["items"]:
                for hit in item["appearances"]:
                    hit["event_ids"] = [eid for eid in hit.get("event_ids", []) if eid in event_ids]
                    hit["character_ids"] = [cid for cid in hit.get("character_ids", []) if cid in character_ids]
        coverage = result.get("style", {}).get("deterministic_statistics", {}).get("coverage", {}).get("characters", 0)
        era["statistics"] = era_statistics(era, people, character_count=coverage)
    return result


def _project_v4(sections, chapter=None, *, volumes=None, source_versions=None, progress=None, legacy_source_version_id=None):
    from .analysis_contract_v4 import adapt_v3_section, normalize_v4_section
    from .language_analysis import naming_statistics
    from .progress import prepare_progress_sources_v4, narrative_progress_v4
    sources = prepare_progress_sources_v4(source_versions)
    modern = copy.deepcopy(sections)
    # Legacy chapter coordinates are located against the same immutable source as
    # v3, then recalculated to v4's gap-inclusive chapter denominator.
    legacy_sources = {identity: {**source, "_progress_index": source["_progress_index_v4"]} for identity, source in sources.items()}
    legacy_view = _project_v3(sections, None, volumes=volumes, source_versions=legacy_sources, legacy_source_version_id=legacy_source_version_id) if not any(
        isinstance(value, dict) and value.get("schema_version") == 4 or isinstance(value, list) and any(isinstance(item, dict) and item.get("schema_version") == 4 for item in value) for value in sections.values()) else sections
    for section in ("plot", "characters", "settings", "style", "rhythm", "opening", "era_background"):
        if section in modern: modern[section] = normalize_v4_section(section, legacy_view.get(section, modern[section]), source_versions=sources)
    if chapter is not None and progress is None:
        cutoffs = [narrative_progress_v4(source, {"chapter": chapter}) for source in sources.values()]
        cutoffs = [value for value in cutoffs if value is not None]
        if cutoffs: progress = max(cutoffs)
    # Feed the existing visibility/link projection a v3 copy. This never crosses
    # a provider/cache boundary, so historical v3 contracts and files stay intact.
    proxy = copy.deepcopy(modern)
    for section in ("plot", "rhythm", "opening", "era_background"):
        if isinstance(proxy.get(section), dict): proxy[section]["schema_version"] = 3
    for line in proxy.get("plot", {}).get("plotlines", []):
        line["goal"] = line.pop("description", "")
        line["stages"] = [{**{key: value for key, value in node.items() if key != "description"}, "goal": node.get("description", "")} for node in line.pop("time_nodes", [])]
    names = {}
    for person in proxy.get("characters", []):
        person["schema_version"] = 3
        for snapshot in person.get("snapshots", []): names[snapshot["id"]] = snapshot.pop("name", "")
    for setting in proxy.get("settings", []):
        setting["schema_version"] = 3
        setting["snapshots"] = []
    proxy["style"] = {"schema_version": 3, "narrative_person": "", "focalization": "", "register": "", "colloquialism": "",
                       "rhetoric": [], "dialogue_style": "", "catchphrases": [], "naming": {"patterns": [], "style": "", "examples": []},
                       "narrative_composition": [], "observations": [],
                       "deterministic_statistics": copy.deepcopy(modern.get("style", {}).get("deterministic_statistics", {}))}
    result = _project_v3(proxy, chapter, volumes=volumes, source_versions={}, progress=progress, legacy_source_version_id=legacy_source_version_id)
    for section in ("plot", "characters", "settings", "rhythm", "opening", "era_background"):
        if section in result: result[section] = adapt_v3_section(section, result[section])
    for person in result.get("characters", []):
        for snapshot in person.get("snapshots", []): snapshot["name"] = names.get(snapshot["id"], "")
        if person.get("current_snapshot"): person["current_snapshot"]["name"] = names.get(person["current_snapshot"]["id"], "")
    result["plot"].pop("outlines", None)
    result["plot"].pop("epochs", None)
    result["plot"]["logline"] = modern.get("plot", {}).get("logline", "")
    result["style"] = copy.deepcopy(modern.get("style", {}))
    stats = result["style"].setdefault("deterministic_statistics", {})
    stats["naming"] = naming_statistics(result.get("characters", []))
    return result
