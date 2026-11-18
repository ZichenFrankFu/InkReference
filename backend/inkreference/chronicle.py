"""Whole-book chronicle validation and reader-facing exports."""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .database import DataError

EVENT_TYPES = ("推进", "日常", "压抑", "高潮", "过渡")
SETTING_CATEGORIES = ("世界观", "地点", "超能力", "器物", "组织")
FORESHADOWING_STATES = ("已收回", "部分收回", "未收回")
SECTIONS = ("events", "characters", "relationships", "settings", "storylines", "foreshadowing", "questions")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_LINE_BREAK = re.compile(r"\r\n|[\n\r]")

SourceLines = Sequence[str] | Mapping[int, str]


def index_source(text: str) -> dict[int, str]:
    """Keep the physical, one-based line numbers, including intervening blank lines."""
    lines = _LINE_BREAK.split(text)
    if lines[-1] == "":
        lines.pop()
    return dict(enumerate(lines, 1))


def empty_chronicle() -> dict[str, list]:
    return {section: [] for section in SECTIONS}


def _invalid(path: str, detail: str) -> None:
    raise DataError(422, f"编年史 {path} {detail}")


def _object(value: Any, required: str, path: str, optional: str = "") -> dict:
    keys = set(required.split())
    if not isinstance(value, dict) or not keys <= value.keys() or value.keys() - keys - set(optional.split()):
        _invalid(path, f"字段应包含 {required}" + (f"，可选 {optional}" if optional else ""))
    return value


def _list(value: Any, path: str, *, minimum: int = 0, maximum: int = 10000) -> list:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _invalid(path, f"需为 {minimum}–{maximum} 项的列表")
    return value


def _text(value: Any, path: str, *, empty: bool = False, limit: int = 50000) -> None:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        _invalid(path, f"需为{'可空' if empty else '非空'}文本，最多 {limit} 字")


def _texts(row: dict, path: str, required: str, optional: str = "") -> None:
    for field in required.split():
        _text(row[field], f"{path}.{field}")
    for field in optional.split():
        _text(row[field], f"{path}.{field}", empty=True)


def _choice(value: Any, options: Sequence[str], path: str) -> None:
    if value not in options:
        _invalid(path, f"只接受 {'、'.join(options)}")


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _invalid(path, "编号无效")
    return value


def _assigned_id(value: Any, seen: set[str], path: str) -> None:
    identifier = _id(value, path)
    if identifier in seen:
        _invalid(path, f"编号 {identifier} 重复")
    seen.add(identifier)


def _link(value: Any, ids: set[str], path: str) -> None:
    if _id(value, path) not in ids:
        _invalid(path, f"找不到编号 {value}")


def _links(value: Any, ids: set[str], path: str, *, minimum: int = 0) -> None:
    links = _list(value, path, minimum=minimum)
    seen: set[str] = set()
    for identifier in links:
        _link(identifier, ids, path)
        _assigned_id(identifier, seen, path)


def _source_line(value: Any, path: str, source: SourceLines | None, *, evidence: bool = True) -> int:
    if type(value) is not int or value < 1:
        _invalid(path, "原文行号需为正整数")
    if source is None:
        return value
    if isinstance(source, Mapping):
        line = source.get(value)
    else:
        line = source[value - 1] if value <= len(source) else None
    if not isinstance(line, str) or (evidence and not line.strip()):
        _invalid(path, f"找不到可用的原文 L{value}")
    return value


def _references(value: Any, path: str, source: SourceLines | None) -> list[int]:
    refs = _list(value, path, minimum=1, maximum=64)
    for line in refs:
        _source_line(line, path, source)
    if len(set(refs)) != len(refs):
        _invalid(path, "原文行号重复")
    return refs


def _endpoint(value: Any, path: str, source: SourceLines | None, event_ids: set[str]) -> int:
    row = _object(value, "basis source_lines event_id", path)
    _text(row["basis"], f"{path}.basis")
    if row["event_id"] is not None:
        _link(row["event_id"], event_ids, f"{path}.event_id")
    return min(_references(row["source_lines"], f"{path}.source_lines", source))


def validate_chronicle(data: Any, source_lines: SourceLines | None = None) -> dict[str, Any]:
    """Reject invalid IDs and anchors before the analysis reaches storage or the UI."""
    doc = _object(data, " ".join(SECTIONS), "结果")
    for section in SECTIONS:
        _list(doc[section], section)

    ids: set[str] = set()
    character_ids: set[str] = set()
    regular_storyline_ids: set[str] = set()
    clue_ids: set[str] = set()
    event_ids: set[str] = set()
    for index, row in enumerate(doc["characters"]):
        path = f"characters[{index}]"
        row = _object(row, "id name aliases identity basis source_lines", path)
        _assigned_id(row["id"], ids, path)
        character_ids.add(row["id"])
        _texts(row, path, "name identity basis")
        aliases = _list(row["aliases"], f"{path}.aliases", maximum=1000)
        for alias in aliases:
            _text(alias, f"{path}.aliases", limit=256)
        _references(row["source_lines"], f"{path}.source_lines", source_lines)

    for index, row in enumerate(doc["storylines"]):
        path = f"storylines[{index}]"
        row = _object(row, "id title character_ids beginning development turn conclusion", path)
        _assigned_id(row["id"], ids, path)
        regular_storyline_ids.add(row["id"])
        _links(row["character_ids"], character_ids, f"{path}.character_ids")
        _texts(row, path, "title", "beginning development turn conclusion")

    for index, row in enumerate(doc["foreshadowing"]):
        path = f"foreshadowing[{index}]"
        row = _object(row, "id title status plant return storyline_id", path)
        _assigned_id(row["id"], ids, path)
        clue_ids.add(row["id"])
        _text(row["title"], f"{path}.title")
        _choice(row["status"], FORESHADOWING_STATES, f"{path}.status")
        if row["storyline_id"] is not None:
            _link(row["storyline_id"], regular_storyline_ids, f"{path}.storyline_id")

    available_storyline_ids = regular_storyline_ids | clue_ids
    last_line = 0
    for index, row in enumerate(doc["events"]):
        path = f"events[{index}]"
        row = _object(row, "id title type story_time location description storyline_ids basis source_lines", path)
        _assigned_id(row["id"], ids, path)
        event_ids.add(row["id"])
        _texts(row, path, "title description basis", "story_time location")
        _choice(row["type"], EVENT_TYPES, f"{path}.type")
        _links(row["storyline_ids"], available_storyline_ids, f"{path}.storyline_ids", minimum=1)
        if row["storyline_ids"][0] not in regular_storyline_ids:
            _invalid(path, "首个所属故事线应为主要故事线")
        first_line = _references(row["source_lines"], f"{path}.source_lines", source_lines)[0]
        if first_line < last_line:
            _invalid(path, "各事件的首条定位依据需按叙述顺序排列")
        last_line = first_line

    for index, row in enumerate(doc["foreshadowing"]):
        path = f"foreshadowing[{index}]"
        first = _endpoint(row["plant"], f"{path}.plant", source_lines, event_ids)
        recalled = row["return"]
        if row["status"] == "未收回":
            if recalled is not None:
                _invalid(path, "未收回的伏笔应留空收回")
        elif _endpoint(recalled, f"{path}.return", source_lines, event_ids) < first:
            _invalid(path, "收回位置早于埋设")

    last_line = 0
    for index, row in enumerate(doc["relationships"]):
        path = f"relationships[{index}]"
        row = _object(row, "character_a_id character_b_id narrative_order relation basis source_lines", path, "end_order")
        _links([row["character_a_id"], row["character_b_id"]], character_ids, path)
        _texts(row, path, "relation basis")
        start = _source_line(row["narrative_order"], f"{path}.narrative_order", source_lines)
        refs = _references(row["source_lines"], f"{path}.source_lines", source_lines)
        if start not in refs or start < last_line:
            _invalid(path, "关系成立位置应有原文依据，并按叙述顺序排列")
        end = row.get("end_order")
        if end is not None and _source_line(end, f"{path}.end_order", source_lines, evidence=False) < start:
            _invalid(path, "结束位置早于成立位置")
        last_line = start

    for index, row in enumerate(doc["settings"]):
        path = f"settings[{index}]"
        row = _object(row, "id name category description source_lines", path)
        _assigned_id(row["id"], ids, path)
        _texts(row, path, "name description")
        _choice(row["category"], SETTING_CATEGORIES, f"{path}.category")
        _references(row["source_lines"], f"{path}.source_lines", source_lines)

    for index, row in enumerate(doc["questions"]):
        path = f"questions[{index}]"
        row = _object(row, "id question options source_lines", path)
        _assigned_id(row["id"], ids, path)
        _text(row["question"], f"{path}.question")
        a, b = _list(row["options"], f"{path}.options", minimum=2, maximum=2)
        for option, letter in ((a, "A"), (b, "B")):
            _object(option, "id label effect", f"{path}.{letter}")
            _choice(option["id"], (letter,), f"{path}.{letter}.id")
            _texts(option, f"{path}.{letter}", "label effect")
        if a["label"].strip() == b["label"].strip() or a["effect"].strip() == b["effect"].strip():
            _invalid(path, "校准选项应有不同做法和结果")
        _references(row["source_lines"], f"{path}.source_lines", source_lines)

    result = copy.deepcopy(doc)
    events = {row["id"]: row for row in result["events"]}
    for clue in result["foreshadowing"]:
        for endpoint in (clue["plant"], clue["return"]):
            if endpoint and endpoint["event_id"] is not None:
                links = events[endpoint["event_id"]]["storyline_ids"]
                if clue["id"] not in links:
                    links.append(clue["id"])

    try:
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except UnicodeError:
        _invalid("结果", "需为有效的 UTF-8 文本")
    if size > 8_000_000:
        raise DataError(413, "编年史超过 8 MB")
    return result


def _basis(text: str, lines: list[int]) -> str:
    return f"{text}　{'、'.join(f'L{line}' for line in lines)}"


def _labels(rows: list[dict], field: str) -> dict[str, str]:
    return {row["id"]: row[field] for row in rows}


def chronicle_outline(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Export the eight columns of the established narrative-order outline."""
    titles = _labels(data["storylines"] + data["foreshadowing"], "title")
    roles: dict[tuple[str, str], list[str]] = {}
    for clue in data["foreshadowing"]:
        for field, label in (("plant", "埋设"), ("return", "收回")):
            endpoint = clue[field]
            if endpoint and endpoint["event_id"] is not None:
                roles.setdefault((endpoint["event_id"], clue["id"]), []).append(label)

    def storylines(event: dict) -> list[str]:
        labels = []
        for identifier in event["storyline_ids"]:
            title = f"{identifier} {titles[identifier]}"
            event_roles = roles.get((event["id"], identifier))
            if event_roles:
                labels.extend(f"{title}（{role}）" for role in event_roles)
            else:
                labels.append(title)
        return labels

    return [
        {
            "叙述序号": f"E{index:03d}",
            "标题": event["title"],
            "类型": event["type"],
            "故事中时间": event["story_time"],
            "地点": event["location"],
            "事件描述": event["description"],
            "所属故事线": storylines(event),
            "定位依据": _basis(event["basis"], event["source_lines"]),
        }
        for index, event in enumerate(data["events"], 1)
    ]
