"""Exact source-position to narrative-progress mapping and conservative story ordering."""
from __future__ import annotations

import math
from bisect import bisect_right
import re
from typing import Any


class ProgressIndex:
    """Per-request index: O(log chapters), or at most 1024 body characters per point."""
    def __init__(self, version: dict, *, contract_version: int = 3):
        self.text = str(version.get("text", ""))
        chapters = [item for item in version.get("chapters", []) if isinstance(item, dict)
                    and not item.get("synthetic") and not item.get("is_preamble")
                    and item.get("kind") not in {"preface", "front_matter"}]
        if chapters and not any(item.get("raw_marker") or item.get("heading") or item.get("number") is not None for item in chapters): chapters = []
        if chapters and all(item.get("kind") == "fallback" for item in chapters): chapters = []
        if len(chapters) == 1:
            item = chapters[0]
            if item.get("start") == item.get("body_start") and not item.get("raw_marker") and not re.match(r"\s*第[^\n]{0,40}[章回节话]", self.text[:100]): chapters = []
        self.chapters = chapters
        self.denominator = len(chapters)
        context = narrative_chapter_context(chapters) if contract_version >= 4 else {}
        self.denominator = context.get("chapter_count", self.denominator)
        global_numbers = context.get("chapter_numbers")
        use_numbers = bool(global_numbers) and global_numbers == list(range(1, self.denominator + 1))
        self.number_ordinals = {}
        self.locations = []
        for index, item in enumerate(chapters, 1):
            position = item.get("number") if use_numbers and type(item.get("number")) is int else index
            self.number_ordinals.setdefault(item.get("number"), position)
            if type(item.get("start")) is int and type(item.get("end")) is int:
                self.locations.append((item["start"], item["end"], position))
        self.locations.sort()
        self.starts = [item[0] for item in self.locations]
        self.prefix = [0]
        if not chapters:
            for offset in range(0, len(self.text), 1024):
                self.prefix.append(self.prefix[-1] + sum(not char.isspace() for char in self.text[offset:offset+1024]))
        self.body_total = self.prefix[-1]

    def at(self, anchor: dict | None) -> float | None:
        anchor = anchor or {}
        offset, number = anchor.get("offset"), anchor.get("chapter")
        if self.chapters:
            ordinal = None
            if type(offset) is int:
                cursor = bisect_right(self.starts, offset) - 1
                if cursor >= 0 and self.locations[cursor][0] <= offset < self.locations[cursor][1]: ordinal = self.locations[cursor][2]
            if ordinal is None and type(number) is int: ordinal = self.number_ordinals.get(number)
            return round(ordinal * 100 / self.denominator, 6) if ordinal is not None else None
        if type(offset) is int and self.text:
            offset = max(0, min(offset, len(self.text)))
            block = offset // 1024
            before = self.prefix[block] + sum(not char.isspace() for char in self.text[block*1024:offset])
            return round(before * 100 / self.body_total, 6) if self.body_total else 0.0
        return None


def prepare_progress_sources(source_versions: dict | None) -> dict:
    """Index immutable payload copies without mutating or retaining user source."""
    return {identity: source if isinstance(source.get("_progress_index"), ProgressIndex)
            else {**source, "_progress_index": ProgressIndex(source)}
            for identity, source in (source_versions or {}).items() if isinstance(source, dict)}


def narrative_progress(version: dict, anchor: dict | None) -> float | None:
    """Chapter ordinal / total, or body-character offset / total without chapters."""
    index = version.get("_progress_index")
    return (index if isinstance(index, ProgressIndex) else ProgressIndex(version)).at(anchor)


def finite_progress(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def story_time_key(value: Any) -> tuple | None:
    """Sort explicit calendar-like dates without inventing dates for relative text."""
    if isinstance(value, dict):
        value = value.get("sort_key") or value.get("label")
    if not isinstance(value, str) or not value.strip():
        return None
    match = re.search(r"(?<!\d)(\d{3,6})\s*(?:年|[-/])(?:\s*(\d{1,2})\s*(?:月|[-/]))?(?:\s*(\d{1,2})\s*日?)?", value)
    if match:
        year, month, day = (int(item or 0) for item in match.groups())
        phase = 0
        if re.search(r"月底|月末|下旬", value): phase = 28
        elif "中旬" in value: phase = 15
        elif "上旬" in value or "月初" in value: phase = 1
        clock = 1 if "上午" in value else 2 if "下午" in value else 0
        return year, month, day or phase, clock
    match = re.fullmatch(r"\s*(\d{3,6})\s*年?\s*", value)
    return (int(match[1]), 0, 0, 0) if match else None


def ordered_story_events(events: list[dict]) -> list[dict]:
    """Known calendar events reorder; unknowns keep their narrative slots.

    Unknown or purely relative dates use narrative order rather than being
    pushed into an artificial 'undated' era after the end of the story.
    """
    narrative = sorted(events, key=lambda item: (item.get("narrative_progress", 0), (item.get("revealed_at") or {}).get("offset") or 0, item.get("id", "")))
    known = sorted((item for item in narrative if story_time_key(item.get("story_time")) is not None),
                   key=lambda item: (story_time_key(item.get("story_time")), item.get("narrative_progress", 0), item.get("id", "")))
    iterator = iter(known)
    return [next(iterator) if story_time_key(item.get("story_time")) is not None else item for item in narrative]


def narrative_chapter_context(chapters: list[dict]) -> dict:
    """Chapter selector coordinates without reading bodies or inventing huge gaps.

    Globally increasing chapter numbers include missing placeholders in the total.
    Repeated/reset/custom numbering uses source order instead. Fallback whole-book
    and prefatory records are not chapters. The bounded list prevents allocations
    controlled by an imported chapter number.
    """
    entries = [item for item in chapters if isinstance(item, dict) and not item.get("synthetic")
               and not item.get("is_preamble") and item.get("kind") not in {"preface", "front_matter", "fallback"}]
    entries = [item for item in entries if item.get("number") is not None or item.get("raw_marker") or item.get("heading")]
    numbers = [item.get("number") for item in entries]
    if not numbers: return {"chapter_count": 0}
    if all(type(number) is int and number > 0 for number in numbers) and all(a < b for a, b in zip(numbers, numbers[1:])) and numbers[-1] <= 100000:
        return {"chapter_count": numbers[-1], "chapter_numbers": list(range(1, numbers[-1] + 1))}
    return {"chapter_count": len(entries)}

def prepare_progress_sources_v4(source_versions: dict | None) -> dict:
    return {identity: source if isinstance(source.get("_progress_index_v4"), ProgressIndex)
            else {**source, "_progress_index_v4": ProgressIndex(source, contract_version=4)}
            for identity, source in (source_versions or {}).items() if isinstance(source, dict)}

def narrative_progress_v4(version: dict, anchor: dict | None) -> float | None:
    index = version.get("_progress_index_v4")
    return (index if isinstance(index, ProgressIndex) else ProgressIndex(version, contract_version=4)).at(anchor)
