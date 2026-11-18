import pytest

from inkreference.chronicle import chronicle_outline, empty_chronicle, index_source, validate_chronicle
from inkreference.database import DataError


@pytest.fixture
def analysis():
    lines = index_source("叶遥望见一盏绿灯。\n\n许冰挡在桥上。\n叶遥交出钥匙，许冰放她过桥。")
    data = empty_chronicle()
    data["characters"] = [
        {"id": "P001", "name": "叶遥", "aliases": [], "identity": "旅客", "basis": "携行李赶路", "source_lines": [1]},
        {"id": "P002", "name": "许冰", "aliases": [], "identity": "守桥人", "basis": "负责守桥", "source_lines": [3]},
    ]
    data["storylines"] = [
        {"id": "S01", "title": "过桥", "character_ids": ["P001", "P002"], "beginning": "叶遥来到桥头",
         "development": "许冰拦路", "turn": "叶遥交出钥匙", "conclusion": "许冰放行"},
    ]
    data["events"] = [
        {"id": "E001", "title": "叶遥被拦在桥头", "type": "压抑", "story_time": "当晚",
         "location": "桥头", "description": "许冰挡在桥上，叶遥被挡在桥头。", "storyline_ids": ["S01", "F01"],
         "basis": "第一章现场，许冰拦住叶遥。", "source_lines": [3, 1]},
        {"id": "E002", "title": "叶遥获准过桥", "type": "高潮", "story_time": "随后",
         "location": "桥头", "description": "叶遥交出钥匙，许冰放她过桥。", "storyline_ids": ["S01"],
         "basis": "第一章现场，叶遥交出钥匙。", "source_lines": [4]},
    ]
    data["relationships"] = [
        {"character_a_id": "P002", "character_b_id": "P001", "narrative_order": 3, "end_order": 3,
         "relation": "阻拦者", "basis": "许冰挡在桥上。", "source_lines": [3]},
        {"character_a_id": "P002", "character_b_id": "P001", "narrative_order": 4,
         "relation": "放行者", "basis": "许冰放行。", "source_lines": [4]},
    ]
    data["foreshadowing"] = [
        {"id": "F01", "title": "桥头绿灯", "status": "未收回", "storyline_id": "S01",
         "plant": {"basis": "第一章现场，叶遥望见绿灯。", "source_lines": [1], "event_id": None}, "return": None},
    ]
    return data, lines


def test_chronicle_preserves_physical_source_and_narrative_order(analysis):
    data, lines = analysis
    assert lines == {1: "叶遥望见一盏绿灯。", 2: "", 3: "许冰挡在桥上。", 4: "叶遥交出钥匙，许冰放她过桥。"}
    assert index_source("甲\r\n\r\n乙\r丙\n") == {1: "甲", 2: "", 3: "乙", 4: "丙"}
    assert index_source("") == {}
    result = validate_chronicle(data, lines)
    assert result == data
    result["events"][0]["title"] = "临时标题"
    assert data["events"][0]["title"] == "叶遥被拦在桥头"

    data["events"].reverse()
    with pytest.raises(DataError, match="首条定位依据需按叙述顺序排列"):
        validate_chronicle(data, lines)


def test_chronicle_rejects_blanks_and_dangling_references(analysis):
    data, lines = analysis
    data["events"][0]["source_lines"] = [2]
    with pytest.raises(DataError, match="找不到可用的原文 L2"):
        validate_chronicle(data, lines)
    data["events"][0]["source_lines"] = [3]
    data["events"][0]["storyline_ids"] = ["F01", "S01"]
    with pytest.raises(DataError, match="首个所属故事线"):
        validate_chronicle(data, list(lines.values()))
    data["events"][0]["storyline_ids"] = ["S99"]
    with pytest.raises(DataError, match="找不到编号 S99"):
        validate_chronicle(data, lines)


def test_reader_export_has_exact_fields_and_resolved_links(analysis):
    data, lines = analysis
    events = chronicle_outline(validate_chronicle(data, lines))
    assert list(events[0]) == ["叙述序号", "标题", "类型", "故事中时间", "地点", "事件描述", "所属故事线", "定位依据"]
    assert events[0]["类型"] == "压抑"
    assert events[0]["定位依据"].endswith("L3、L1")
    assert events[0]["所属故事线"] == ["S01 过桥", "F01 桥头绿灯"]
    clue = data["foreshadowing"][0]
    clue["plant"]["event_id"] = "E001"
    clue["status"] = "已收回"
    clue["return"] = {"basis": "第一章现场，许冰放行后说绿灯表示桥已开放。", "source_lines": [4], "event_id": "E002"}
    events = chronicle_outline(validate_chronicle(data, lines))
    assert events[0]["所属故事线"][1] == "F01 桥头绿灯（埋设）"
    assert events[1]["所属故事线"][1] == "F01 桥头绿灯（收回）"


def test_chronicle_rejects_replayed_or_unsupported_relationship_state(analysis):
    data, lines = analysis
    data["relationships"][0]["source_lines"] = [4]
    with pytest.raises(DataError, match="成立位置应有原文依据"):
        validate_chronicle(data, lines)
    data["relationships"][0]["source_lines"] = [3]
    data["relationships"][0]["end_order"] = 2
    with pytest.raises(DataError, match="结束位置早于成立位置"):
        validate_chronicle(data, lines)


def test_calibration_choices_have_different_consequences(analysis):
    data, lines = analysis
    data["questions"] = [
        {"id": "Q01", "question": "过桥前后怎么划分？", "source_lines": [3, 4],
         "options": [{"id": "A", "label": "合并", "effect": "保留一次过桥"},
                     {"id": "B", "label": "拆分", "effect": "保留一次过桥"}]},
    ]
    with pytest.raises(DataError, match="不同做法和结果"):
        validate_chronicle(data, lines)
    data["questions"][0]["options"][1]["effect"] = "按遇阻和脱困分别记录"
    assert validate_chronicle(data, lines)["questions"] == data["questions"]
