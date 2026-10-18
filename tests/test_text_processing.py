"""Synthetic source-integrity checks; no private corpus or model needed."""

import pytest

from inkreference.reuse_check import SYNTHETIC_STORY
from inkreference.text_processing import analyze_text, chunk_text, clean_text, cn2int, diagnose_text, split_chapters


@pytest.mark.parametrize("value,expected", [
    ("十二", 12), ("二百零三", 203), ("１２", 12), ("一万零三", 10003),
    ("十二万三千零五", 123005), ("〇", 0), ("二〇二六", 2026), ("", None), ("正文", None),
])
def test_cn_numerals(value, expected):
    assert cn2int(value) == expected


def test_cleaning_preserves_original_and_tracks_versions():
    source = '\ufeff第一章 钥匙\r\n他说：&quot;甲&amp;乙&quot;。\x00\r尾标'
    result = clean_text(source)
    assert result["original_text"] == source
    assert result["cleaned_text"] == '第一章 钥匙\n他说："甲&乙"。\n尾标'
    assert result["original_sha256"] != result["cleaned_sha256"]
    records = result["transformations"]
    assert {record["stage"] for record in records} == {"leading_bom", "line_endings", "html_entities", "control_characters"}
    assert records[0]["input_sha256"] == result["original_sha256"]
    assert records[-1]["output_sha256"] == result["cleaned_sha256"]
    assert all(left["output_sha256"] == right["input_sha256"] for left, right in zip(records, records[1:]))


def test_html_entities_are_decoded_not_deleted_or_treated_as_markup():
    source = "&lt;门&gt;&amp;&quot;灯&quot;&#x4e2d;&#13;&#10;正文"
    assert clean_text(source)["cleaned_text"] == '<门>&"灯"中\n正文'


def test_random_id_neighbors_survive_confirmed_removal():
    source = "左3Abq5FQkXVhYkEy8u3y右又6Bcd7GHlABhMNJz9p2q后"
    assert clean_text(source)["cleaned_text"] == source
    assert clean_text(source, remove_random_ids=True)["cleaned_text"] == "左右又后"
    findings = diagnose_text(source)
    ids = [finding for finding in findings if finding["code"] == "random_id"]
    assert len(ids) == 2
    assert all(source[finding["start"]:finding["end"]] == finding["sample"] for finding in ids)


def test_diagnostics_do_not_delete_questionable_story_text():
    source = "求月票也可能出现在对白中。锟斤拷与��需要核对。<!--保留注释--><p>正文</p>"
    result = clean_text(source)
    assert result["cleaned_text"] == source
    assert {finding["code"] for finding in result["diagnostics"]} >= {
        "author_note", "mojibake_signature", "replacement_character", "html_comment", "html_tag"}


@pytest.mark.parametrize("length", [0, 1, 2999, 3000, 3001, 3502, 6000, 6001, 33013])
def test_fallback_has_no_gaps_or_missing_tail(length):
    source = "甲" * max(0, length - 3) + "尾标。" if length >= 3 else "甲" * length
    result = split_chapters(source)
    assert "".join(chapter["text"] for chapter in result["chapters"]) == source
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["covered_characters"] == len(source)


def test_numbered_short_work_and_preface_remain_complete():
    result = analyze_text(SYNTHETIC_STORY)
    chapters = result["chapter_analysis"]["chapters"]
    assert chapters[0]["kind"] == "preface"
    assert [chapter["number"] for chapter in chapters[1:]] == [1, 2, 3, 4]
    assert [chapter["title"] for chapter in chapters[1:]] == ["铜钥匙", "白手套", "身份", "归还"]
    assert "".join(chapter["text"] for chapter in chapters) == result["cleaned_text"]
    assert result["chapter_analysis"]["coverage"]["complete"] is True
    assert result["cleaned_text"].endswith("全文尾标。")


def test_blank_lines_after_heading_do_not_become_title_or_disappear():
    source = "第一章\n\n正文第一字。\n第二章 尾声\n\n结束。"
    result = split_chapters(source)
    assert len(result["chapters"]) == 2
    assert result["chapters"][0]["title"] == ""
    assert result["chapters"][0]["content"].startswith("\n\n正文第一字")
    assert result["coverage"]["complete"] is True


def test_long_single_chapter_can_be_chunked_without_truncation():
    source = "第一章 长夜\n" + "故事正文。" * 7000 + "结束尾标。"
    result = split_chapters(source)
    assert len(result["chapters"]) == 1
    chunks = chunk_text(result["chapters"][0]["text"], 3000)
    assert len(chunks) > 1
    assert all(len(chunk["text"]) <= 3000 for chunk in chunks)
    assert "".join(chunk["text"] for chunk in chunks) == source


@pytest.mark.parametrize("invalid", [0, -1, 2.5, True, "3000"])
def test_invalid_chunk_size_is_rejected(invalid):
    with pytest.raises(ValueError):
        chunk_text("正文", invalid)
    with pytest.raises(ValueError):
        split_chapters("第一章 标题\n正文", fallback_chars=invalid)


def test_cleaning_is_stable_for_one_level_entities_and_normal_text():
    first = clean_text("正文\r\n甲&amp;乙")
    second = clean_text(first["cleaned_text"])
    assert first["cleaned_text"] == second["cleaned_text"]
    assert second["transformations"] == []


def test_bytes_are_not_silently_decoded_with_replacement():
    with pytest.raises(TypeError):
        clean_text(b"\xff")
