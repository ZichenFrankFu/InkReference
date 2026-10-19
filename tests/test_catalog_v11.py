"""Invented schema6 materials, atomic research and explicit character bridges."""
import copy
import csv
import io
import json
import sqlite3
import zipfile
from pathlib import Path
import pytest
from inkreference.database import DataStore,DataError,SCHEMA5_COLUMNS
from inkreference.catalog_schema_v11 import SCORE_FORMULA
from inkreference.library_exchange import export_library,parse_library_files,preview_library_import,commit_library_import
from inkreference.documents import DocumentStore
from inkreference.reference_analysis import ReferenceAnalysisStore
from inkreference.catalog_links import CatalogLinks
from inkreference.character_bridge_v11 import CharacterBridge


def store(tmp_path):return DataStore(tmp_path/'catalog.sqlite3')
def work(s,id='W1',heat='8',market='4'):
    return s.create_record('works',{'作品ID':id,'作品':'合成作品'+id,'作者':'合成作者','剧情梗概':'梗概','讨论热度':heat,'商业规模':market})
def character(id='CH1',name='林青'):
    return {'schema_version':3,'id':id,'name':name,'aliases':[],'role_tag':'主角','temperament':'利他','visual_design':'青衣','initial_state':{},'snapshots':[]}


def test_schema6_columns_and_weighted_unique_score(tmp_path):
    s=store(tmp_path);a=work(s);b=work(s,'W2','2','2');work(s,'W3','','')
    c=s.create_record('cliches',{'clichéID':'C1','cliché内容':'护送同伴','关联作品':'[{"作品ID":"W1"}]'})
    assert s.meta()['schema_version']==6
    assert c['computed']['cliche_score']==round(600/9,4)
    assert c['computed']['missing_heat_count']==1
    assert c['computed']['formula']==SCORE_FORMULA
    w=s.get_record('works','W1');s.update_record('works','W1',{'关联cliché':'[{"clichéID":"C1"}]'},w['revision'])
    assert s.get_record('cliches','C1')['computed']['matched_work_count']==1
    assert '命中作品数' not in s.get_record('cliches','C1')['catalog_values']
    with pytest.raises(DataError):s.create_record('works',{'作品ID':'BAD','讨论热度':'11'})


def test_heat_ranges_preserve_evidence_and_midpoints(tmp_path):
    s=store(tmp_path);heat=json.dumps({'min':6,'max':10,'sources':['S1'],'rationale':'synthetic'})
    a=work(s,heat=heat,market='2')
    assert a['computed']['heat']==5
    assert a['values']['讨论热度']==heat
    with pytest.raises(DataError):work(s,'BAD','{"min":8,"max":2}','2')


@pytest.mark.parametrize('fmt',['db','csv','xlsx'])
def test_v6_export_semantics_and_context_roundtrip(tmp_path,fmt):
    s=store(tmp_path);work(s)
    s.create_record('characters',{'人物ID':'CH1','角色名':'林青','性格基调':'利他','关联作品':'[{"作品ID":"W1"}]'})
    from inkreference.character_contract import normalize_character
    with s.transaction() as db:db.execute('INSERT INTO character_contexts VALUES(?,?,?,?,?,?)',('CH1','W1',None,'CH1',json.dumps(normalize_character(character())),1))
    data=export_library(s,fmt);batch=parse_library_files([('bundle.'+('zip' if fmt=='csv' else fmt),data)])
    t=DataStore(tmp_path/('target-'+fmt+'.db'));preview=preview_library_import(t,batch)
    assert not preview['errors'],preview
    assert not preview['warnings'],preview
    commit_library_import(t,batch)
    assert t.get_record('characters','CH1')['values']==s.get_record('characters','CH1')['values']
    with t.connect() as db:assert db.execute('SELECT COUNT(*) FROM character_contexts').fetchone()[0]==1
    def context_values(target):
        with target.connect() as db:return [tuple(r) for r in db.execute('SELECT character_id,work_id,document_id,extracted_character_id,character_json FROM character_contexts ORDER BY 1,2')]
    assert [(r[:4],json.loads(r[4])) for r in context_values(t)]==[(r[:4],json.loads(r[4])) for r in context_values(s)]
    second=parse_library_files([('again.'+('zip' if fmt=='csv' else fmt),export_library(t,fmt))]);u=DataStore(tmp_path/('second-'+fmt+'.db'))
    assert not preview_library_import(u,second)['errors'];commit_library_import(u,second)
    assert context_values(u)==context_values(t)



def test_v5_real_headers_digest_checked_before_projection(tmp_path):
    from inkreference.library_exchange import FORMAT,FORMAT_VERSION,_digest
    values={'works':[{c:'' for c in SCHEMA5_COLUMNS['works']}],'characters':[],'cliches':[]}
    values['works'][0].update({'作品ID':'W1','作品':'旧作品','两句话概括':'旧梗概','热度权重':'6','关联作品':'[]','关联角色':'[]','关联cliché':'[]'})
    manifest={'format':FORMAT,'format_version':FORMAT_VERSION,'schema_version':5,'bundle_id':'fixture','tables':{t:{'columns':cols,'rows':len(values[t]),'sha256':_digest(values[t])} for t,cols in SCHEMA5_COLUMNS.items()}}
    files=[]
    for table,cols in SCHEMA5_COLUMNS.items():
        target=io.StringIO(newline='');writer=csv.DictWriter(target,fieldnames=cols);writer.writeheader();writer.writerows(values[table]);files.append((table+'.csv',target.getvalue().encode()))
    files.append(('manifest.json',json.dumps(manifest).encode()))
    b=parse_library_files(files);assert not b.errors,b.errors;assert not b.warnings,b.warnings
    s=store(tmp_path);assert not preview_library_import(s,b)['errors'];commit_library_import(s,b)
    assert s.get_record('works','W1')['values']['剧情梗概']=='旧梗概'
    with s.connect() as db:assert json.loads(db.execute("SELECT values_json FROM legacy_values WHERE record_id='W1'").fetchone()[0])['两句话概括']=='旧梗概'


def research_draft():
    return {'work':{'title':'研究作品','author':'作者','synopsis':'梗概','discussion_heat':{'min':4,'max':6},'market_scale':3},'characters':[{'candidate_id':'hero','name':'林青','aliases':[],'role':'主角','temperament':'利他','visual_design':'青衣'}],'cliches':[{'candidate_id':'beat','content':'护送同伴','character_ids':['hero']}]}


def test_atomic_research_reuses_cliche_id_and_rejects_stale(tmp_path):
    s=store(tmp_path);work(s);s.create_record('cliches',{'clichéID':'C1','cliché内容':'护送同伴'})
    draft=research_draft();decision={'characters':{'hero':{'action':'new'}},'cliches':{'beat':{'action':'reuse','id':'C1'}}}
    revision=s.catalog_snapshot()['revision'];result=s.apply_research(draft,decision,revision,'job1')
    assert s.stats()['cliches']==1
    assert s.get_record('cliches','C1')['computed']['matched_work_count']==1
    cid=result['characters']['hero'];assert [r['id'] for r in s.relations('characters',cid)['cliches']]==['C1']
    assert s.apply_research(draft,decision,revision,'job1')==result
    with pytest.raises(DataError):s.apply_research(draft,decision,revision,'job2')
    before=s.catalog_snapshot();bad=copy.deepcopy(decision);bad['cliches']['beat']={'action':'reuse','id':'missing'}
    with pytest.raises(DataError):s.apply_research(draft,bad,before['revision'],'job3')
    assert s.catalog_snapshot()==before


def fixture_bridge(tmp_path):
    s=store(tmp_path);w=work(s);docs=DocumentStore(tmp_path);reference=ReferenceAnalysisStore(tmp_path,documents=docs)
    link=CatalogLinks(s,docs,lambda:reference);d=docs.upload('a.txt','第1章 入城\n林青走进城。'.encode());link.link(d['id'],expected_work_id=None,expected_version_id=d['latest_version_id'],work_id=w['id'])
    b=CharacterBridge(s,docs,lambda:reference);reference.catalog_sync=b.sync_bound_from_reference_connection
    from inkreference.character_contract import normalize_character
    state=reference.save_section(d['id'],'characters',[normalize_character(character())],0)
    b.import_character(d['id'],'CH1',expected_reference_revision=state['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'])
    return s,docs,reference,b,d


def test_character_bridge_atomic_bidirectional_and_context_cas(tmp_path):
    s,docs,reference,b,d=fixture_bridge(tmp_path);contexts=b.contexts('CH1');context=contexts['contexts'][0]
    changed=copy.deepcopy(context['character']);changed['visual_design']='白衣'
    result=b.save_context('CH1','W1',changed,expected_revision=context['revision'],expected_catalog_revision=contexts['catalog_revision'],expected_reference_revision=context['reference_revision'])
    assert reference.get(d['id'])['sections']['characters'][0]['visual_design']=='白衣'
    assert s.get_record('characters','CH1')['values']['视觉元素设计']=='白衣'
    with pytest.raises(DataError):b.save_context('CH1','W1',changed,expected_revision=context['revision'],expected_catalog_revision=contexts['catalog_revision'],expected_reference_revision=context['reference_revision'])
    state=reference.get(d['id']);characters=copy.deepcopy(state['sections']['characters']);characters[0]['visual_design']='红衣'
    reference.save_section(d['id'],'characters',characters,state['revision'])
    assert b.contexts('CH1')['contexts'][0]['character']['visual_design']=='红衣'
    assert s.get_record('characters','CH1')['values']['视觉元素设计']=='红衣'


def test_character_hook_failure_rolls_back_reference_and_catalog(tmp_path,monkeypatch):
    s,docs,reference,b,d=fixture_bridge(tmp_path);before=reference.get(d['id']);catalog=s.catalog_snapshot()
    def fail(*args):
        b.sync_bound_from_reference_connection(*args)
        raise DataError(409,'synthetic conflict')
    reference.catalog_sync=fail;changed=copy.deepcopy(before['sections']['characters']);changed[0]['name']='另一人'
    with pytest.raises(DataError):reference.save_section(d['id'],'characters',changed,before['revision'])
    assert reference.get(d['id'])==before
    assert s.catalog_snapshot()==catalog


def test_one_character_multiple_work_snapshots_do_not_overwrite(tmp_path):
    s,docs,reference,b,d=fixture_bridge(tmp_path)
    original=b.contexts('CH1')['contexts'][0]['character']
    w=work(s,'W2');links=CatalogLinks(s,docs,lambda:reference)
    second=docs.upload('b.txt','第1章 后日\n林青白发归来。'.encode())
    links.link(second['id'],expected_work_id=None,expected_version_id=second['latest_version_id'],work_id=w['id'])
    from inkreference.character_contract import normalize_character
    other=normalize_character(character('new-extracted-id'));other['initial_state']['appearance']='白发'
    state=reference.save_section(second['id'],'characters',[other],0)
    b.import_character(second['id'],'new-extracted-id',character_id='CH1',expected_reference_revision=state['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'])
    contexts={c['work_id']:c for c in b.contexts('CH1')['contexts']}
    assert contexts['W1']['character']==original
    assert contexts['W2']['character']['initial_state']['appearance']=='白发'
    changed=copy.deepcopy(contexts['W2']['character']);changed['initial_state']['appearance']='银发'
    b.save_context('CH1','W2',changed,expected_revision=contexts['W2']['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'],expected_reference_revision=contexts['W2']['reference_revision'])
    assert b.contexts('CH1')['contexts'][0]['character']==original


def test_reversed_character_cliche_maps_render_one_relationship(tmp_path):
    s=store(tmp_path);work(s);s.create_record('characters',{'人物ID':'CH1','角色名':'林青'})
    s.create_record('cliches',{'clichéID':'C1','cliché内容':'护送同伴','关联角色':'[{"人物ID":"CH1","作品ID":"W1","关联内容":"保护者"}]'})
    a=s.relations('characters','CH1')['cliches'];b=s.relations('cliches','C1')['characters']
    assert len(a)==len(b)==1
    assert len(a[0]['relationships'])==len(b[0]['relationships'])==1
    assert a[0]['relationship']['description']=='保护者'


def test_raw_v8_export_current_bundle_keeps_archive_and_maps(tmp_path):
    from inkreference.csvio import parse_files,preview_import,commit_import,export_legacy_table
    s=store(tmp_path);files=[(t+'.csv',(Path(__file__).parent/'fixtures'/f'{t}.csv').read_bytes()) for t in ('works','characters','cliches')]
    batch=parse_files(files);assert not preview_import(s,batch)['errors'];commit_import(s,batch)
    raw=export_library(s,'db');target=DataStore(tmp_path/'restored.db');imported=parse_library_files([('bundle.db',raw)])
    assert not preview_library_import(target,imported)['errors'];commit_library_import(target,imported)
    assert target.stats()==s.stats()
    for table,original in files:
        t=table.removesuffix('.csv')
        expected=list(csv.DictReader(io.StringIO(original.decode('utf-8-sig'),newline='')))
        restored=list(csv.DictReader(io.StringIO(export_legacy_table(target,t).decode('utf-8-sig'),newline='')))
        assert restored==expected


def test_global_character_identity_edits_sync_bound_reference(tmp_path):
    s,docs,reference,b,d=fixture_bridge(tmp_path);s.character_sync=b.sync_identity_from_catalog_connection
    record=s.get_record('characters','CH1');before=b.contexts('CH1')['contexts'][0]['character']
    s.update_record('characters','CH1',{'角色名':'林澜','别名':'["林青"]'},record['revision'])
    updated=reference.get(d['id'])['sections']['characters'][0]
    assert updated['name']=='林澜' and updated['aliases']==['林青']
    assert updated['initial_state']==before['initial_state']
    assert updated['snapshots']==before['snapshots']


def test_schema5_migration_archives_latest_fields_without_replacing_v8(tmp_path):
    s=store(tmp_path)
    with s.transaction() as db:
        values={c:'' for c in SCHEMA5_COLUMNS['characters']};values.update({'人物ID':'P1','角色':'某人','别名':'[]','后续能力':'最新修改','关联作品':'[]','关联cliché':'[]'})
        db.execute('INSERT INTO characters VALUES(?,NULL,?,1,0)',('P1',json.dumps(values)))
        db.execute('INSERT INTO legacy_values VALUES(?,?,?)',('characters','P1','{"人物ID":"P1","后续能力":"原始旧值"}'))
        db.execute('DELETE FROM schema_migrations WHERE version=6')
    reopened=DataStore(s.db_path)
    assert reopened.get_record('characters','P1')['values']['角色名']=='某人'
    with reopened.connect() as db:
        assert json.loads(db.execute("SELECT values_json FROM legacy_values WHERE record_id='P1'").fetchone()[0])['后续能力']=='原始旧值'
        assert json.loads(db.execute("SELECT values_json FROM catalog_field_archive WHERE record_id='P1' AND source_schema='5'").fetchone()[0])['后续能力']=='最新修改'


def test_score_sort_uses_live_weights_not_saved_derived_cells(tmp_path):
    s=store(tmp_path);work(s,'W1','8','8');work(s,'W2','2','2')
    s.create_record('cliches',{'clichéID':'C1','cliché内容':'低热作品','cliché评分':'99','关联作品':'[{"作品ID":"W2"}]'})
    s.create_record('cliches',{'clichéID':'C2','cliché内容':'高热作品','cliché评分':'1','关联作品':'[{"作品ID":"W1"}]'})
    assert [r['id'] for r in s.list_records('cliches',sort_by='cliché评分',sort_direction='desc')['items']]==['C2','C1']
    assert [r['computed']['cliche_score'] for r in s.list_records('cliches',sort_by='cliché评分',sort_direction='desc')['items']]==[80,20]


def test_legacy_person_projection_can_be_explicitly_bound(tmp_path):
    s=store(tmp_path);w=work(s);docs=DocumentStore(tmp_path);reference=ReferenceAnalysisStore(tmp_path,documents=docs)
    link=CatalogLinks(s,docs,lambda:reference);d=docs.upload('old.txt','旧正文。'.encode());link.link(d['id'],expected_work_id=None,expected_version_id=d['latest_version_id'],work_id=w['id'])
    state=reference.save_section(d['id'],'characters',[{'name':'林青'}],0)
    from inkreference.chronicle_schema import adapt_v2_section
    identity=adapt_v2_section('characters',state['sections']['characters'])[0]['id']
    bridge=CharacterBridge(s,docs,lambda:reference)
    contexts=bridge.import_character(d['id'],identity,expected_reference_revision=state['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'])
    assert contexts['contexts'][0]['character']['id']==identity
    assert reference.get(d['id'])['sections']['characters'][0]['id']==identity


def test_web_reuse_preserves_bound_source_id_and_all_timeline_facts(tmp_path):
    s=store(tmp_path);work(s);docs=DocumentStore(tmp_path);reference=ReferenceAnalysisStore(tmp_path,documents=docs)
    links=CatalogLinks(s,docs,lambda:reference);d=docs.upload('source.txt','第1章 变故\n林青入城。'.encode());links.link(d['id'],expected_work_id=None,expected_version_id=d['latest_version_id'],work_id='W1')
    from inkreference.character_contract import normalize_character
    original=normalize_character(character('extracted-1'));original['initial_state']['identity']='学徒';original['snapshots']=[{'id':'phase1','narrative_range':{'start':20,'end':None},'story_time':'次年','event_ids':[],'identity':'医师','ability':'急救','personality':'沉稳','appearance':'白发','visual_design':'白衣','relationships':[],'origin':'manual'}]
    state=reference.save_section(d['id'],'characters',[original],0);b=CharacterBridge(s,docs,lambda:reference);reference.catalog_sync=b.sync_bound_from_reference_connection;s.character_sync=b.sync_identity_from_catalog_connection
    b.import_character(d['id'],'extracted-1',character_id='global-1',expected_reference_revision=state['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'])
    before=b.contexts('global-1')['contexts'][0];source_before=reference.get(d['id'])
    draft={'work':{'title':'合成作品W1','author':'合成作者','synopsis':'网络核实梗概'},'characters':[{'candidate_id':'hero','name':'林青','aliases':[],'role':'','temperament':'','visual_design':'','initial_state':{},'states':[]}],'cliches':[]}
    s.apply_research(draft,{'work_id':'W1','characters':{'hero':{'action':'reuse','id':'global-1'}},'cliches':{}},s.catalog_snapshot()['revision'],'web-update')
    after=b.contexts('global-1')['contexts'][0]
    assert after==before
    assert after['character']['id']=='extracted-1'
    assert reference.get(d['id'])==source_before
    assert docs.get_version(d['id'])['text']=='第1章 变故\n林青入城。'


def test_research_duplicate_name_or_alias_in_same_work_requires_reuse(tmp_path):
    s=store(tmp_path);work(s);s.create_record('characters',{'人物ID':'existing','角色名':'林青','别名':'["小青"]','关联作品':'[{"作品ID":"W1"}]'})
    draft={'work':{'title':'合成作品W1','author':'合成作者','synopsis':'不应写入'},'characters':[{'candidate_id':'hero','name':'小 青','aliases':[],'temperament':''}],'cliches':[]}
    before=s.catalog_snapshot();decisions={'work_id':'W1','characters':{'hero':{'action':'new'}},'cliches':{}}
    with pytest.raises(DataError,match='复用'):s.apply_research(draft,decisions,before['revision'],'duplicate')
    assert s.catalog_snapshot()==before
    draft['work']={'title':'另一作品','author':'另一作者'};decisions.pop('work_id')
    result=s.apply_research(draft,decisions,before['revision'],'different-work')
    assert result['characters']['hero']!='existing'


def test_shared_identity_changes_propagate_without_cross_work_state_changes(tmp_path):
    s,docs,reference,b,d=fixture_bridge(tmp_path);s.character_sync=b.sync_identity_from_catalog_connection
    w=work(s,'W2');links=CatalogLinks(s,docs,lambda:reference);second=docs.upload('b.txt','第1章 后日\n林青白发归来。'.encode());links.link(second['id'],expected_work_id=None,expected_version_id=second['latest_version_id'],work_id=w['id'])
    from inkreference.character_contract import normalize_character
    other=normalize_character(character('second-source-id'));other['initial_state']['appearance']='白发';state=reference.save_section(second['id'],'characters',[other],0)
    b.import_character(second['id'],'second-source-id',character_id='CH1',expected_reference_revision=state['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'])
    original_first=reference.get(d['id'])['sections']['characters'][0]['initial_state']
    first=reference.get(d['id']);changed=copy.deepcopy(first['sections']['characters']);changed[0]['name']='林澜'
    reference.save_section(d['id'],'characters',changed,first['revision'])
    assert reference.get(second['id'])['sections']['characters'][0]['name']=='林澜'
    assert reference.get(second['id'])['sections']['characters'][0]['initial_state']['appearance']=='白发'
    contexts=b.contexts('CH1');phase=next(c for c in contexts['contexts'] if c['work_id']=='W2');edited=copy.deepcopy(phase['character']);edited['visual_design']='银衣'
    b.save_context('CH1','W2',edited,expected_revision=phase['revision'],expected_catalog_revision=contexts['catalog_revision'],expected_reference_revision=phase['reference_revision'])
    assert reference.get(d['id'])['sections']['characters'][0]['visual_design']=='银衣'
    assert reference.get(d['id'])['sections']['characters'][0]['initial_state']==original_first


def test_portable_bound_context_is_editable_offline_and_keeps_exact_source_ids(tmp_path):
    source_root=tmp_path/'source';source_root.mkdir();s,docs,reference,b,d=fixture_bridge(source_root)
    original=b.contexts('CH1')['contexts'][0]
    target_root=tmp_path/'target';target_root.mkdir();target=DataStore(target_root/'catalog.sqlite3');target_docs=DocumentStore(target_root);target_reference=ReferenceAnalysisStore(target_root,documents=target_docs);bridge=CharacterBridge(target,target_docs,lambda:target_reference);target.character_sync=bridge.sync_identity_from_catalog_connection
    raw=export_library(s,'db');batch=parse_library_files([('bundle.db',raw)]);assert not preview_library_import(target,batch)['errors'];commit_library_import(target,batch)
    offline=bridge.contexts('CH1');context=offline['contexts'][0]
    assert context['missing_source'] and not context['source_available']
    assert context['document_id']==d['id'] and context['extracted_character_id']=='CH1'
    with target_reference._connect() as db:assert db.execute('SELECT COUNT(*) FROM reference_analysis').fetchone()[0]==0
    edited=copy.deepcopy(context['character']);edited['initial_state']['identity']='人工校正身份'
    result=bridge.save_context('CH1','W1',edited,expected_revision=context['revision'],expected_catalog_revision=offline['catalog_revision'])
    assert result['contexts'][0]['character']['initial_state']['identity']=='人工校正身份'
    assert result['contexts'][0]['document_id']==original['document_id']
    second_root=tmp_path/'second';second_root.mkdir();second=DataStore(second_root/'catalog.sqlite3');data=export_library(target,'db');batch=parse_library_files([('second.db',data)]);assert not preview_library_import(second,batch)['errors'];commit_library_import(second,batch)
    with second.connect() as db:
        saved=db.execute('SELECT document_id,extracted_character_id,character_json FROM character_contexts').fetchone()
    assert saved[0]==d['id'] and saved[1]=='CH1' and json.loads(saved[2])==result['contexts'][0]['character']
    before=s.catalog_snapshot();same=parse_library_files([('same.db',raw)]);assert not preview_library_import(s,same)['errors'];commit_library_import(s,same,'update')
    current=b.contexts('CH1')['contexts'][0]
    assert current['source_available'] and not current['missing_source'] and current['document_id']==original['document_id']
    assert current['revision']==original['revision']
    changed=copy.deepcopy(current['character']);changed['initial_state']['identity']='仍然同步'
    b.save_context('CH1','W1',changed,expected_revision=current['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'],expected_reference_revision=current['reference_revision'])
    assert reference.get(d['id'])['sections']['characters'][0]['initial_state']['identity']=='仍然同步'


def test_context_source_work_or_identity_conflict_never_silently_becomes_offline(tmp_path):
    s,docs,reference,b,d=fixture_bridge(tmp_path);work(s,'W2')
    with docs._connection() as db:db.execute("UPDATE doc_documents SET work_id='W2' WHERE id=?",(d['id'],))
    with pytest.raises(DataError,match='其他作品'):b.contexts('CH1')
    with docs._connection() as db:db.execute("UPDATE doc_documents SET work_id='W1' WHERE id=?",(d['id'],))
    with reference._connect() as db:db.execute('UPDATE reference_analysis SET sections_json=? WHERE document_id=?',(json.dumps({'characters':[]}),d['id']))
    with pytest.raises(DataError,match='提取 ID'):b.contexts('CH1')


def test_portable_offline_row_cannot_clear_or_replace_existing_binding(tmp_path):
    s,docs,reference,b,d=fixture_bridge(tmp_path);raw=export_library(s,'db');before=b.contexts('CH1')['contexts'][0]
    batch=parse_library_files([('offline.db',raw)]);batch.relation_tables['character_contexts'][0]['document_id']=''
    assert not preview_library_import(s,batch)['errors'];commit_library_import(s,batch,'update')
    current=b.contexts('CH1')['contexts'][0]
    assert current['document_id']==d['id'] and current['revision']==before['revision']
    changed=parse_library_files([('other.db',raw)]);changed.relation_tables['character_contexts'][0]['document_id']='different-source'
    snapshot=s.catalog_snapshot();assert preview_library_import(s,changed)['errors']
    with pytest.raises(DataError):commit_library_import(s,changed,'update')
    assert s.catalog_snapshot()==snapshot


def stable_v2_bridge(tmp_path):
    s=store(tmp_path);work(s);docs=DocumentStore(tmp_path);reference=ReferenceAnalysisStore(tmp_path,documents=docs);link=CatalogLinks(s,docs,lambda:reference)
    d=docs.upload('v2.txt','第1章 入城\n林青入城。'.encode());link.link(d['id'],expected_work_id=None,expected_version_id=d['latest_version_id'],work_id='W1')
    text=docs.get_version(d['id'])['text'];proof={'revealed_at':{'chapter':1,'offset':text.index('林青入城。')},'source_version_id':d['latest_version_id'],'evidence':'林青入城。'}
    old={'schema_version':2,'id':'stable-v2-person','name':'林青','intro':'旧版人物简介应在历史保留','temperament':'利他','visual_design':'青衣','states':[{'id':'old-identity','attribute':'identity','value':'学徒','change_type':'addition','supersedes':[],'event_id':None,'setting_ids':[],'character_ids':[],'story_time':{'label':'次日','sort_key':'2'},**proof}],'experiences':[],**proof}
    saved=reference.save_section(d['id'],'characters',[old],0)
    bridge=CharacterBridge(s,docs,lambda:reference);reference.catalog_sync=bridge.sync_bound_from_reference_connection
    imported=bridge.import_character(d['id'],'stable-v2-person',expected_reference_revision=saved['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'])
    return s,docs,reference,bridge,d,old,saved,imported


def test_binding_stable_id_schema2_migrates_atomically_and_keeps_legacy_history(tmp_path):
    s,docs,reference,bridge,d,old,saved,imported=stable_v2_bridge(tmp_path)
    current=reference.get(d['id']);person=current['sections']['characters'][0]
    assert person['schema_version']==3 and person['id']==old['id']
    assert not {'intro','states','experiences'} & person.keys()
    assert person['snapshots'][0]['id']=='old-identity' and person['snapshots'][0]['identity']=='学徒'
    assert person['evidence']==old['evidence'] and person['source_version_id']==old['source_version_id']
    assert current['revision']==saved['revision']+1
    with reference._connect() as db:
        previous=json.loads(db.execute('SELECT sections_json FROM reference_analysis_history WHERE document_id=? AND revision=?',(d['id'],saved['revision'])).fetchone()[0])
    assert previous['characters']==[old]
    context=imported['contexts'][0];changed=copy.deepcopy(context['character']);changed['visual_design']='白衣'
    bridge.save_context(old['id'],'W1',changed,expected_revision=context['revision'],expected_catalog_revision=imported['catalog_revision'],expected_reference_revision=context['reference_revision'])
    assert reference.get(d['id'])['sections']['characters'][0]['visual_design']=='白衣'


def test_existing_binding_can_save_after_restoring_schema2_history(tmp_path):
    s,docs,reference,bridge,d,old,saved,imported=stable_v2_bridge(tmp_path)
    restored=reference.restore(d['id'],saved['revision'],reference.get(d['id'])['revision'])
    assert restored['sections']['characters'][0]['schema_version']==2
    contexts=bridge.contexts(old['id']);context=contexts['contexts'][0];changed=copy.deepcopy(context['character']);changed['visual_design']='恢复后新衣'
    bridge.save_context(old['id'],'W1',changed,expected_revision=context['revision'],expected_catalog_revision=contexts['catalog_revision'],expected_reference_revision=context['reference_revision'])
    final=reference.get(d['id'])['sections']['characters'][0]
    assert final['schema_version']==3 and final['visual_design']=='恢复后新衣'
    assert not {'intro','states','experiences'} & final.keys()
    assert final['evidence']==old['evidence'] and final['snapshots'][0]['evidence']==old['states'][0]['evidence']


def test_shared_write_lock_serializes_catalog_and_reference_before_sqlite_locks(tmp_path,monkeypatch):
    import threading
    from inkreference.api import create_app
    from inkreference.character_contract import normalize_character
    runtime=create_app(tmp_path/'runtime',tmp_path/'no-dist').state.runtime
    s=runtime.store;work(s);docs=runtime.studio.documents;reference=runtime.studio.reference;bridge=runtime.studio.character_bridge
    assert s.write_lock is reference.write_lock is runtime.lock is runtime.studio.lock
    d=docs.upload('concurrent.txt','第1章 入城\n林青入城。'.encode(),work_id='W1')
    state=reference.save_section(d['id'],'characters',[normalize_character(character())],0)
    bridge.import_character(d['id'],'CH1',expected_reference_revision=state['revision'],expected_catalog_revision=s.catalog_snapshot()['revision'])
    catalog_entered=threading.Event();release_catalog=threading.Event();reference_started=threading.Event();reference_entered=threading.Event();errors=[]
    original_update=s._update_raw;original_write=reference._write
    def held_update(db,table,identity,values,revision):
        if table=='characters' and identity=='CH1':
            catalog_entered.set()
            if not release_catalog.wait(3):raise AssertionError('catalog test gate timed out')
        return original_update(db,table,identity,values,revision)
    def tracked_write(*args):reference_entered.set();return original_write(*args)
    monkeypatch.setattr(s,'_update_raw',held_update);monkeypatch.setattr(reference,'_write',tracked_write)
    record=s.get_record('characters','CH1')
    def catalog_edit():
        try:s.update_record('characters','CH1',{'角色名':'林澜'},record['revision'])
        except BaseException as exc:errors.append(exc)
    def reference_edit():
        try:
            reference_started.set();state=reference.get(d['id']);people=copy.deepcopy(state['sections']['characters']);people[0]['visual_design']='银衣';reference.save_section(d['id'],'characters',people,state['revision'])
        except BaseException as exc:errors.append(exc)
    first=threading.Thread(target=catalog_edit,daemon=True);second=threading.Thread(target=reference_edit,daemon=True)
    first.start();assert catalog_entered.wait(2);second.start();assert reference_started.wait(2)
    try:assert not reference_entered.wait(.05)
    finally:release_catalog.set()
    first.join(3);second.join(3)
    assert not first.is_alive() and not second.is_alive(), 'cross-database writers must not deadlock'
    assert not errors,errors
    person=reference.get(d['id'])['sections']['characters'][0]
    assert person['name']=='林澜' and person['visual_design']=='银衣'
    assert s.get_record('characters','CH1')['values']['视觉元素设计']=='银衣'
