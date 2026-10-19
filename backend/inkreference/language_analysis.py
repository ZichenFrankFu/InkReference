"""Deterministic, writer-facing language measures; no model or word tokenizer."""
from __future__ import annotations

from collections import Counter
import re
import io
import math
from typing import Iterable

PUNCTUATION = {"comma": "，,", "full_stop": "。.", "question": "？?", "exclamation": "！!",
               "colon": "：:", "semicolon": "；;", "dash": "—", "ellipsis": "…", "quotes": '“”「」『』\"'}


def _distribution(values: Iterable[int]) -> dict:
    counts = Counter(values)
    total = sum(counts.values())
    def rank(target):
        accumulated = 0
        for value, count in sorted(counts.items()):
            accumulated += count
            if accumulated >= target: return value
        return 0
    median = (rank((total + 1) // 2) + rank(total // 2 + 1)) / 2 if total else 0
    buckets = {"1–10": 0, "11–20": 0, "21–40": 0, "41–80": 0, "81+": 0}
    for value, count in counts.items():
        bucket = "1–10" if value <= 10 else "11–20" if value <= 20 else "21–40" if value <= 40 else "41–80" if value <= 80 else "81+"
        buckets[bucket] += count
    return {"count": total, "mean": round(sum(value * count for value, count in counts.items()) / total, 2) if total else 0,
            "median": median, "p90": rank(math.ceil(total * .9)), "min": min(counts, default=0),
            "max": max(counts, default=0), "distribution": buckets}


def compute_language_statistics(text: str, *, source_version_id: str = "", chapter_count: int | None = None) -> dict:
    """Counts exclude whitespace; sentence/paragraph policies are explicit.

    Paragraphs are each nonempty physical line, matching the application's
    normalized Chinese prose. Sentence delimiters are 。！？!? plus ellipsis runs;
    punctuation remains part of sentence length. Trailing fragments count once.
    """
    paragraph_lengths = Counter()
    chars = 0
    for line in io.StringIO(text):
        size = len(re.sub(r"\s+", "", line))
        if size:
            paragraph_lengths[size] += 1
            chars += size
    lengths = (len(re.sub(r"\s+", "", match[0])) for match in re.finditer(r"[^。！？!?…]+(?:[。！？!?]+|…+|$)|…+", text))
    punctuation = {name: {"count": sum(text.count(char) for char in symbols),
                          "per_1000_chars": round(sum(text.count(char) for char in symbols) * 1000 / chars, 3) if chars else 0}
                   for name, symbols in PUNCTUATION.items()}
    return {"method": "unicode_non_whitespace_v1", "source_version_id": source_version_id,
            "coverage": {"characters": chars, "chapters": chapter_count},
            "sentence_length": _distribution(value for value in lengths if value), "paragraph_length": _distribution(paragraph_lengths.elements()),
            "punctuation_frequency": punctuation}


def era_statistics(era: dict, characters: list[dict], *, character_count: int = 0) -> dict:
    """Frequencies derive from exact, deduplicated occurrence anchors.

    Character usage means meme dialogue/thought only; real-world event
    references stay in story occurrence counts. Average includes all identified
    characters, including zero users. A narration mention is not character use.
    """
    all_ids = {person["id"] for person in characters if person.get("id")}
    names = {person["id"]: person.get("name", person["id"]) for person in characters if person.get("id")}
    counts: Counter = Counter()
    seen, items, total = set(), [], 0
    for item in era.get("items", []):
        count = 0
        for occurrence in item.get("appearances", item.get("occurrences", [])):
            anchor = occurrence.get("revealed_at", {})
            key = (item.get("id"), occurrence.get("source_version_id"), anchor.get("chapter"), anchor.get("offset"), occurrence.get("evidence"))
            if not occurrence.get("source_version_id") and not occurrence.get("evidence"):
                key = (*key, occurrence.get("narrative_progress"), occurrence.get("story_time"), occurrence.get("context"), occurrence.get("usage"), tuple(occurrence.get("character_ids", [])))
            if key in seen: continue
            seen.add(key)
            count += 1
            cids = occurrence.get("character_ids", [occurrence.get("character_id")])
            if item.get("kind") == "meme" and occurrence.get("usage") in {"dialogue", "thought"}:
                for cid in set(cids):
                    if cid in all_ids: counts[cid] += 1
        total += count
        items.append({"id": item.get("id"), "count": count,
                      "per_10000_chars": round(count * 10000 / character_count, 4) if character_count else None})
    highest = max(counts.values(), default=0)
    return {"occurrence_count": total, "per_10000_chars": round(total * 10000 / character_count, 4) if character_count else None,
            "items": items, "character_usage": [{"character_id": key, "name": names[key], "count": counts[key],
            "per_10000_chars": round(counts[key] * 10000 / character_count, 4) if character_count else None} for key in sorted(all_ids)],
            "highest_characters": [{"character_id": key, "name": names[key], "count": counts[key]} for key in sorted(counts) if counts[key] == highest],
            "average_per_character": round(sum(counts.values()) / len(all_ids), 4) if all_ids else 0,
            "denominator_characters": len(all_ids), "coverage_characters": character_count}
