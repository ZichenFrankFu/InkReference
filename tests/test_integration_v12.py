"""Actual HTTP, SQLite bridge and three-format v4 character interoperability.

All names and source text are invented. No external model or search is invoked.
"""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.database import DataStore
from inkreference.library_exchange import export_library, parse_library_files, preview_library_import, commit_library_import


def phase(name='入城之后', start=33.333333):
    return {'id': 'phase-1', 'name': name, 'narrative_range': {'start': start, 'end': None}, 'story_time': None,
            'event_ids': ['event-1'], 'identity': '守门人', 'ability': '辨认道路', 'personality': '',
            'appearance': '', 'visual_design': '', 'relationships': []}


def person(identity='source-person', snapshots=None):
    return {'schema_version': 4, 'id': identity, 'name': '林青', 'aliases': ['阿青'], 'role_tag': '主角',
            'temperament': '利他', 'visual_design': '灰衣', 'initial_state': {'gender': '男', 'age': '20',
            'identity': '旅人', 'ability': '', 'personality': '', 'appearance': '黑发', 'relationships': [],
            'narrative_progress': 0, 'story_time': None}, 'snapshots': snapshots if snapshots is not None else [phase()]}


def event(progress=33.333333):
    return {'id': 'event-1', 'name': '抵达旧城', 'theme': '寻找归途', 'description': '林青抵达旧城。',
            'narrative_progress': progress, 'story_time': None, 'participants': ['source-person'], 'setting_ids': [],
            'causes': [], 'results': [], 'plotline_ids': [], 'volume': None}


def catalog_values(character):
    return {'人物ID': character['id'], '角色名': character['name'], '别名': json.dumps(character['aliases']),
            '角色定位': character['role_tag'], '性格基调': character['temperament'], '视觉元素设计': character['visual_design'],
            '初始状态': json.dumps(character['initial_state'], ensure_ascii=False),
            '阶段状态': json.dumps(character['snapshots'], ensure_ascii=False)}


def bind_source(client, *, proof=True):
    runtime = client.app.state.runtime
    runtime.store.create_record('works', {'作品ID': 'W1', '作品': '合成归途', '作者': '合成作者', '剧情梗概': '旅人寻找归途。'})
    document = runtime.studio.documents.upload('合成归途.txt', '第1章 入城\n林青抵达旧城。\n第3章 离城\n林青离开旧城。'.encode())
    prefix = f"/api/studio/documents/{document['id']}"
    linked = client.put(prefix + '/catalog', json={'expected_work_id': None, 'expected_version_id': document['latest_version_id'], 'work_id': 'W1'})
    assert linked.status_code == 200, linked.text
    source = runtime.studio.documents.get_version(document['id'])
    e, c = event(), person()
    if proof:
        citation = {'revealed_at': {'chapter': 1, 'offset': source['text'].index('林青抵达旧城。')}, 'source_version_id': source['id'], 'evidence': '林青抵达旧城。'}
        e.update(citation); c.update(citation); c['snapshots'][0].update(citation)
    saved = client.put(prefix + '/reference/characters', json={'data': [c], 'expected_revision': 0})
    assert saved.status_code == 422  # Event link cannot precede the event itself.
    plot = {'schema_version': 4, 'logline': '旅人在旧城寻找归途。', 'events': [{**e, 'participants': []}], 'plotlines': []}
    saved = client.put(prefix + '/reference/plot', json={'data': plot, 'expected_revision': 0})
    assert saved.status_code == 200, saved.text
    saved = client.put(prefix + '/reference/characters', json={'data': [c], 'expected_revision': saved.json()['revision']})
    assert saved.status_code == 200, saved.text
    links = client.get(prefix + '/reference/characters/catalog').json()
    bound = client.post(prefix + '/reference/characters/source-person/catalog', json={'expected_reference_revision': saved.json()['revision'], 'expected_catalog_revision': links['catalog_revision']})
    assert bound.status_code == 200, bound.text
    return runtime, document, source, prefix, bound.json()


@pytest.mark.parametrize('format', ['db', 'csv', 'xlsx'])
def test_named_character_manual_catalog_crud_and_three_format_roundtrip(tmp_path, format):
    store = DataStore(tmp_path / 'source.db')
    character = person('global-person')
    character['snapshots'][0]['event_ids'] = []  # Standalone catalog phases do not claim source event IDs.
    row = store.create_record('characters', catalog_values(character))
    assert json.loads(row['catalog_values']['阶段状态'])[0]['name'] == '入城之后'
    changed = copy.deepcopy(character); changed['snapshots'][0]['name'] = '守城时期'
    row = store.update_record('characters', row['id'], {'阶段状态': json.dumps(changed['snapshots'], ensure_ascii=False)}, row['revision'])
    raw = export_library(store, format)
    target = DataStore(tmp_path / ('target-' + format + '.db'))
    extension = 'zip' if format == 'csv' else format
    batch = parse_library_files([('bundle.' + extension, raw)])
    assert not batch.errors, batch.errors
    assert not preview_library_import(target, batch)['errors']
    commit_library_import(target, batch)
    restored = target.get_record('characters', 'global-person')
    assert json.loads(restored['catalog_values']['阶段状态']) == json.loads(row['catalog_values']['阶段状态'])
    assert json.loads(restored['catalog_values']['初始状态']) == json.loads(row['catalog_values']['初始状态'])
    assert json.loads(restored['catalog_values']['阶段状态'])[0]['name'] == '守城时期'


def test_actual_http_character_v4_phase_name_and_history_sync_are_lossless(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        runtime, doc, source, prefix, bound = bind_source(client)
        context = bound['contexts'][0]
        assert context['character']['schema_version'] == 4
        assert context['character']['snapshots'][0]['name'] == '入城之后'
        changed = copy.deepcopy(context['character']); changed['snapshots'][0]['name'] = '守城时期'
        response = client.put('/api/library/characters/source-person/contexts/W1', json={'character': changed,
            'expected_revision': context['revision'], 'expected_reference_revision': context['reference_revision'],
            'expected_catalog_revision': bound['catalog_revision']})
        assert response.status_code == 200, response.text
        state = runtime.studio.reference.get(doc['id'])
        assert state['sections']['characters'][0]['snapshots'][0]['name'] == '守城时期'
        restored = runtime.studio.reference.restore(doc['id'], context['reference_revision'], state['revision'])
        rebound = runtime.studio.character_bridge.contexts('source-person')['contexts'][0]
        assert rebound['character']['snapshots'][0]['name'] == '入城之后'
        assert rebound['character']['schema_version'] == 4
        assert rebound['character']['evidence'] == '林青抵达旧城。'
        assert rebound['reference_revision'] == restored['revision']


def test_bound_character_context_uses_analyzed_source_not_later_uploaded_body(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        runtime, doc, source, prefix, bound = bind_source(client)
        assert bound['contexts'][0]['narrative_context']['chapter_count'] == 3
        runtime.studio.documents.append(doc['id'], source['id'], '补充.txt', '第5章 归来\n林青归来。'.encode())
        context = client.get('/api/library/characters/source-person/contexts').json()['contexts'][0]
        projected = client.get(prefix + '/reference/projection?schema_version=4').json()
        assert projected['narrative_context']['chapter_count'] == 3
        assert context['narrative_context'] == projected['narrative_context']
        assert context['events'][0]['narrative_progress'] == projected['sections']['plot']['events'][0]['narrative_progress']


def test_bound_character_context_mixed_source_has_no_false_chapter_denominator(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        runtime, doc, source, prefix, bound = bind_source(client)
        runtime.studio.documents.append(doc['id'], source['id'], '补充.txt', '第5章 归来\n林青归来。'.encode())
        latest = runtime.studio.documents.get_version(doc['id'])
        state = runtime.studio.reference.get(doc['id'])
        plot = copy.deepcopy(state['sections']['plot'])
        second = event(100); second.update(id='event-2', name='归来', participants=[],
            revealed_at={'chapter': 5, 'offset': latest['text'].index('林青归来。')}, source_version_id=latest['id'], evidence='林青归来。')
        plot['events'].append(second)
        saved = client.put(prefix + '/reference/plot', json={'data': plot, 'expected_revision': state['revision']})
        assert saved.status_code == 200, saved.text
        context = client.get('/api/library/characters/source-person/contexts').json()['contexts'][0]
        projected = client.get(prefix + '/reference/projection?schema_version=4').json()
        assert projected['narrative_context'] == {'chapter_count': 0}
        assert context['narrative_context'] == projected['narrative_context']


def test_default_http_workflow_and_preview_are_v4_and_controls_do_not_call_model(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        runtime = app.state.runtime
        doc = runtime.studio.documents.upload('合成.txt', '第1章 开始\n林青抵达旧城。'.encode())
        response = client.post('/api/studio/jobs', json={'document_id': doc['id'], 'version_id': doc['latest_version_id'], 'manual': True, 'start': False})
        assert response.status_code in (200, 201), response.text
        job = response.json()
        assert job['workflow_version'] == '4.0.0' and job['snapshots']['contract_version'] == 4
        paused = client.post(f"/api/studio/jobs/{job['id']}/pause")
        assert paused.status_code == 200, paused.text
        stopped = client.post(f"/api/studio/jobs/{job['id']}/terminate")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()['status'] == 'terminated'
        assert client.post(f"/api/studio/jobs/{job['id']}/resume").status_code == 409
        before = runtime.studio.reference.get(doc['id'])
        old = {'schema_version': 3, 'logline': '', 'events': [], 'plotlines': []}
        preview = client.post(f"/api/studio/documents/{doc['id']}/reference/preview", json={'sections': {'plot': old}})
        assert preview.status_code == 200, preview.text
        assert preview.json()['sections']['plot']['schema_version'] == 4
        assert runtime.studio.reference.get(doc['id']) == before


@pytest.mark.parametrize('format', ['db', 'csv', 'xlsx'])
def test_bound_v4_context_export_is_portable_offline_without_fabricating_source(tmp_path, format):
    from inkreference.documents import DocumentStore
    from inkreference.character_bridge_v11 import CharacterBridge
    from inkreference.reference_analysis import ReferenceAnalysisStore
    app = create_app(tmp_path / 'source')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        runtime, document, source, prefix, bound = bind_source(client)
        original = bound['contexts'][0]
        raw = export_library(runtime.store, format)
        destination = tmp_path / 'restored'
        target = DataStore(destination / 'catalog.sqlite3')
        batch = parse_library_files([('bundle.' + ('zip' if format == 'csv' else format), raw)])
        assert not batch.errors, batch.errors
        assert not preview_library_import(target, batch)['errors']
        commit_library_import(target, batch)
        docs = DocumentStore(destination)
        reference = ReferenceAnalysisStore(destination, documents=docs)
        bridge = CharacterBridge(target, docs, lambda: reference)
        contexts = bridge.contexts('source-person')
        restored = contexts['contexts'][0]
        assert restored['character'] == original['character']
        assert restored['document_id'] == document['id']
        assert restored['extracted_character_id'] == 'source-person'
        assert restored['character']['schema_version'] == 4
        assert restored['character']['snapshots'][0]['name'] == '入城之后'
        assert not restored['source_available'] and restored['missing_source']
        assert restored['narrative_context'] == {'chapter_count': 0}
        assert docs.list_documents() == []
        changed = copy.deepcopy(restored['character']); changed['snapshots'][0]['name'] = '离线改名'
        saved = bridge.save_context('source-person', 'W1', changed,
            expected_revision=restored['revision'], expected_catalog_revision=contexts['catalog_revision'])
        after = saved['contexts'][0]['character']
        assert after['snapshots'][0]['name'] == '离线改名'
        assert after['evidence'] == original['character']['evidence']
        assert after['snapshots'][0]['evidence'] == original['character']['snapshots'][0]['evidence']
        assert docs.list_documents() == []
