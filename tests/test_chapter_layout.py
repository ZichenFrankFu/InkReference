"""Synthetic chapter directory/body regressions; originals remain immutable."""
import pytest

from inkreference.chapter_layout import (
    chapter_body_span, chapter_label, chapter_sequence, expand_missing_chapters,
    normalize_chapter_body, normalize_document_chapter_bodies,
)
from inkreference.chapter_preprocessing import annotate_chapters
from inkreference.text_processing import split_chapters


def parsed(text):
    chapters = split_chapters(text)["chapters"]
    for index, chapter in enumerate(chapters):
        chapter.update(id=f"chapter-{index + 1}", index=index)
    return chapters


@pytest.mark.parametrize("source,expected", [
    ("正文\n\n第二段\n\n\n末段", "正文\n第二段\n末段"),
    ("正文\r\n\r\n第二段", "正文\r\n第二段"),
    ("正文\r\n第二段", "正文\r\n第二段"),
    ("正文\r\r第二段", "正文\r第二段"),
    ("正文\n \t　\r\n　第二段", "正文\n　第二段"),
    ("第一段\n  第二段  ", "第一段\n  第二段  "),
    ("🌟\r\n\r\n𠮷", "🌟\r\n𠮷"),
])
def test_duplicate_body_lines_collapse_without_reencoding_content(source, expected):
    assert normalize_chapter_body(source) == expected
    assert normalize_chapter_body(expected) == expected


def test_body_boundary_trim_preserves_story_indentation_and_spaces():
    assert normalize_chapter_body("\r\n\r\n　首段  \r\n　后段  \r\n\r\n", trim_boundaries=True) == "　首段  \r\n　后段  "
    assert normalize_chapter_body("  首段  ", trim_boundaries=True) == "  首段  "


def test_document_layout_normalizes_only_body_spans_and_keeps_source_heading_bytes():
    source = "书名🌟\r\n\r\n作者信息\r\n第００１章　标题\r\n\r\n　第一段。\r\n \r\n　第二段。\r\n\r\n第二章 尾声\r\n末段\r\n\r\n"
    chapters = parsed(source)
    normalized = normalize_document_chapter_bodies(source, chapters)
    assert normalized == "书名🌟\r\n\r\n作者信息\r\n第００１章　标题\r\n　第一段。\r\n　第二段。\r\n第二章 尾声\r\n末段\r\n"
    assert source != normalized
    assert "第００１章　标题\r\n" in normalized
    assert normalize_document_chapter_bodies(normalized) == normalized


def test_document_layout_rejects_stale_or_overlapping_spans():
    with pytest.raises(ValueError, match="chapter spans"):
        normalize_document_chapter_bodies("正文", [{"start": 1, "body_start": 0, "end": 2}])


def test_body_span_excludes_heading_and_only_boundary_empty_lines():
    source = "第001章 🌟标题\r\n\r\n　首段。\r\n　尾段。\r\n\r\n第２章 之后\r\n正文"
    first = parsed(source)[0]
    start, end = chapter_body_span(first)
    assert first["text"][start:end] == "　首段。\r\n　尾段。"
    assert source[first["start"] + start:first["start"] + end] == first["text"][start:end]
    assert chapter_label(first) == "第1章 🌟标题"
    assert "".join(c["text"] for c in parsed(source)) == source


def test_empty_body_span_never_contains_the_heading():
    for source in ["第一章 标题", "第一章 标题\n", "第一章 标题\r\n\r\n"]:
        chapter = parsed(source)[0]
        start, end = chapter_body_span(chapter)
        assert chapter["text"][start:end] == ""


def test_lone_cr_chapter_lines_parse_with_exact_original_offsets_and_bytes():
    source = "第一章 星夜\r　正文🌟。\r\r第二章 天明\r　尾声𠮷。"
    result = split_chapters(source)
    chapters = result["chapters"]
    assert [chapter["number"] for chapter in chapters] == [1, 2]
    assert "".join(chapter["text"] for chapter in chapters) == source
    assert result["coverage"]["complete"] is True
    assert [chapter["raw_marker"] for chapter in chapters] == ["第一章 星夜", "第二章 天明"]
    start, end = chapter_body_span(chapters[0])
    assert chapters[0]["text"][start:end] == "　正文🌟。"


def test_label_does_not_duplicate_imported_number_or_invent_number_for_preface():
    assert chapter_label({"kind": "chapter", "number": 12, "title": "第十二章： 夜航"}) == "第12章 夜航"
    assert chapter_label({"kind": "chapter", "number": 12, "title": "第十三章 的回声"}) == "第12章 第十三章 的回声"
    assert chapter_label({"kind": "preface", "number": None, "title": "前置文本"}) == "前置文本"


def test_five_missing_chapters_are_five_individual_blank_virtual_items():
    source = "第11章 上一章\n现有🌟正文。\n第17章 下一章\n其后正文。"
    chapters = parsed(source)
    result = expand_missing_chapters(chapters)
    missing = [item for item in result["chapters"] if item.get("placeholder")]
    assert [item["number"] for item in missing] == [12, 13, 14, 15, 16]
    assert [item["display_label"] for item in missing] == [f"第{n}章" for n in range(12, 17)]
    assert len({item["id"] for item in result["chapters"]}) == 7
    assert all(item["text"] == item["content"] == "" for item in missing)
    assert all(item["start"] == item["end"] == item["body_start"] == chapters[1]["start"] for item in missing)
    assert "".join(item["text"] for item in result["chapters"]) == source
    assert chapters[1]["id"] == "chapter-2"
    assert chapters[1]["index"] == 1
    assert "display_label" not in chapters[1]


def test_zero_and_leading_zero_numbers_do_not_create_negative_placeholders():
    chapters = parsed("第000章 引子\n正文\n第002章 后续\n正文")
    result = expand_missing_chapters(chapters)
    assert [item["number"] for item in result["chapters"]] == [0, 1, 2]


def test_number_restart_is_a_new_sequence_not_duplicate_or_giant_gap():
    chapters = parsed("第1章 首卷\n甲\n第2章 尾声\n乙\n第1章 新卷\n丙\n第3章 末章\n丁")
    structure = chapter_sequence(chapters)
    assert structure["duplicates"] == {}
    assert [gap["missing_numbers"] for gap in structure["gaps"]] == [[2]]
    assert structure["gaps"][0]["before_chapter_id"] == "chapter-4"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_volume_markers_reset_sequence_even_if_numbers_ascend(newline):
    chapters = parsed(newline.join(["第一卷 初见", "第1章 开端", "甲。", "第二卷 远行", "第8章 新卷开端", "乙。", "第9章 后续", "丙。"]))
    structure = chapter_sequence(chapters)
    assert structure["gaps"] == []
    assert structure["duplicates"] == {}


def test_lone_cr_volume_boundary_does_not_invent_five_missing_chapters():
    source = "第11章 之前\r正文。\r第二卷 远行\r第17章 下一卷\r正文。"
    chapters = parsed(source)
    assert [chapter["number"] for chapter in chapters] == [11, 17]
    structure = chapter_sequence(chapters)
    assert structure["gaps"] == []
    assert structure["segments"] == {"chapter-1": 0, "chapter-2": 1}
    assert not any(chapter.get("placeholder") for chapter in expand_missing_chapters(chapters)["chapters"])
    assert "".join(chapter["text"] for chapter in chapters) == source


def test_actual_duplicate_is_flagged_without_duplicate_placeholder_items():
    chapters = parsed("第1章 甲\n正文\n第3章 乙\n正文\n第3章 丙\n正文\nChapter 4 End\n正文")
    structure = chapter_sequence(chapters)
    assert structure["duplicates"] == {"chapter-2": 2, "chapter-3": 2}
    assert [gap["missing_numbers"] for gap in structure["gaps"]] == [[2]]
    result = expand_missing_chapters(chapters)
    assert [item["number"] for item in result["chapters"]] == [1, 2, 3, 3, 4]


def test_mixed_heading_formats_share_one_sequence():
    chapters = parsed("第一章 甲\n正文\nChapter 4 End\n正文")
    assert chapter_sequence(chapters)["gaps"][0]["missing_numbers"] == [2, 3]


def test_hostile_gap_is_bounded_and_explicit_without_losing_any_text():
    source = "第1章 首章\n甲🌟\n第99999999999999999999章 末章\n乙𠮷"
    chapters = parsed(source)
    result = expand_missing_chapters(chapters, max_placeholders=5)
    assert len(result["chapters"]) == 8
    assert result["placeholder_count"] == 5
    remaining = next(item for item in result["chapters"] if item.get("gap_range"))
    assert remaining["number_start"] == 7
    assert remaining["number_end"] == 99999999999999999998
    assert remaining["missing_count"] == result["truncated_missing_count"]
    assert "".join(item["text"] for item in result["chapters"]) == source


def test_global_budget_remains_bounded_across_many_gaps():
    chapters = parsed("第1章 甲\n正文\n第6章 乙\n正文\n第12章 丙\n正文")
    result = expand_missing_chapters(chapters, max_placeholders=2)
    assert result["placeholder_count"] == 2
    assert len([item for item in result["chapters"] if item.get("gap_range")]) == 2


def test_annotate_cache_only_rescans_changed_chapter(monkeypatch):
    import inkreference.chapter_preprocessing as preprocessing
    chapters = parsed("第1章 甲\n正文\n第2章 乙\n正文")
    scans = []
    original = preprocessing.chapter_proposals
    def counted(chapter, options):
        scans.append(chapter["id"])
        return original(chapter, options)
    monkeypatch.setattr(preprocessing, "chapter_proposals", counted)
    proposals = {}
    annotate_chapters(chapters, {}, proposals_by_id=proposals)
    assert scans == ["chapter-1", "chapter-2"]
    scans.clear()
    del proposals["chapter-2"]
    chapters[1]["content"] = "改后的正文🌟"
    annotate_chapters(chapters, {}, proposals_by_id=proposals)
    assert scans == ["chapter-2"]
    assert chapters[1]["body_length"] == 6
    assert chapters[1]["char_count"] == 6
    assert all(chapter["issue_count"] in {0, 1} for chapter in chapters)
