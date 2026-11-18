"""Exact PS review spans and chapter-local quick-location evidence."""
from __future__ import annotations

import pytest

from inkreference.chapter_preprocessing import AUTHOR_KEYWORDS, LEGACY_AUTHOR_KEYWORDS, chapter_proposals, validate_settings
from inkreference.documents import DocumentStore


def chapter(body: str, heading: str = "第1章 出发") -> dict:
    text = f"{heading}\n{body}"
    return {"id": "chapter-1", "index": 0, "kind": "chapter", "number": 1,
            "title": heading.split(" ", 1)[-1], "raw_marker": heading,
            "pattern": "第N章", "start": 0, "end": len(text), "body_start": len(heading) + 1,
            "content": body, "text": text}


@pytest.mark.parametrize("marker", ["PS：", "PS:", "ps：", "pS:"])
def test_postscript_defaults_keep_exact_unicode_span_and_do_not_mutate_source(marker):
    source = chapter(f"🌙他点燃了炉火。\n{marker}今天先到这里。")
    original = source["text"]
    marks = chapter_proposals(source, validate_settings({}))
    author = [mark for mark in marks if mark["kind"] == "author_note"]
    assert len(author) == 1
    mark = author[0]
    assert mark["scope"] == "body" and mark["removable"] and mark["enabled"]
    assert mark["text"] == f"{marker}今天先到这里。"
    assert original[mark["start"]:mark["end"]] == mark["text"]
    assert source["text"] == original


def test_new_default_exposes_ps_without_changing_legacy_keyword_identity():
    assert AUTHOR_KEYWORDS == [*LEGACY_AUTHOR_KEYWORDS, "PS："]
    assert validate_settings({})["author_keywords"] == AUTHOR_KEYWORDS
    assert "PS：" not in LEGACY_AUTHOR_KEYWORDS


@pytest.mark.parametrize("body", [
    "他在信上写着PS：等待天亮。", "“PS：等待天亮。”她说道。", "「PS：这句话很重要。」",
    "SPS：并不是附言。", "他讨论了ps:命令。", "PS：" + "长篇剧情正文。" * 30,
])
def test_postscript_does_not_match_inline_dialogue_or_long_story_paragraphs(body):
    assert not [mark for mark in chapter_proposals(chapter(body), validate_settings({}))
                if mark["kind"] == "author_note"]


@pytest.mark.parametrize("keywords", [[], ["自定关键词"], LEGACY_AUTHOR_KEYWORDS])
def test_explicit_keyword_rules_remain_authoritative(keywords):
    options = validate_settings({"author_keywords": keywords})
    assert options["author_keywords"] == keywords
    assert not [mark for mark in chapter_proposals(chapter("正文。\nPS：今天先到这里。"), options)
                if mark["kind"] == "author_note"]


def test_explicit_ascii_ps_keyword_is_case_and_colon_compatible():
    options = validate_settings({"author_keywords": ["ps:"]})
    assert chapter_proposals(chapter("正文。\npS：今天先到这里。"), options)[0]["text"] == "pS：今天先到这里。"


def test_red_evidence_is_chapter_local_after_far_non_ascii_prefix_and_heading_noise(tmp_path):
    store = DocumentStore(tmp_path / "data")
    body = "🌙炉火映在窗上。\n" * 3000 + "PS：明天继续。"
    original = "第1章 序章\n前文。\n第2章 星舟（求订阅）\n" + body
    document = store.upload("定位示例.txt", original.encode(), preserve_text=True)
    summary = store.get_version_summary(document["id"])
    part = store.chapter_index(document["id"], summary["id"])["chapters"][1]
    page = store.get_chapter(document["id"], summary["id"], part["id"], body_only=True)
    title = next(mark for mark in page["proposals"] if mark["scope"] == "heading")
    finding = next(mark for mark in page["proposals"] if mark["text"] == "PS：明天继续。")
    assert title["text"] == "（求订阅）"
    assert finding["start"] > 20000
    assert part["first_body_issue_start"] == finding["start"]
    page_start = finding["start"] // 20000 * 20000
    located = store.get_chapter(document["id"], summary["id"], part["id"], start=page_start, body_only=True)
    assert located["editable_start"] <= finding["start"] < located["editable_end"]
    points = list(located["editable_text"])
    offsets = located["source_offset_map"]
    assert "".join(point for point, raw in zip(points, offsets) if finding["start"] <= raw < finding["end"]) == finding["text"]
    assert store.get_original(document["id"])[0] == original.encode()
