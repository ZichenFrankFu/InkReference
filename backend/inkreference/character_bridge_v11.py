"""Explicit, revision checked character links between catalog and source analysis.

One work owns its snapshots. Reusing a character ID in another work never replaces
that work's states. ATTACH keeps catalog, binding and reference history atomic.
"""
from __future__ import annotations
import copy
import json
import re
from typing import Any
from uuid import uuid4
from .database import DataError
from .catalog_schema_v11 import dumps, project_values

class _ReferenceConnection:
    """Route the store's fixed internal reference tables to the attached database."""
    def __init__(self,connection):self.connection=connection
    def execute(self,sql,parameters=()):
        sql=re.sub(r'\b(reference_analysis_history|reference_analysis|reference_section_commits|reference_section_overrides)\b',r'texts.\1',sql)
        return self.connection.execute(sql,parameters)

class _DirectReferenceConnection(_ReferenceConnection):
    def execute(self,sql,parameters=()):return self.connection.execute(sql,parameters)

class CharacterBridge:
    def __init__(self,store,documents,reference):
        self.store,self.documents,self.reference=store,documents,reference

    @staticmethod
    def _values(character:dict,identifier:str)->dict[str,str]:
        return project_values('characters',{'人物ID':identifier,'角色名':character['name'],'别名':dumps(character['aliases']),'角色定位':character['role_tag'],'性格基调':character['temperament'],'视觉元素设计':character['visual_design'],'初始状态':dumps(character['initial_state']),'阶段状态':dumps(character['snapshots'])})

    def contexts(self,character_id:str)->dict[str,Any]:
        self.store.get_record('characters',character_id)
        with self.store.connect() as db:
            db.execute('BEGIN')
            rows=[dict(r) for r in db.execute('SELECT * FROM character_contexts WHERE character_id=? ORDER BY work_id',(character_id,))]
            revision=self.store._catalog_revision(db)
        result=[]
        for row in rows:
            character=json.loads(row.pop('character_json'))
            item={**row,'character':character,'reference_revision':None,'characters':[],'events':[],'source_available':False,'missing_source':False}
            if item['document_id']:
                with self.documents._connection() as source_db:source=source_db.execute('SELECT work_id FROM doc_documents WHERE id=?',(item['document_id'],)).fetchone()
                if source is None:item['missing_source']=True
                else:
                    if source[0]!=item['work_id']:raise DataError(409,'人物的原始文本已关联其他作品，请重新绑定人物来源')
                    reference_store=self.reference()
                    with reference_store._connect() as source_db:
                        if source_db.execute('SELECT 1 FROM reference_analysis WHERE document_id=?',(item['document_id'],)).fetchone() is None:raise DataError(409,'人物的原始分析不存在，请重新绑定人物来源')
                        reference=reference_store._get(source_db,item['document_id'])
                    if not any(c.get('id')==item['extracted_character_id'] for c in reference['sections'].get('characters',[]) if isinstance(c,dict)):raise DataError(409,'人物的原始提取 ID 已变化，请重新绑定人物来源')
                    item['source_available']=True;item['reference_revision']=reference['revision']
                    item['characters']=[{'id':c.get('id'),'name':c.get('name','')} for c in reference['sections'].get('characters',[]) if isinstance(c,dict)]
                    item['events']=[{'id':e.get('id'),'name':e.get('name','')} for e in reference['sections'].get('plot',{}).get('events',[]) if isinstance(e,dict)]
            result.append(item)
        return {'character_id':character_id,'catalog_revision':revision,'contexts':result}

    def import_character(self,document_id:str,extracted_character_id:str,*,character_id:str|None=None,expected_reference_revision:int,expected_catalog_revision:str)->dict[str,Any]:
        from .character_contract import normalize_character
        explicit_character_id=character_id is not None
        reference=self.reference();reference.get(document_id)
        with self.store.transaction() as db:
            db.execute('ATTACH DATABASE ? AS texts',(str(self.documents.db_path),))
            proxy=_ReferenceConnection(db)
            state=reference._get(proxy,document_id);reference._revision(state,expected_reference_revision)
            if self.store._catalog_revision(db)!=expected_catalog_revision:raise DataError(409,'素材库已变化，请刷新后同步')
            document=db.execute('SELECT work_id FROM texts.doc_documents WHERE id=?',(document_id,)).fetchone()
            if not document or not document[0]:raise DataError(409,'请先将文本关联到作品')
            work_id=document[0];self.store._row(db,'works',work_id)
            character=next((c for c in state['sections'].get('characters',[]) if c.get('id')==extracted_character_id),None)
            if character is None or character.get("schema_version") != 3:
                from .chronicle_schema import adapt_v2_section
                adapted=adapt_v2_section('characters',state['sections'].get('characters',[]))
                character=next((c for c in adapted if c.get('id')==extracted_character_id),None)
                if character is not None:
                    from .reference_analysis import validate_section
                    state['sections']['characters']=validate_section('characters',[normalize_character(c) for c in adapted])
                    reference._write(proxy,document_id,state['sections'],state['structure_type'])
            if character is None:raise DataError(404,'提取人物不存在')
            canonical=normalize_character(character)
            bound=db.execute('SELECT character_id FROM character_contexts WHERE work_id=? AND extracted_character_id=?',(work_id,extracted_character_id)).fetchone()
            if bound and character_id and bound[0]!=character_id:raise DataError(409,'此提取人物已绑定其他人物 ID')
            character_id=character_id or (bound[0] if bound else extracted_character_id)
            existing=db.execute('SELECT * FROM characters WHERE id=?',(character_id,)).fetchone()
            if existing and not bound and not explicit_character_id:
                raise DataError(409,'人物 ID 已存在，请显式选择复用人物')
            if existing is None:self.store._insert_raw(db,'characters',self._values(canonical,character_id))
            current=db.execute('SELECT * FROM character_contexts WHERE character_id=? AND work_id=?',(character_id,work_id)).fetchone()
            if current and current['extracted_character_id']!=extracted_character_id:raise DataError(409,'当前作品的此人物已经绑定另一个提取 ID')
            revision=current['revision']+1 if current else 1
            db.execute('INSERT INTO character_contexts VALUES(?,?,?,?,?,?) ON CONFLICT(character_id,work_id) DO UPDATE SET document_id=excluded.document_id,character_json=excluded.character_json,revision=excluded.revision',(character_id,work_id,document_id,extracted_character_id,dumps(canonical),revision))
            self.store._append_catalog_link(db,'characters',character_id,'关联作品',{'作品ID':work_id})
            self.store._rebuild_library_links(db)
        return self.contexts(character_id)

    def save_context(self,character_id:str,work_id:str,character:dict,*,expected_revision:int,expected_catalog_revision:str,expected_reference_revision:int|None=None)->dict[str,Any]:
        from .character_contract import normalize_character
        canonical=normalize_character(character)
        with self.store.transaction() as db:
            if self.store._catalog_revision(db)!=expected_catalog_revision:raise DataError(409,'素材库已变化，请刷新后保存')
            row=db.execute('SELECT * FROM character_contexts WHERE character_id=? AND work_id=?',(character_id,work_id)).fetchone()
            if row is None:raise DataError(404,'人物作品状态不存在，请先绑定提取人物')
            if row['revision']!=expected_revision:raise DataError(409,'人物状态已变化，请刷新后保存')
            if canonical['id']!=row['extracted_character_id']:raise DataError(422,'不能修改已绑定的提取人物 ID')
            source=None
            if row['document_id']:
                db.execute('ATTACH DATABASE ? AS texts',(str(self.documents.db_path),))
                source=db.execute('SELECT work_id FROM texts.doc_documents WHERE id=?',(row['document_id'],)).fetchone()
                if source is not None and source[0]!=work_id:raise DataError(409,'人物的原始文本已关联其他作品，请重新绑定人物来源')
            if source is None:
                previous=json.loads(row['character_json']);previous_states={s.get('id'):s for s in previous.get('snapshots',[])}
                for fact,old in [(canonical,previous),*[(s,previous_states.get(s.get('id'),{})) for s in canonical.get('snapshots',[])]]:
                    if fact.get('evidence') and not old.get('evidence'):raise DataError(422,'离线人物状态不能新增原文证据')
                    for field in ('revealed_at','source_version_id','evidence'):
                        if field in old:fact[field]=copy.deepcopy(old[field])
            else:
                reference=self.reference();proxy=_ReferenceConnection(db)
                if proxy.execute('SELECT 1 FROM reference_analysis WHERE document_id=?',(row['document_id'],)).fetchone() is None:raise DataError(409,'人物的原始分析不存在，请重新绑定人物来源')
                state=reference._get(proxy,row['document_id']);reference._revision(state,expected_reference_revision)
                index=next((i for i,c in enumerate(state['sections']['characters']) if c.get('id')==canonical['id']),None)
                if index is None:raise DataError(409,'原提取人物已删除，请重新绑定')
                previous=normalize_character(state['sections']['characters'][index])
                # Business edits keep the existing source anchors. Added states
                # have no invented quote and remain explicit manual facts.
                value={**copy.deepcopy(previous),**canonical}
                for field in ('revealed_at','source_version_id','evidence'):
                    if field in previous:value[field]=copy.deepcopy(previous[field])
                old_states={s.get('id'):s for s in previous.get('snapshots',[])}
                for snapshot in value.get('snapshots',[]):
                    old=old_states.get(snapshot.get('id'),{})
                    for field in ('revealed_at','source_version_id','evidence'):
                        if field in old:snapshot[field]=copy.deepcopy(old[field])
                from .reference_analysis import validate_section
                characters=copy.deepcopy(state['sections']['characters']);characters[index]=value
                characters=validate_section('characters',characters)
                reference._validate_sources(proxy,row['document_id'],{'characters':characters})
                state['sections']['characters']=characters
                reference._write(proxy,row['document_id'],state['sections'],state['structure_type'])
                canonical=characters[index]
            db.execute('UPDATE character_contexts SET character_json=?,revision=revision+1 WHERE character_id=? AND work_id=?',(dumps(canonical),character_id,work_id))
            # Global identity fields are shared, work states stay in the context.
            record=self.store._row(db,'characters',character_id);values=project_values('characters',json.loads(record['values_json']))
            for key,value in self._values(canonical,character_id).items():
                if key not in {'初始状态','阶段状态','关联作品','关联cliché'}:values[key]=value
            self.store._write_library_values(db,'characters',character_id,values)
            self.sync_identity_from_catalog_connection(db,character_id,values)
        return self.contexts(character_id)

    def sync_bound_from_reference_connection(self,db,document_id:str,sections:dict)->None:
        """Hook called before reference commits; its transaction owns both DB writes."""
        if isinstance(db,_ReferenceConnection):return
        databases={r[1]:r[2] for r in db.execute('PRAGMA database_list')}
        if 'catalog_bridge' not in databases:db.execute('ATTACH DATABASE ? AS catalog_bridge',(str(self.store.db_path),))
        cursor=db.execute('SELECT * FROM catalog_bridge.character_contexts WHERE document_id=?',(document_id,))
        names=[column[0] for column in cursor.description]
        rows=[dict(zip(names,row)) for row in cursor.fetchall()]
        by_id={c.get('id'):c for c in sections.get('characters',[]) if isinstance(c,dict)}
        for row in rows:
            character=by_id.get(row['extracted_character_id'])
            if character is None:continue
            from .character_contract import normalize_character
            canonical=normalize_character(character)
            if json.loads(row['character_json'])==canonical:continue
            db.execute('UPDATE catalog_bridge.character_contexts SET character_json=?,revision=revision+1 WHERE character_id=? AND work_id=?',(dumps(canonical),row['character_id'],row['work_id']))
            record=db.execute('SELECT values_json,revision FROM catalog_bridge.characters WHERE id=?',(row['character_id'],)).fetchone()
            if record is None:raise DataError(409,'已绑定的人物库记录不存在，请先修复关联')
            values=project_values('characters',json.loads(record[0]))
            for key,value in self._values(canonical,row['character_id']).items():
                if key not in {'初始状态','阶段状态','关联作品','关联cliché'}:values[key]=value
            if values!=json.loads(record[0]):
                db.execute('UPDATE catalog_bridge.characters SET values_json=?,revision=revision+1 WHERE id=?',(dumps(values),row['character_id']))
                db.execute("INSERT INTO catalog_bridge.record_versions VALUES('characters',?,?) ON CONFLICT(table_key,record_id) DO UPDATE SET revision=excluded.revision",(row['character_id'],record[1]+1))
                others=db.execute('SELECT work_id,document_id,extracted_character_id,character_json FROM catalog_bridge.character_contexts WHERE character_id=? AND work_id<>?',(row['character_id'],row['work_id'])).fetchall()
                mapping={'name':canonical['name'],'aliases':canonical['aliases'],'role_tag':canonical['role_tag'],'temperament':canonical['temperament'],'visual_design':canonical['visual_design']}
                for other_work,other_document,other_extracted,other_json in others:
                    other_character=json.loads(other_json)
                    if all(other_character.get(k)==v for k,v in mapping.items()):continue
                    other_character.update(copy.deepcopy(mapping))
                    other_source=db.execute('SELECT work_id FROM doc_documents WHERE id=?',(other_document,)).fetchone() if other_document else None
                    if other_source is not None:
                        if other_source[0]!=other_work:raise DataError(409,'关联人物的原始文本已关联其他作品，请重新绑定来源')
                        reference=self.reference();direct=_DirectReferenceConnection(db)
                        if direct.execute('SELECT 1 FROM reference_analysis WHERE document_id=?',(other_document,)).fetchone() is None:raise DataError(409,'关联人物的原始分析不存在，请重新绑定来源')
                        other_state=reference._get(direct,other_document)
                        index=next((i for i,c in enumerate(other_state['sections']['characters']) if c.get('id')==other_extracted),None)
                        if index is None:raise DataError(409,'关联作品的人物已删除，请刷新关联')
                        other_state['sections']['characters'][index].update(copy.deepcopy(mapping))
                        from .reference_analysis import validate_section
                        other_state['sections']['characters']=validate_section('characters',other_state['sections']['characters'])
                        reference._write(direct,other_document,other_state['sections'],other_state['structure_type'])
                        other_character=other_state['sections']['characters'][index]
                    db.execute('UPDATE catalog_bridge.character_contexts SET character_json=?,revision=revision+1 WHERE character_id=? AND work_id=?',(dumps(other_character),row['character_id'],other_work))

    def refresh_document(self,document_id:str,*,expected_reference_revision:int|None=None)->dict:
        reference=self.reference()
        with reference._connect() as db:
            db.execute('BEGIN IMMEDIATE');state=reference._get(db,document_id)
            if expected_reference_revision is not None:reference._revision(state,expected_reference_revision)
            self.sync_bound_from_reference_connection(db,document_id,state['sections'])
        return {'document_id':document_id,'reference_revision':state['revision']}

    def sync_identity_from_catalog_connection(self,db,character_id:str,values:dict)->None:
        """Global identity edits reach existing per-work bindings in this transaction."""
        contexts=db.execute('SELECT * FROM character_contexts WHERE character_id=?',(character_id,)).fetchall()
        if not contexts:return
        reference=self.reference()
        databases={r[1]:r[2] for r in db.execute('PRAGMA database_list')}
        if any(r['document_id'] for r in contexts) and 'texts' not in databases:db.execute('ATTACH DATABASE ? AS texts',(str(self.documents.db_path),))
        mapping={'角色名':'name','别名':'aliases','角色定位':'role_tag','性格基调':'temperament','视觉元素设计':'visual_design'}
        for row in contexts:
            canonical=json.loads(row['character_json'])
            for source,target in mapping.items():canonical[target]=json.loads(values[source]) if source=='别名' else values[source]
            from .character_contract import normalize_character
            canonical=normalize_character(canonical)
            if canonical==json.loads(row['character_json']):continue
            source=db.execute('SELECT work_id FROM texts.doc_documents WHERE id=?',(row['document_id'],)).fetchone() if row['document_id'] else None
            if source is not None:
                if source[0]!=row['work_id']:raise DataError(409,'人物的原始文本已关联其他作品，请重新绑定人物来源')
                proxy=_ReferenceConnection(db)
                if proxy.execute('SELECT 1 FROM reference_analysis WHERE document_id=?',(row['document_id'],)).fetchone() is None:raise DataError(409,'人物的原始分析不存在，请重新绑定人物来源')
                state=reference._get(proxy,row['document_id'])
                index=next((i for i,c in enumerate(state['sections']['characters']) if c.get('id')==row['extracted_character_id']),None)
                if index is None:raise DataError(409,'已绑定的提取人物不存在，请刷新人物关联')
                character=state['sections']['characters'][index]
                for target in mapping.values():character[target]=copy.deepcopy(canonical[target])
                from .reference_analysis import validate_section
                state['sections']['characters']=validate_section('characters',state['sections']['characters'])
                reference._validate_sources(proxy,row['document_id'],{'characters':state['sections']['characters']})
                reference._write(proxy,row['document_id'],state['sections'],state['structure_type'])
                canonical=state['sections']['characters'][index]
            db.execute('UPDATE character_contexts SET character_json=?,revision=revision+1 WHERE character_id=? AND work_id=?',(dumps(canonical),character_id,row['work_id']))
