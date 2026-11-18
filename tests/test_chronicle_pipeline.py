import json

import pytest

from inkreference.chronicle import chronicle_outline, empty_chronicle
from inkreference.chronicle_pipeline import apply_calibration, run_chronicle
from inkreference.database import DataError


def event(identifier, title, line, storyline='S1'):
    return {"id": identifier, "title": title, "type": "推进", "story_time": "当日", "location": "河口",
            "description": title, "storyline_ids": [storyline], "basis": f"河口现场，阿生{title}。", "source_lines": [line]}


def story(identifier, title):
    return {"id": identifier, "title": title, "character_ids": [], "beginning": "阿生到达河口。",
            "development": "", "turn": "", "conclusion": ""}


def test_full_book_extract_reconciles_distant_matching_threads_and_keeps_physical_anchors():
    lines = ["第一章 河口", "阿生抵达河口，拜师。", "", "第二章 告别", "阿生出师。"]
    progress = []
    requests = []
    def generate(prompt):
        requests.append(prompt)
        if '当前原文：' in prompt:
            source = prompt.split('当前原文：')[1]
            second = '告别' in source
            return json.dumps({**empty_chronicle(), 'events': [event('E1', '出师' if second else '拜师', 5 if second else 2)],
                               'storylines': [story('S1', '拜师学艺')]})
        if '当前 storylines {"id":"B1-S001"' in prompt:
            assert 'B2-S001' in prompt
            revised = {**story('B1-S001', '拜师学艺'), 'conclusion': '阿生出师。'}
            return json.dumps({'merge': {'storylines': [{'from': 'B2-S001', 'into': 'B1-S001'}]},
                               'put': {'storylines': [revised]}})
        return '{}'
    result = run_chronicle(lines, generate, lambda *args: progress.append(args), input_budget=5950)
    assert all(len(text.encode()) <= 5950 for text in requests)
    assert result['storylines'][0]['conclusion'] == '阿生出师。'
    assert [x['storyline_ids'] for x in result['events']] == [['S001'], ['S001']]
    assert [x['source_lines'] for x in result['events']] == [[2], [5]]
    assert [x['标题'] for x in chronicle_outline(result)] == ['拜师', '出师']
    assert result['questions'] == []
    assert ('extract', 2, 2) in progress and progress[-1] == ('review', 1, 1)


def test_long_paragraph_retries_from_smaller_excerpts_without_losing_original_content():
    # Use two recognizable pieces in a single physical line, including spaces.
    line = '开头  ' + '原样文字' * 160 + '  末尾'
    source_reads = []
    def generate(prompt):
        source = prompt.split('当前原文：\n', 1)[1].split('\n上次结果', 1)[0]
        if len(source.encode()) > 1200:
            return '{'
        source_reads.append(source.removeprefix('[1] '))
        return json.dumps(empty_chronicle())
    result = run_chronicle([line], generate, lambda *_: None, input_budget=5950)
    assert result == empty_chronicle()
    assert ''.join(source_reads) == line


def test_calibration_merge_keeps_eligible_references_and_requires_concrete_changes():
    source = ['阿生当日拜师。', '阿生接过师父给的令牌。']
    question = {'id': 'Q01', 'question': '拜师和交接合为一个事件吗？', 'options': [
        {'id': 'A', 'label': '保留两步', 'effect': '拜师和交接各保留一条事件。'},
        {'id': 'B', 'label': '合并', 'effect': '合成一次拜师。'},
    ], 'source_lines': [1, 2]}
    clue = {'id': 'F1', 'title': '令牌来历', 'status': '未收回', 'plant': {'basis': '河口现场，阿生接过令牌。', 'source_lines': [2], 'event_id': 'E2'}, 'return': None, 'storyline_id': 'S1'}
    data = {**empty_chronicle(), 'events': [event('E1', '拜师', 1), event('E2', '接过令牌', 2)],
            'storylines': [story('S1', '拜师学艺')], 'foreshadowing': [clue], 'questions': [question]}
    with pytest.raises(DataError, match='校准需要落实'):
        apply_calibration(data, question, 'B', '', source, lambda prompt: '{}', scope='item', input_budget=5950)
    merged = {**event('E1', '阿生完成拜师', 1), 'description': '阿生拜师后接过师父给的令牌。', 'source_lines': [1, 2]}
    response = {'merge': {'events': [{'from': 'E2', 'into': 'E1'}]}, 'put': {'events': [merged]}, 'rule': '属于同一次行动的手续和交接合为一条事件。'}
    edited, rule = apply_calibration(data, question, 'B', '', source, lambda prompt: json.dumps(response), scope='similar', input_budget=5950)
    assert edited['events'] == [{**merged, 'storyline_ids': ['S1', 'F1']}]
    assert edited['foreshadowing'][0]['plant']['event_id'] == 'E1'
    assert not edited['questions']
    assert rule == response['rule']
    assert len(data['events']) == 2
