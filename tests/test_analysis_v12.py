"""Invented v4 contracts, copy migration, progress and phased-state semantics."""
import copy
import json
import sqlite3
from contextlib import closing

import pytest

from inkreference.analysis_projection import project_sections
from inkreference.character_contract import normalize_character
from inkreference.chronicle_schema import (REFERENCE_V3_SCHEMA, REFERENCE_V4_SCHEMA, adapt_v3_section,
    canonical_stage_schema, normalize_v4_section, validate_v4_section, validate_stage_result, validate_links)
from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.language_analysis import naming_statistics
from inkreference.progress import narrative_chapter_context, narrative_progress, narrative_progress_v4, prepare_progress_sources_v4
from inkreference.reference_analysis import ReferenceAnalysisStore, aggregate_style_v4, validate_section


def event(identity='e1', progress=20, **changes):
    return {'id': identity, 'name': '抵达旧城', 'theme': '旅途', 'description': '林青进入旧城', 'narrative_progress': progress,
            'story_time': None, 'participants': [], 'setting_ids': [], 'causes': [], 'results': [], 'plotline_ids': [], 'volume': None, **changes}


def plot(events=None, **changes):
    return {'schema_version': 4, 'logline': '旅人在旧城寻找归途。', 'events': events or [], 'plotlines': [], **changes}


def snapshot(identity='p1', start=20, **changes):
    return {'id': identity, 'name': '初入旧城', 'narrative_range': {'start': start, 'end': None}, 'story_time': None,
            'event_ids': [], 'identity': '', 'ability': '', 'personality': '', 'appearance': '', 'visual_design': '', **changes}


def person(identity='c1', **changes):
    return {'schema_version': 4, 'id': identity, 'name': '林青', 'aliases': [], 'role_tag': '主角', 'temperament': '利他',
            'visual_design': '灰衣', 'initial_state': {'gender': '男', 'age': '20', 'identity': '旅人', 'ability': '',
            'personality': '', 'appearance': '', 'relationships': [], 'narrative_progress': 0, 'story_time': None}, 'snapshots': [], **changes}


def style(**changes):
    return {'schema_version': 4, 'focalization': '有限第三人称视角', 'naming_style': '双字名为主',
            'rhetoric': [{'description': '比拟', 'examples': ['旧城睡着了。']}],
            'dialogue_style': {'description': '简短问句', 'examples': ['你去哪里？']},
            'narrative_composition': [{'kind': kind, 'percentage': 25, 'assessment': ''}
                for kind in ('action', 'description', 'psychology', 'exposition')], **changes}


def proof(source, quote, chapter=1):
    return {'revealed_at': {'chapter': chapter, 'offset': source['text'].index(quote)},
            'source_version_id': source['id'], 'evidence': quote}


def run(source, reference, chunk='chunk1', run_id='run1', doc='book'):
    return {'id': run_id, 'document_id': doc, 'version_id': source['id'], 'candidates': [{'chunk_id': chunk,
            'index': 0, 'start': 0, 'end': len(source['text']), 'status': 'completed', 'result': {'reference': reference}}]}


def test_fourth_contract_does_not_change_historical_strict_shapes():
    before = copy.deepcopy(REFERENCE_V3_SCHEMA)
    new = canonical_stage_schema('language', 4)
    assert new['properties']['style']['properties']['narrative_composition']['items']['properties']['percentage']['type'] == 'number'
    assert 'naming' in canonical_stage_schema('language', 3)['properties']['style']['properties']
    assert 'naming_style' in new['properties']['style']['properties']
    assert REFERENCE_V3_SCHEMA == before
    assert 'snapshots' not in REFERENCE_V4_SCHEMA['properties']['settings']['items']['properties']


def test_relationships_inherit_only_when_omitted_and_copy_without_mutation():
    initial = person()['initial_state']
    initial['relationships'] = [{'character_id': 'c2', 'relationship': '同伴'}]
    source = person(initial_state=initial, snapshots=[snapshot('s1', 20), snapshot('s2', 50, relationships=[]), snapshot('s3', 70)])
    original = copy.deepcopy(source)
    value = normalize_character(source)
    assert value['snapshots'][0]['relationships'] == initial['relationships']
    assert value['snapshots'][1]['relationships'] == value['snapshots'][2]['relationships'] == []
    assert value['snapshots'][0]['name'] == '初入旧城'
    assert source == original
    value['snapshots'][0]['relationships'][0]['relationship'] = '故交'
    assert initial['relationships'][0]['relationship'] == '同伴'


def test_phase_event_lower_boundary_uses_previous_phase_start():
    phases = [snapshot('s1', 20, event_ids=['e0'], relationships=[]), snapshot('s2', 60, event_ids=['e2'], relationships=[])]
    sections = {'plot': plot([event('e0', 10), event('e2', 20)]), 'characters': [person(snapshots=phases)]}
    validate_links(sections)
    sections['characters'][0]['snapshots'][1]['event_ids'] = ['e0']
    with pytest.raises(DataError, match='上一阶段'): validate_links(sections)


def test_setting_snapshots_become_appendices_and_original_is_unchanged():
    old = [{'schema_version': 3, 'id': 'castle', 'title': '旧城', 'category': 'geography', 'content': '石城墙',
            'earlier_setting_id': None, 'narrative_progress': 10, 'story_time': None,
            'snapshots': [{'id': 'gate', 'narrative_range': {'start': 30, 'end': 50}, 'story_time': None, 'event_ids': [], 'content': '东门封闭'},
                          {'id': 'open', 'narrative_range': {'start': 50, 'end': None}, 'story_time': None, 'event_ids': [], 'content': '东门重开'}]}]
    original = copy.deepcopy(old)
    value = adapt_v3_section('settings', old)
    assert [item['content'] for item in value] == ['石城墙', '东门封闭', '东门重开']
    assert value[1]['earlier_setting_id'] == 'castle'
    assert value[2]['earlier_setting_id'] == value[1]['id']
    assert all('snapshots' not in item for item in value)
    validate_v4_section('settings', value)
    validate_links({'settings': value})
    assert value == adapt_v3_section('settings', old) and old == original


def test_legacy_language_adaptation_keeps_unknown_percentages_and_known_judgments():
    old = {'schema_version': 3, 'narrative_person': '第三人称', 'focalization': '有限视角', 'register': '书面', 'colloquialism': '中等',
           'rhetoric': ['比拟'], 'dialogue_style': '简短', 'catchphrases': [], 'naming': {'patterns': ['双字'], 'style': '简洁', 'examples': ['林青']},
           'narrative_composition': [{'kind': 'action', 'assessment': '动作紧凑'}], 'observations': []}
    original = copy.deepcopy(old)
    value = adapt_v3_section('style', old)
    assert value['focalization'] == '有限第三人称视角'
    assert value['naming_style'] == '简洁\n双字\n林青'
    assert all(item['percentage'] is None for item in value['narrative_composition'])
    assert not {'register', 'narrative_person', 'colloquialism', 'observations', 'catchphrases'} & value.keys()
    validate_v4_section('style', value)
    assert old == original


@pytest.mark.parametrize('changes', [
    {'focalization': '第三人称'}, {'register': '书面'}, {'narrative_person': '第三人称'},
    {'narrative_composition': [{'kind': 'action', 'percentage': 100, 'assessment': ''}]},
    {'narrative_composition': [{'kind': kind, 'percentage': 20, 'assessment': ''} for kind in ('action', 'description', 'psychology', 'exposition')]},
])
def test_new_language_rejects_removed_fields_bad_viewpoint_or_invalid_composition(changes):
    with pytest.raises(DataError): validate_v4_section('style', style(**changes))


def test_llm_examples_and_composition_require_real_source_and_manual_can_add_observations():
    source = {'id': 'v1', 'text': '旧城睡着了。你去哪里？'}
    value = style()
    for item in value['narrative_composition']: item.update(proof(source, '旧城睡着了。', chapter=None))
    validate_v4_section('style', value, source['text'], require_evidence=True)
    bad = copy.deepcopy(value); bad['dialogue_style']['examples'] = ['虚构示例']
    with pytest.raises(DataError, match='原文'): validate_v4_section('style', bad, source['text'], require_evidence=True)
    bad['dialogue_style']['examples'] = []
    with pytest.raises(DataError, match='示例'): validate_v4_section('style', bad, source['text'], require_evidence=True)
    validate_v4_section('style', style())


def test_naming_statistics_are_identity_based_compound_surname_top10_and_local():
    names = [{'id': 'a', 'name': '欧阳青云'}, {'id': 'a', 'name': '欧阳青云'}, {'id': 'b', 'name': '林青'},
             {'id': 'c', 'name': '林云'}, {'id': 'd', 'name': '克莱恩·莫雷蒂'}, {'id': 'e', 'name': 'Alice'},
             {'id': 'f', 'name': '无名'}]
    result = naming_statistics(names)
    assert result['eligible_names'] == 3 and result['given_character_count'] == 4
    assert result['surnames'][0] == {'character': '林', 'count': 2, 'percentage': 66.6667}
    assert result['surnames'][1]['character'] == '欧阳'
    assert result['given_characters'] == [{'character': '云', 'count': 2, 'percentage': 50}, {'character': '青', 'count': 2, 'percentage': 50}]
    assert len(naming_statistics([{'id': str(i), 'name': chr(0x4e00+i)+'青'} for i in range(100)])['surnames']) <= 10


def test_gap_inclusive_v4_progress_and_old_v3_progress_remain_distinct():
    source = {'text': '第一段。第三段。', 'chapters': [{'number': 1, 'start': 0, 'body_start': 1, 'end': 4, 'heading': '第1章'},
              {'number': 3, 'start': 4, 'body_start': 5, 'end': 8, 'heading': '第3章'}]}
    original = copy.deepcopy(source)
    assert narrative_progress(source, {'chapter': 1}) == 50
    assert narrative_progress_v4(source, {'chapter': 1}) == pytest.approx(100/3, abs=.00001)
    assert narrative_progress_v4(source, {'offset': 5}) == 100
    assert narrative_chapter_context(source['chapters']) == {'chapter_count': 3, 'chapter_numbers': [1, 2, 3]}
    indexed = prepare_progress_sources_v4({'v1': source})
    assert narrative_progress_v4(indexed['v1'], {'chapter': 1}) == pytest.approx(100/3, abs=.00001)
    assert source == original


def test_reset_and_hostile_numbering_and_preface_do_not_allocate_giant_lists():
    assert narrative_chapter_context([{'number': 1}, {'number': 2}, {'number': 1}]) == {'chapter_count': 3}
    assert narrative_chapter_context([{'number': 1}, {'number': 10**12}]) == {'chapter_count': 2}
    assert narrative_chapter_context([{'number': 1, 'kind': 'fallback'}, {'number': 0, 'kind': 'preface'}]) == {'chapter_count': 0}


def test_fourth_projection_has_flat_views_named_phases_no_outlines_and_keeps_sources():
    old = {'plot': plot([event('e1', 20), event('e2', 60)]), 'characters': [person(snapshots=[snapshot('s1', 20, relationships=[], event_ids=['e1'])])], 'style': style()}
    original = copy.deepcopy(old)
    view = project_sections(old, target_version=4, progress=30)
    assert [item['id'] for item in view['plot']['events']] == ['e1']
    assert set(view['plot']['views']) == {'narrative', 'story'}
    assert 'outlines' not in view['plot'] and view['plot']['logline'] == old['plot']['logline']
    assert view['characters'][0]['current_snapshot']['name'] == '初入旧城'
    assert view['style']['deterministic_statistics']['naming']['eligible_names'] == 1
    assert old == original


def test_manual_save_archives_full_old_setting_and_history_restore(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    old = [{'schema_version': 3, 'id': 's1', 'title': '旧城', 'category': 'geography', 'content': '城墙', 'earlier_setting_id': None,
            'narrative_progress': 0, 'story_time': None, 'snapshots': []}]
    state = store.save_section('book', 'settings', old, 0)
    original = copy.deepcopy(state['sections']['settings'])
    view = store.project('book', target_version=4)
    assert store.get('book')['sections']['settings'] == original
    saved = store.save_section('book', 'settings', view['sections']['settings'], state['revision'])
    with closing(sqlite3.connect(store.path)) as db:
        archived = json.loads(db.execute('SELECT data_json FROM reference_contract_archives WHERE document_id=? AND section=?', ('book', 'settings')).fetchone()[0])
    assert archived == original and saved['sections']['settings'][0]['schema_version'] == 4
    restored = store.restore('book', state['revision'], saved['revision'])
    assert restored['sections']['settings'] == original


def test_v4_candidate_proof_and_progress_context_actual_document(tmp_path):
    docs = DocumentStore(tmp_path)
    document = docs.upload('book.txt', '第1章 入城\n林青入城。\n第3章 离开\n林青离城。'.encode())
    source = docs.get_version(document['id'])
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    value = plot([event(progress=1, **proof(source, '林青入城。'))])
    saved = store.commit_candidate(run(source, {'plot': value}, doc=document['id']), 'chunk1', ['plot'], 0)
    assert saved['sections']['plot']['events'][0]['narrative_progress'] == pytest.approx(100/3, abs=.00001)
    projected = store.project(document['id'], target_version=4)
    assert projected['narrative_context']['chapter_count'] == 3
    assert projected['narrative_context']['chapter_numbers'] == [1, 2, 3]
    assert store.get(document['id'])['revision'] == saved['revision']


def test_v4_approval_keeps_manual_progress_and_legacy3_candidate_can_merge(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    current = store.save_section('book', 'plot', plot([event(progress=12)]), 0)
    value = plot([event('e2', 30, **proof({'id': 'v1', 'text': '归来'}, '归来', None))]); value['schema_version'] = 3
    saved = store.commit_candidate(run({'id': 'v1', 'text': '归来'}, {'plot': value}), 'chunk1', ['plot'], current['revision'])
    assert saved['sections']['plot']['schema_version'] == 4
    assert saved['sections']['plot']['events'][0]['narrative_progress'] == 12
    assert len(saved['sections']['plot']['events']) == 2
    validate_section('plot', saved['sections']['plot'])


def test_plotline_copy_adapter_uses_time_nodes_and_roundtrip_save(tmp_path):
    old = {'schema_version': 3, 'logline': '', 'events': [], 'plotlines': [{'id': 'l1', 'title': '寻路', 'goal': '找到出口',
           'stages': [{'id': 'n1', 'goal': '寻找地图', 'status': 'open', 'event_ids': [], 'narrative_progress': 20, 'story_time': None}]}]}
    view = project_sections({'plot': old}, target_version=4)['plot']
    assert view['plotlines'][0]['description'] == '找到出口'
    assert view['plotlines'][0]['time_nodes'][0]['description'] == '寻找地图'
    store = ReferenceAnalysisStore(tmp_path)
    saved = store.save_section('book', 'plot', view, 0)['sections']['plot']
    assert 'views' not in saved and 'outlines' not in saved


def test_weighted_style_composition_and_examples_are_bounded_by_chunk_coverage():
    first, second = style(), style()
    second['narrative_composition'][0]['percentage'] = 55
    second['narrative_composition'][1]['percentage'] = 15
    second['narrative_composition'][2]['percentage'] = 15
    second['narrative_composition'][3]['percentage'] = 15
    result = aggregate_style_v4([{'data': first, 'chars': 100}, {'data': second, 'chars': 300}])
    assert [item['percentage'] for item in result['narrative_composition']] == [47.5, 17.5, 17.5, 17.5]
    assert result['dialogue_style']['examples'] == ['你去哪里？']
    validate_v4_section('style', result)


@pytest.mark.parametrize(('version','stage','expected'), [
    (2, 'plot', '6b43c9729e8a0d62280e2e9692c77dd450a84b4cd141c6a6078436673f427145'),
    (2, 'characters', '7e9c74f1d100aa3fbcca76d8d271de70003e638a1e9125a3b251a946e0c719c0'),
    (2, 'settings', '8f997cfcaa91c10d21a8053f3ae7f1cd9754365d7c4126ce2b9459e8672a4514'),
    (2, 'language', '33cdd5c47ab29dc415f66bece14a0614616b7fe7116a08013e40c5632eec2a6b'),
    (3, 'plot', '0d75d194e92a2e8cd56e5be3536bfb38b676ecbf94b4d7c09f158fcd0f1a887e'),
    (3, 'characters', '6eca96845b29fd805e5cfee89cc57561c13627749fddc4be28f4c22a2d0984ef'),
    (3, 'settings', 'cbf2ba5dfa4366819dbda5ab6911483c684c7c3cc5bf65303b323d10209e5126'),
    (3, 'language', 'd2c01e67ea13f88e1f885aeb59b59d1c00f36474d9635e8acd64f016899e1d31'),
])
def test_frozen_provider_schema_hashes_match_v11_commit_6338b7e(version, stage, expected):
    import hashlib
    rendered = json.dumps(canonical_stage_schema(stage, version), sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    assert hashlib.sha256(rendered.encode()).hexdigest() == expected


def test_actual_document_language_approval_preserves_local_statistics_and_snapshot_style(tmp_path):
    docs = DocumentStore(tmp_path)
    document = docs.upload('book.txt', '第1章 入城\n旧城睡着了。你去哪里？'.encode())
    source = docs.get_version(document['id'])
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    state = store.save_section(document['id'], 'characters', [person()], 0)
    value = style()
    for item in value['narrative_composition']: item.update(proof(source, '旧城睡着了。'))
    saved = store.commit_candidate(run(source, {'style': value}, doc=document['id']), 'chunk1', ['style'], state['revision'])
    stats = saved['sections']['style']['deterministic_statistics']
    assert stats['coverage']['chapters'] == 1
    assert stats['sentence_length']['count'] == 2
    assert stats['naming']['eligible_names'] == 1
    changed = copy.deepcopy(saved['sections']['style']); changed['naming_style'] = '手工保留的规律'
    edited = store.save_section(document['id'], 'style', changed, saved['revision'])
    result = store.commit_candidate(run(source, {'style': value}, run_id='r2', doc=document['id']), 'chunk1', ['style'], edited['revision'])
    assert result['sections']['style']['naming_style'] == '手工保留的规律'
    assert result['sections']['style']['deterministic_statistics']['naming']['surnames'][0]['character'] == '林'


def test_fourth_style_commit_rejects_forged_examples_with_atomic_revision(tmp_path):
    docs = DocumentStore(tmp_path)
    document = docs.upload('book.txt', '旧城睡着了。你去哪里？'.encode())
    source = docs.get_version(document['id'])
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    value = style()
    for item in value['narrative_composition']: item.update(proof(source, '旧城睡着了。', None))
    value['rhetoric'][0]['examples'] = ['不在原文里的话']
    with pytest.raises(DataError, match='原文'): store.commit_candidate(run(source, {'style': value}, doc=document['id']), 'chunk1', ['style'], 0)
    assert store.get(document['id'])['revision'] == 0
    assert store.get(document['id'])['commits'] == []


def test_source_provenance_fourth_projection_updates_gap_denominator_old_view_stays_frozen():
    source = {'id': 'v1', 'text': '一段。三段。', 'chapters': [{'number': 1, 'start': 0, 'body_start': 1, 'end': 3, 'heading': '第1章'},
              {'number': 3, 'start': 3, 'body_start': 4, 'end': 6, 'heading': '第3章'}]}
    old = plot([event(progress=50, **proof(source, '一段。'))]); old['schema_version'] = 3
    original = copy.deepcopy(old)
    new = project_sections({'plot': old}, target_version=4, source_versions={'v1': source})
    assert new['plot']['events'][0]['narrative_progress'] == pytest.approx(100/3, abs=.00001)
    assert project_sections({'plot': old}, target_version=3, source_versions={'v1': source})['plot']['events'][0]['narrative_progress'] == 50
    assert old == original


def test_mixed_source_chapter_context_is_not_guessed(tmp_path):
    docs = DocumentStore(tmp_path)
    document = docs.upload('book.txt', '第1章 入城\n林青入城。'.encode())
    source = docs.get_version(document['id'])
    docs.replace(document['id'], source['id'], 'book.txt', '第1章 归来\n林青归来。'.encode())
    second = docs.get_version(document['id'])
    store = ReferenceAnalysisStore(tmp_path, documents=docs)
    value = plot([event('e1', 20, **proof(source, '林青入城。')), event('e2', 30, **proof(second, '林青归来。'))])
    state = store.save_section(document['id'], 'plot', value, 0)
    assert store.project(document['id'], target_version=4)['narrative_context'] == {'chapter_count': 0}
    assert store.get(document['id'])['revision'] == state['revision']


def test_character_initial_point_uses_unique_quote_when_internal_anchor_unknown():
    source = {'id': 'v1', 'text': '甲' * 20 + '林青来了。' + '乙' * 75, 'chapters': []}
    value = person(**{'revealed_at': {'chapter': None, 'offset': None}, 'source_version_id': 'v1', 'evidence': '林青来了。'})
    result = normalize_v4_section('characters', [value], source_versions={'v1': source})
    assert result[0]['initial_state']['narrative_progress'] == 20


def test_mixed_legacy_setting_and_v4_appendix_archive_preserves_original_snapshots(tmp_path):
    store = ReferenceAnalysisStore(tmp_path)
    old = {'schema_version': 3, 'id': 's1', 'title': '旧城', 'category': 'geography', 'content': '旧墙', 'earlier_setting_id': None,
           'narrative_progress': 0, 'story_time': None, 'snapshots': [
               {'id': 's1-change', 'narrative_range': {'start': 20, 'end': None}, 'story_time': None, 'event_ids': [], 'content': '城门封闭'}]}
    new = {'schema_version': 4, 'id': 's2', 'title': '北门', 'category': 'geography', 'content': '北门开启', 'earlier_setting_id': 's1',
           'narrative_progress': 50, 'story_time': None}
    original = store.get('book')
    original['sections']['settings'] = [old, new]
    # Simulate a portable mixed-contract database from an intermediate upgrade.
    with store._connect() as db:
        db.execute('UPDATE reference_analysis SET sections_json=? WHERE document_id=?', (json.dumps(original['sections']), 'book'))
    view = store.project('book', target_version=4)
    saved = store.save_section('book', 'settings', view['sections']['settings'], original['revision'])
    with closing(sqlite3.connect(store.path)) as db:
        archived = json.loads(db.execute('SELECT data_json FROM reference_contract_archives WHERE document_id=? AND section=? AND contract_version=3', ('book', 'settings')).fetchone()[0])
    assert archived == original['sections']['settings']
    assert archived[0]['snapshots'][0]['content'] == '城门封闭'
    assert all('snapshots' not in item for item in saved['sections']['settings'])
    assert len(saved['sections']['settings']) == 3
