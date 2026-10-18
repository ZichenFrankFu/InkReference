"""Pure chapter presentation, body layout and virtual missing-chapter helpers.

Presentation items never change immutable chapter spans or workflow indices.
Only an explicit save/materialization may put a missing chapter into the text.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_HORIZONTAL = r"[ \t\u3000]"
_NUMBER = r"[零〇一二两三四五六七八九十百千万0-9０-９]+"
_VOLUME = re.compile(
    rf"^{_HORIZONTAL}*(?:第{_HORIZONTAL}*{_NUMBER}{_HORIZONTAL}*卷|"
    rf"卷{_HORIZONTAL}*{_NUMBER}|序卷|终卷|終卷)"
    rf"(?:{_HORIZONTAL}*[^\r\n。！？；]{{0,80}})?{_HORIZONTAL}*\r?$", re.MULTILINE)
# Atomic CRLF tokens matter: otherwise a lone CRLF could backtrack into two
# line breaks and be shortened to CR by a repeated-newline expression.
_NEWLINE = r"(?>\r\n|\r|\n)"
_REPEATED_NEWLINE = re.compile(rf"({_NEWLINE})(?:{_HORIZONTAL}*{_NEWLINE})+")
_LEADING_EMPTY_LINE = re.compile(rf"^{_HORIZONTAL}*{_NEWLINE}")
_TRAILING_EMPTY_LINE = re.compile(rf"{_NEWLINE}{_HORIZONTAL}*$")


def chapter_label(chapter: dict[str, Any]) -> str:
    """Use one normalized chapter number and a title without source newlines."""
    title = str(chapter.get("title") or "").strip()
    number = chapter.get("number")
    if chapter.get("gap_range"):
        return f"第{chapter['number_start']}章—第{chapter['number_end']}章"
    if chapter.get("kind") in {"chapter", "missing"} and isinstance(number, int) and not isinstance(number, bool):
        prefix = f"第{number}章"
        # Imported/future records occasionally store the full heading as title.
        # Remove the prefix only if it actually names this chapter number.
        marker = re.match(rf"^第{_HORIZONTAL}*({_NUMBER}){_HORIZONTAL}*[章回节]{_HORIZONTAL}*", title)
        if marker:
            from .text_processing import cn2int
            if cn2int(marker.group(1)) == number:
                title = title[marker.end():].lstrip("：:.、，,． \t\u3000")
        return f"{prefix} {title}" if title else prefix
    return title or ("前置文本" if chapter.get("kind") == "preface" else "正文")


def normalize_chapter_body(body: str, *, trim_boundaries: bool = False) -> str:
    """Collapse empty body lines without altering paragraph indentation/Unicode.

    The first newline's exact CRLF, CR or LF style survives each collapsed run.
    Boundary trimming is useful for a body editor/export; ordinary normalization
    preserves the heading/body separator and final separator of source spans.
    """
    if not isinstance(body, str):
        raise TypeError("chapter body must be str")
    result = _REPEATED_NEWLINE.sub(lambda match: match.group(1), body)
    if trim_boundaries:
        # The complete empty line is removed, but first-paragraph indentation
        # and trailing spaces on actual paragraphs remain untouched.
        result = _LEADING_EMPTY_LINE.sub("", result, count=1)
        result = _TRAILING_EMPTY_LINE.sub("", result, count=1)
    return result


def normalize_document_chapter_bodies(text: str, chapters: list[dict[str, Any]] | None = None) -> str:
    """Normalize a derived document's body blank lines, preserving headings.

    Original source bytes belong to the immutable source store. This helper
    changes only body spans of recognized chapters/fallback parts. Prefaces,
    heading bytes, titles, unknown text between spans and paragraph indentation
    remain exact. Pass the pinned parse to avoid accidentally choosing different
    heading formats; a missing parse uses ordinary automatic recognition.
    """
    if not isinstance(text, str):
        raise TypeError("document text must be str")
    if chapters is None:
        from .text_processing import split_chapters
        chapters = split_chapters(text)["chapters"]
    pieces = []
    cursor = 0
    for chapter in chapters:
        start, end = chapter["start"], chapter["end"]
        body_start = chapter.get("body_start", start)
        if not (isinstance(start, int) and isinstance(end, int) and isinstance(body_start, int)
                and cursor <= start <= body_start <= end <= len(text)):
            raise ValueError("chapter spans must be ordered, non-overlapping document offsets")
        pieces.append(text[cursor:body_start])
        body = text[body_start:end]
        pieces.append(normalize_chapter_body(body) if chapter.get("kind") in {"chapter", "fallback"} else body)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def chapter_body_span(chapter: dict[str, Any], *, trim_boundaries: bool = True) -> tuple[int, int]:
    """Return chapter-local source offsets for body-only reading/editing."""
    text = chapter.get("text", "")
    start = max(0, int(chapter.get("body_start", chapter.get("start", 0))) - int(chapter.get("start", 0)))
    end = len(text)
    if trim_boundaries:
        # Skip complete empty boundary lines; do not consume story indentation.
        while start < end:
            match = _LEADING_EMPTY_LINE.match(text, start, end)
            # ^ anchors match only at 0, so match a slice at a non-zero offset.
            if match is None:
                match = _LEADING_EMPTY_LINE.match(text[start:end])
                if match is None:
                    break
                start += match.end()
            else:
                start = match.end()
        while start < end:
            match = _TRAILING_EMPTY_LINE.search(text, start, end)
            if match is None:
                break
            end = match.start()
    return min(start, end), end


def chapter_sequence(chapters: list[dict[str, Any]], *, missing_preview_limit: int = 2000) -> dict[str, Any]:
    """Identify numbering gaps/duplicates within each volume or number restart.

    A decrease starts a new sequence; equal numbers remain duplicates. Explicit
    standalone volume headings also start a sequence even if numbering remains
    ascending. Mixed Chapter N / 第N章 formats share the same numeric sequence.
    No leading missing chapters are inferred from a deliberately partial book.
    """
    if isinstance(missing_preview_limit, bool) or not isinstance(missing_preview_limit, int) or missing_preview_limit < 0:
        raise ValueError("missing_preview_limit must be a nonnegative integer")
    gaps: list[dict[str, Any]] = []
    segments: dict[str, int] = {}
    numbered: list[tuple[str, int, int]] = []
    previous: dict[str, Any] | None = None
    sequence = 0
    volume_pending = False
    for index, chapter in enumerate(chapters):
        identity = str(chapter.get("id", f"chapter-{index + 1}"))
        number = chapter.get("number")
        eligible = chapter.get("kind") == "chapter" and isinstance(number, int) and not isinstance(number, bool)
        if eligible:
            if previous is not None and (volume_pending or number < previous["number"]):
                sequence += 1
                previous = None
            segments[identity] = sequence
            numbered.append((identity, sequence, number))
            if previous is not None and number > previous["number"] + 1:
                missing_count = number - previous["number"] - 1
                limit = min(number, previous["number"] + 1 + missing_preview_limit)
                gaps.append({"before_chapter_id": identity,
                             "after_chapter_id": previous["id"],
                             "after_number": previous["number"], "before_number": number,
                             "expected_next": previous["number"] + 1,
                             "missing_numbers": list(range(previous["number"] + 1, limit)),
                             "missing_count": missing_count,
                             "missing_numbers_truncated": missing_count > missing_preview_limit,
                             "pattern": chapter.get("pattern"), "sequence": sequence})
            previous = {"id": identity, "number": number}
            volume_pending = False
        # A volume heading lies in the previous source span when the chapter
        # parser is configured for chapter headings. Ignore its own raw heading.
        text = str(chapter.get("text") or chapter.get("content") or "")
        body_start = max(0, int(chapter.get("body_start", chapter.get("start", 0))) - int(chapter.get("start", 0)))
        # This is a boolean structural check, not an offset-bearing match.
        # MULTILINE otherwise misses a volume following a lone CR separator.
        volume_lines = text[body_start:].replace("\r\n", "\n").replace("\r", "\n")
        if _VOLUME.search(volume_lines):
            volume_pending = True
    counts = Counter((segment, number) for _, segment, number in numbered)
    duplicates = {identity: counts[(segment, number)] for identity, segment, number in numbered
                  if counts[(segment, number)] > 1}
    return {"gaps": gaps, "duplicates": duplicates, "segments": segments}


def expand_missing_chapters(chapters: list[dict[str, Any]], *, max_placeholders: int = 2000,
                            gaps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a bounded directory with individually editable virtual gap items.

    Extremely large/hostile numbers produce an explicit remaining-range item,
    never a giant allocation or a silently omitted chapter range. Real chapters
    retain their identifiers, source indices and exact original spans.
    """
    if isinstance(max_placeholders, bool) or not isinstance(max_placeholders, int) or max_placeholders < 0:
        raise ValueError("max_placeholders must be a nonnegative integer")
    if gaps is None:
        gaps = chapter_sequence(chapters, missing_preview_limit=max_placeholders)["gaps"]
    gap_before = {str(gap["before_chapter_id"]): gap for gap in gaps}
    result = []
    inserted = 0
    truncated_missing_count = 0
    for index, chapter in enumerate(chapters):
        identity = str(chapter.get("id", f"chapter-{index + 1}"))
        gap = gap_before.get(identity)
        if gap:
            count = min(gap["missing_count"], max(0, max_placeholders - inserted))
            first_number = gap["after_number"] + 1
            offset = chapter["start"]
            common = {"kind": "missing", "placeholder": True, "title": "", "pattern": chapter.get("pattern"),
                      "start": offset, "end": offset, "body_start": offset, "raw_marker": "",
                      "text": "", "content": "", "char_count": 0, "proposal_count": 0,
                      "proposal_ids": [], "enabled_proposal_ids": [], "before_chapter_id": identity,
                      "index": chapter.get("index", index),
                      "issues": [{"kind": "gap", "label": "待补充", "count": 1, "severity": "warning"}]}
            for number in range(first_number, first_number + count):
                item = {**common, "id": f"missing-before-{identity}-{number}", "number": number}
                item["display_label"] = chapter_label(item)
                result.append(item)
            inserted += count
            remainder = gap["missing_count"] - count
            if remainder:
                truncated_missing_count += remainder
                item = {**common, "id": f"missing-range-before-{identity}-{first_number + count}-{gap['before_number'] - 1}",
                        "number": None, "gap_range": True, "number_start": first_number + count,
                        "number_end": gap["before_number"] - 1, "missing_count": remainder,
                        "issues": [{"kind": "gap", "label": f"缺 {remainder} 章", "count": remainder, "severity": "warning"}]}
                item["display_label"] = chapter_label(item)
                result.append(item)
        result.append({**chapter, "display_label": chapter_label(chapter)})
    for display_index, chapter in enumerate(result):
        chapter["display_index"] = display_index
    return {"chapters": result, "gaps": gaps, "placeholder_count": inserted,
            "truncated_missing_count": truncated_missing_count}
