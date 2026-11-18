"""Reference UI contracts: reversible chapter operations and independent reviews."""
import json

import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'data', tmp_path / 'dist'), base_url='http://127.0.0.1') as session:
        yield session


def upload(client, source='第一章 雨夜\n江屿拾起红伞。\n作者说：求月票，感谢支持！\n第二章 归途\n许云蝉在桥头等他。\n'):
    response = client.post('/api/studio/documents', files={'file': ('book.txt', source.encode(), 'text/plain')})
    assert response.status_code == 201, response.text
    doc = response.json()
    return doc, f"/api/studio/documents/{doc['id']}"


def checked(response):
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_chapter_inline_cleanup_append_edit_and_undo(client):
    doc, base = upload(client)
    vid = doc['latest_version_id']
    index = checked(client.get(f'{base}/versions/{vid}/chapters'))
    first = index['chapters'][0]
    page = checked(client.get(f"{base}/versions/{vid}/chapters/{first['id']}"))
    assert '江屿拾起红伞' in page['text']
    proposals = [p for p in page['proposals'] if p['kind'] == 'author_note']
    assert proposals
    original = checked(client.get(f'{base}/versions/{vid}'))['text']
    # An explicitly kept proposal leaves the passage in the saved version.
    kept_response = client.post(base+'/chapters/clean', json={'base_version_id':vid,'selections':[{'chapter_id':first['id'],'proposal_ids':[]}]})
    assert kept_response.status_code == 422
    assert checked(client.get(f'{base}/versions/{vid}'))['text'] == original
    kept = {'id':vid}
    removed = checked(client.post(base+'/chapters/clean', json={'base_version_id':kept['id'],'selections':[{'chapter_id':first['id'],'proposal_ids':[p['id'] for p in proposals]}]}))
    assert '求月票' not in removed['text'] and '江屿拾起红伞' in removed['text']
    assert checked(client.get(f'{base}/versions/{vid}'))['text'] == original
    restored = checked(client.post(base+'/chapters/restore',json={'base_version_id':removed['id'],'target_version_id':vid}))
    assert restored['text'] == original
    edited = checked(client.post(base+'/chapters/edit',json={'base_version_id':restored['id'],'chapter_id':first['id'],'body_text':'他撑起红伞。\n'}))
    assert '他撑起红伞' in edited['text'] and '许云蝉在桥头等他' in edited['text']
    tail = '第三章 明日\n桥上春风正暖。\n'.encode()
    appended = checked(client.post(base+'/append',data={'base_version_id':edited['id']},files={'file':('next.txt',tail,'text/plain')}))
    assert appended['text'].startswith(edited['text']) and appended['text'].endswith(tail.decode())
    sources = checked(client.get(base+'/sources'))['items']
    assert len(sources) == 2
    assert client.get(f"{base}/sources/{sources[1]['id']}/download").content == tail
    stale = client.post(base+'/chapters/edit',json={'base_version_id':vid,'chapter_id':first['id'],'body_text':'过期'})
    assert stale.status_code == 409


def test_work_analysis_zero_revision_and_section_ledger(client):
    doc, base = upload(client)
    state = checked(client.get(base+'/reference'))
    assert state['revision'] == 0
    state = checked(client.put(base+'/reference',json={'structure_type':'setting_collection','expected_revision':0}))
    entries = [{'title':'雨城', 'content':'第一行\n## 仍是正文\n设定：保留冒号。'}]
    state = checked(client.put(base+'/reference/raw_entries',json={'data':entries,'expected_revision':state['revision']}))
    assert state['sections']['raw_entries'] == entries
    assert client.put(base+'/reference/raw_entries',json={'data':[],'expected_revision':0}).status_code == 409
    job = checked(client.post('/api/studio/jobs',json={'document_id':doc['id'],'version_id':doc['latest_version_id'],'manual':True,'start':False,'workflow_version':'1.1.0'}))
    chunk = job['candidates'][0]['chunk_id']
    prompt = checked(client.get(f"/api/studio/jobs/{job['id']}/chunks/{chunk}/prompt"))
    assert '江屿' in prompt['prompt']
    native = {'events':[{'name':'拾伞','description':'江屿拾起红伞。','first_chapter':'第一章'}],
              'characters':[{'name':'江屿','intro':'拾伞人','appearance':[{'chapter':'第一章','text':'持红伞'}]}],
              'settings':[{'category':'geography','title':'雨城','content':'桥边的城市'}],
              'style':{'narrative_perspective':'第三人称','avg_sentence_length':8}}
    job = checked(client.post(f"/api/studio/jobs/{job['id']}/chunks/{chunk}/paste",json={'content':json.dumps(native,ensure_ascii=False),'expected_revision':job['revision']}))
    commitbase = f"/api/studio/jobs/{job['id']}/chunks/{chunk}/commit"
    state = checked(client.post(commitbase,json={'sections':['characters'],'expected_revision':state['revision']}))
    assert state['sections']['characters'][0]['name']=='江屿'
    assert state['sections']['settings'] == []
    state = checked(client.post(commitbase,json={'sections':['settings','style','plot'],'expected_revision':state['revision']}))
    assert state['sections']['settings'][0]['title']=='雨城'
    assert len(state['commits']) == 4
    repeat = checked(client.post(commitbase,json={'sections':['characters'],'expected_revision':0}))
    assert repeat['revision']==state['revision'] and len(repeat['sections']['characters'])==1
    assert checked(client.get('/api/studio/usage'))['items'] == []
    preview = checked(client.post(base+'/reference/transform',json={'kind':'translate','payload':{'entry_index':0,'target_language':'英语','prompt_only':True}}))
    assert entries[0]['content'] in preview['prompt']


def test_empty_collection_source_snapshot_and_analysis_history(client):
    document = checked(client.post('/api/studio/reference-collections',json={'title':'雾港设定集'}))
    base = f"/api/studio/documents/{document['id']}"
    assert document['format']=='collection' and document['original_bytes']==0
    state = checked(client.get(base+'/reference'))
    assert state['structure_type']=='setting_collection'
    before=state['revision']
    raw=[{'title':'雾港','content':'@@ 仍是正文\n## 标题亦是正文\n甲：乙\n&amp; &quot;\r\n😀。'}]
    state=checked(client.put(base+'/reference/raw_entries',json={'expected_revision':before,'data':raw}))
    prepared=checked(client.post(base+'/reference/prepare-source',json={'expected_revision':state['revision']}))
    assert raw[0]['content'] in prepared['version']['text']
    firstversion=prepared['version']['id']
    repeated=checked(client.post(base+'/reference/prepare-source',json={'expected_revision':state['revision']}))
    assert repeated['version']['id']==firstversion
    exported=client.get(base+'/reference/export')
    assert exported.status_code==200
    other=checked(client.post('/api/studio/reference-collections',json={'title':'导入校验'}))
    otherbase=f"/api/studio/documents/{other['id']}"
    otherstate=checked(client.get(otherbase+'/reference'))
    imported=checked(client.post(otherbase+'/reference/import',json={'expected_revision':otherstate['revision'],'text':exported.content.decode('utf-8-sig'),'mode':'replace'}))
    assert imported['sections']['raw_entries']==raw
    history=checked(client.get(base+'/reference/history'))['items']
    assert any(row['revision']==before for row in history)
    restored=checked(client.post(base+'/reference/restore',json={'expected_revision':state['revision'],'target_revision':before}))
    assert restored['revision']>state['revision'] and restored['sections']['raw_entries']==[]
    # Old extraction input remains exact after collection edits and undo.
    assert checked(client.get(f'{base}/versions/{firstversion}'))['text']==prepared['version']['text']


def test_volume_api_validation_does_not_overwrite_plan(client):
    document,base=upload(client)
    vid=document['latest_version_id']
    path=f'{base}/versions/{vid}/volumes'
    current=checked(client.get(path))
    saved=checked(client.put(path,json={'expected_revision':current['revision'],'volumes':[{'title':'伞缘','start_chapter':1,'end_chapter':2}]}))
    invalid=client.put(path,json={'expected_revision':saved['revision'],'volumes':[{'title':'遗漏','start_chapter':2,'end_chapter':2}]})
    assert invalid.status_code==422
    assert checked(client.get(path))==saved
    preview=checked(client.post(path+'/paste',json={'content':'{"volumes":[{"title":"新卷","start_chapter":1,"end_chapter":2}]}'}))
    assert preview['volumes'][0]['title']=='新卷'
    assert checked(client.get(path))==saved
    assert checked(client.get(path+'/prompt'))['chapter_count']==2


def test_reference_workspace_backup_restores_sources_reviews_and_plans(client, tmp_path):
    import sqlite3
    document, base = upload(client)
    original = client.get(base+'/original/download').content
    tail = '第三章 春风\n春天到了。\n'.encode()
    version = checked(client.post(base+'/append',data={'base_version_id':document['latest_version_id']},files={'file':('next.txt',tail,'text/plain')}))
    plan = [{'title':'伞缘','start_chapter':1,'end_chapter':3}]
    checked(client.put(f"{base}/versions/{version['id']}/volumes",json={'expected_revision':0,'volumes':plan}))
    state = checked(client.get(base+'/reference'))
    state = checked(client.put(base+'/reference/metadata',json={'expected_revision':state['revision'],'data':{'rating':5,'notes':'保留的笔记\n第二行'}}))
    job = checked(client.post('/api/studio/jobs',json={'document_id':document['id'],'version_id':version['id'],'manual':True,'start':False,'workflow_version':'1.1.0'}))
    chunk = job['candidates'][0]['chunk_id']
    job = checked(client.post(f"/api/studio/jobs/{job['id']}/chunks/{chunk}/paste",json={'expected_revision':job['revision'],'content':json.dumps({'settings':[{'title':'石桥','category':'geography','content':'桥头相会。'}],'style':{'narrative_perspective':'第三人称'}},ensure_ascii=False)}))
    state = checked(client.post(f"/api/studio/jobs/{job['id']}/chunks/{chunk}/commit",json={'expected_revision':state['revision'],'sections':['settings','style']}))
    # Partial/manual jobs can be saved and explicitly paused before a portable snapshot.
    if job['status'] == 'queued': checked(client.post(f"/api/studio/jobs/{job['id']}/cancel",json={}))
    history = checked(client.get(base+'/reference/history'))
    source_list = checked(client.get(base+'/sources'))
    archive = client.get('/api/studio/projects/export')
    assert archive.status_code == 200, archive.text
    restored = checked(client.post('/api/studio/projects/restore',files={'file':('project.zip',archive.content,'application/zip')}))
    target = restored['data_dir']
    with TestClient(create_app(target,tmp_path/'restored-dist'),base_url='http://127.0.0.1') as reopened:
        assert checked(reopened.get(base+'/reference')) == state
        assert checked(reopened.get(base+'/reference/history')) == history
        assert checked(reopened.get(base+'/sources')) == source_list
        assert reopened.get(base+'/original/download').content == original
        assert reopened.get(f"{base}/sources/{source_list['items'][1]['id']}/download").content == tail
        assert checked(reopened.get(f"{base}/versions/{version['id']}"))['text']==version['text']
        assert checked(reopened.get(f"{base}/versions/{version['id']}/volumes"))['volumes']==plan
    from pathlib import Path
    for path in Path(target).glob('*.sqlite3'):
        with sqlite3.connect(path) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchall()==[('ok',)], path.name
