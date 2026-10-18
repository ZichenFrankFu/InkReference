"""Pure, conservative text preparation; no network, filesystem or model calls.

Numeral conversion, numbered heading patterns and pattern scoring are adapted
from InkOctoBot reference_pipeline/chapter_parser.py at 51c82b6f01f6a5b5210e1bae9302be257b3b8083.
Copyright (c) 2026 ZichenFrankFu, MIT; see THIRD_PARTY_NOTICES.md.

All offsets are Python Unicode code-point offsets in the indicated text version.
These helpers are a Batch 01 reuse foundation, not an AI extraction pipeline.
"""

from __future__ import annotations

import hashlib
import html
import re
import statistics
from typing import Any

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "百": 100, "千": 1000, "万": 10000,
}
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
_CN_NUMBER = r"[零〇一二两三四五六七八九十百千万0-9０-９]+"
# Use horizontal whitespace only: \s in the original could consume body lines.
_HORIZONTAL = r"[ \t\u3000]"
_PATTERNS = [
    ("第N章/回/节", re.compile(
        rf"^{_HORIZONTAL}*第{_HORIZONTAL}*({_CN_NUMBER}){_HORIZONTAL}*[章回节]"
        rf"{_HORIZONTAL}*(?:[：:.、，,．]{_HORIZONTAL}*)?([^\n]{{0,80}})$", re.MULTILINE)),
    ("Chapter N", re.compile(
        rf"^{_HORIZONTAL}*Chapter{_HORIZONTAL}+([0-9０-９]+)"
        rf"{_HORIZONTAL}*(?:[：:.、，,．]{_HORIZONTAL}*)?([^\n]{{0,80}})$",
        re.MULTILINE | re.IGNORECASE)),
    ("N、/.", re.compile(
        rf"^{_HORIZONTAL}*([0-9０-９]+){_HORIZONTAL}*[、.．]{_HORIZONTAL}*"
        r"(?![0-9０-９])([^\n]{1,80})$", re.MULTILINE)),
]
_RANDOM_ID = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*[0-9])"
    r"[A-Za-z0-9]{12,}(?![A-Za-z0-9])"
)
_HTML_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9a-fA-F]+);")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Diagnostics adapted from InkOctoBot's garble signatures. Suspected story text
# and author notes remain data; no heuristic encoding repair or deletion.
_DIAGNOSTIC_PATTERNS = [
    ("random_id", _RANDOM_ID, "疑似采集 ID；默认保留，确认后可移除"),
    ("replacement_character", re.compile(r"[\ufffd\ufffe\uffff]+"), "字符信息可能已丢失，请重新选择源文件编码"),
    ("mojibake_signature", re.compile(r"锟[斤拷]+"), "疑似编码错误；保留原文并检查原文件"),
    ("html_comment", re.compile(r"<!--.*?-->", re.DOTALL), "疑似采集注释；默认保留"),
    ("html_tag", re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>"), "疑似标记；默认保留"),
    ("author_note", re.compile(r"上架感言|完本感言|请假说明|断更通知|求月票|求订阅"), "疑似作者题外话；默认保留"),
]


def cn2int(value: str) -> int | None:
    """Convert common Chinese/full-width chapter numerals, including 万 units.

    Adapted from InkOctoBot's cn2int; fixes its non-sectional 万 handling.
    """
    value = (value or "").strip().translate(_FULLWIDTH)
    if not value:
        return None
    if value.isdecimal():
        return int(value)
    if not all(ch in _CN_DIGITS for ch in value):
        return None
    if all(_CN_DIGITS[ch] < 10 for ch in value):
        return int("".join(str(_CN_DIGITS[ch]) for ch in value))
    total, section, current = 0, 0, 0
    for ch in value:
        unit = _CN_DIGITS[ch]
        if unit < 10:
            current = unit
        elif unit == 10000:
            total += (section + current or 1) * unit
            section, current = 0, 0
        else:
            section += (current or 1) * unit
            current = 0
    return total + section + current


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def diagnose_text(text: str) -> list[dict[str, Any]]:
    """Return exact spans in the supplied text; flagging never changes it."""
    findings = []
    for code, pattern, message in _DIAGNOSTIC_PATTERNS:
        for match in pattern.finditer(text):
            findings.append({"code": code, "start": match.start(), "end": match.end(),
                             "sample": match.group(0)[:80], "message": message})
    return sorted(findings, key=lambda finding: (finding["start"], finding["code"]))


def clean_text(text: str, *, remove_random_ids: bool = False) -> dict[str, Any]:
    """Create a separate cleaned version, preserving original text and hashes.

    Ordered transformation records describe each stage, not original-file spans.
    No stripping, Unicode normalization, entity deletion or invented characters.
    ID deletion requires an explicit option; symbols and potential dialogue stay.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str; decode source bytes strictly first")
    cleaned = text
    transformations = []

    def apply(stage: str, result: str, count: int) -> None:
        nonlocal cleaned
        if result != cleaned:
            transformations.append({"stage": stage, "count": count,
                                    "input_characters": len(cleaned), "output_characters": len(result),
                                    "input_sha256": _sha256(cleaned), "output_sha256": _sha256(result)})
            cleaned = result

    apply("leading_bom", cleaned[1:] if cleaned.startswith("\ufeff") else cleaned,
          int(cleaned.startswith("\ufeff")))
    apply("html_entities", _HTML_ENTITY.sub(lambda m: html.unescape(m.group(0)), cleaned),
          len(_HTML_ENTITY.findall(cleaned)))
    apply("line_endings", cleaned.replace("\r\n", "\n").replace("\r", "\n"), cleaned.count("\r"))
    cleaned_controls, control_count = _CONTROL.subn("", cleaned)
    apply("control_characters", cleaned_controls, control_count)
    if remove_random_ids:
        cleaned_ids, id_count = _RANDOM_ID.subn("", cleaned)
        apply("confirmed_random_ids", cleaned_ids, id_count)
    return {"original_text": text, "cleaned_text": cleaned,
            "original_sha256": _sha256(text), "cleaned_sha256": _sha256(cleaned),
            "transformations": transformations, "diagnostics": diagnose_text(cleaned),
            "diagnostic_offset_version": "cleaned_text"}


def _score_pattern(matches: list[re.Match[str]], text_len: int) -> float:
    """Adapted count/order/short-line/spacing score, accepting short works."""
    if not matches:
        return 0.0
    nums = [cn2int(match.group(1)) for match in matches]
    nums = [num for num in nums if num is not None]
    ordered = sum(b > a for a, b in zip(nums, nums[1:])) / max(1, len(nums) - 1) if len(nums) > 1 else 1.0
    short = sum(len(match.group(0)) < 50 for match in matches) / len(matches)
    gaps = [b.start() - a.start() for a, b in zip(matches, matches[1:])] or [text_len]
    mean = statistics.mean(gaps)
    deviation = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
    spacing = 1.0 - min(1.0, deviation / (mean + 1))
    return 2.5 * min(1.0, len(matches) / 500) + 2.0 * ordered + 1.5 * short + spacing


def chunk_text(text: str, max_chars: int = 3000) -> list[dict[str, Any]]:
    """Lossless character-bound chunks; no claim of tokenizer/model support."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    return [{"start": start, "end": min(start + max_chars, len(text)),
             "text": text[start:start + max_chars]} for start in range(0, len(text), max_chars)]


def _coverage(text: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    cursor = 0
    contiguous = True
    for segment in segments:
        contiguous = contiguous and segment["start"] == cursor and segment["text"] == text[segment["start"]:segment["end"]]
        cursor = segment["end"]
    reconstructed = "".join(segment["text"] for segment in segments)
    complete = contiguous and cursor == len(text) and reconstructed == text
    return {"offset_version": "cleaned_text", "characters": len(text),
            "covered_characters": sum(segment["end"] - segment["start"] for segment in segments),
            "contiguous": contiguous, "complete": complete,
            "reconstructed_sha256": _sha256(reconstructed)}


def split_chapters(text: str, *, fallback_chars: int = 3000) -> dict[str, Any]:
    """Find numbered headings and retain *all* text, including prefix and tail.

    ``text`` includes each full span; ``content`` starts after the heading but
    retains its line ending. Empty text has no chapters and complete coverage.
    Call clean_text first for normalized newlines and separate version tracking.
    """
    chunk_text("", fallback_chars)  # Validate even when headings are available.
    candidates = []
    best_name = None
    best_score = 0.0
    best_matches: list[re.Match[str]] = []
    for name, pattern in _PATTERNS:
        matches = list(pattern.finditer(text))
        score = _score_pattern(matches, len(text))
        # List-style headings are ambiguous; require repetition for auto-pick.
        eligible = bool(matches) and (name != "N、/." or len(matches) >= 3)
        candidates.append({"name": name, "count": len(matches), "score": round(score, 4), "eligible": eligible})
        if eligible and score > best_score:
            best_name, best_score, best_matches = name, score, matches
    chapters = []
    if best_matches:
        if best_matches[0].start() > 0:
            end = best_matches[0].start()
            chapters.append({"number": None, "title": "前置文本", "raw_marker": "", "kind": "preface",
                             "start": 0, "end": end, "body_start": 0, "text": text[:end], "content": text[:end]})
        for index, match in enumerate(best_matches):
            start = match.start()
            end = best_matches[index + 1].start() if index + 1 < len(best_matches) else len(text)
            chapters.append({"number": cn2int(match.group(1)), "title": match.group(2).strip(),
                             "raw_marker": match.group(0), "kind": "chapter", "start": start,
                             "end": end, "body_start": match.end(), "text": text[start:end],
                             "content": text[match.end():end]})
    else:
        for index, chunk in enumerate(chunk_text(text, fallback_chars)):
            chapters.append({**chunk, "number": index + 1, "title": f"段落 {index + 1}",
                             "raw_marker": "", "kind": "fallback", "body_start": chunk["start"],
                             "content": chunk["text"]})
    return {"chapters": chapters, "pattern": best_name, "fallback_used": not bool(best_matches),
            "candidates": candidates, "coverage": _coverage(text, chapters)}


def analyze_text(text: str, *, remove_random_ids: bool = False, fallback_chars: int = 3000) -> dict[str, Any]:
    """Prepare and segment a supplied text while preserving both versions."""
    result = clean_text(text, remove_random_ids=remove_random_ids)
    result["chapter_analysis"] = split_chapters(result["cleaned_text"], fallback_chars=fallback_chars)
    return result
