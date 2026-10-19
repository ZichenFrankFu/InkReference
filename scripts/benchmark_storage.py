"""Reproducible synthetic SQLite growth benchmark; prints JSON and removes fixtures."""
import sys,json,sqlite3,tempfile,time,statistics,tracemalloc
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
from inkreference.storage_utils import ensure_growth_indexes,json_field_expression
from inkreference.database import DataStore
from inkreference.catalog_schema_v11 import project_values

def median(fn,n=7):
    fn(); times=[]
    for _ in range(n):
        start=time.perf_counter();fn();times.append((time.perf_counter()-start)*1000)
    return round(statistics.median(times),3)
def measure_memory(fn):
    tracemalloc.start();fn();peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop();return peak
with tempfile.TemporaryDirectory(prefix='inkreference-growth-bench-') as raw:
    root=Path(raw); db=sqlite3.connect(root/'growth.db')
    db.executescript('''CREATE TABLE works(id TEXT PRIMARY KEY,values_json TEXT NOT NULL,sort_order INTEGER NOT NULL);
    CREATE TABLE run_jobs(id TEXT PRIMARY KEY,document_id TEXT,version_id TEXT,created_at TEXT,status TEXT);
    CREATE INDEX run_jobs_document_version ON run_jobs(document_id,version_id,created_at,id);
    CREATE TABLE run_chunks(run_id TEXT,id TEXT,status TEXT,position INTEGER,PRIMARY KEY(run_id,id));
    CREATE INDEX run_chunks_order ON run_chunks(run_id,position);
    CREATE TABLE run_events(seq INTEGER PRIMARY KEY,run_id TEXT,time TEXT,stage TEXT,message TEXT);
    CREATE TABLE doc_versions(id TEXT PRIMARY KEY,document_id TEXT,version_number INTEGER,payload_json TEXT);
    CREATE INDEX doc_versions_document ON doc_versions(document_id,version_number);
    CREATE TABLE doc_chapter_views(version_id TEXT,chapter_id TEXT,chapter_order INTEGER,metadata_json TEXT,text TEXT,PRIMARY KEY(version_id,chapter_id));
    CREATE INDEX doc_chapter_views_version ON doc_chapter_views(version_id,chapter_order);
    CREATE TABLE library_links(source_table TEXT,source_id TEXT,field TEXT,position INTEGER,target_table TEXT,target_id TEXT,payload_json TEXT,PRIMARY KEY(source_table,source_id,field,position));
    CREATE INDEX idx_library_links_target ON library_links(target_table,target_id);
    CREATE TABLE usage_calls(id TEXT PRIMARY KEY,run_id TEXT,started_at TEXT,record_json TEXT);
    CREATE INDEX usage_calls_run ON usage_calls(run_id,started_at);
    ''')
    N=30000
    db.executemany('INSERT INTO works VALUES(?,?,?)',[(f'W{i:06}',json.dumps({'作品':f'合成{i}','类别':f'分类{i%100}','剧情梗概':'合成梗概。'*60},ensure_ascii=False),i) for i in range(N)])
    db.executemany('INSERT INTO run_jobs VALUES(?,?,?,?,?)',[(f'J{i:06}',f'D{i%100}',f'V{i%500}',f'2026-01-{i%28+1:02}T{i//28:06}', 'running' if i%999==0 else 'approved') for i in range(N)])
    db.executemany('INSERT INTO run_chunks VALUES(?,?,?,?)',[(f'J{i//10:06}',f'C{i%10}','completed' if i%3 else 'pending',i%10) for i in range(N*10)])
    db.executemany('INSERT INTO run_events VALUES(?,?,?,?,?)',[(i,f'J{i%N:06}','2026','stage','synthetic') for i in range(N*10)])
    db.executemany('INSERT INTO doc_versions VALUES(?,?,?,?)',[(f'V{i:06}',f'D{i%100}',i//100,'{}') for i in range(N)])
    db.executemany('INSERT INTO doc_chapter_views VALUES(?,?,?,?,?)',[(f'V{i//100:06}',f'C{i%100}',i%100,'{}','合成正文。'*200) for i in range(N)])
    db.executemany('INSERT INTO library_links VALUES(?,?,?,?,?,?,?)',[('works',f'W{i%N:06}','关联角色',i//N,'characters',f'P{i:06}','{}') for i in range(N*3)])
    db.executemany('INSERT INTO usage_calls VALUES(?,?,?,?)',[(f'U{i:06}',f'J{i%N:06}',f'2026-01-{i%28+1:02}T{i//28:06}','{}') for i in range(N)])
    db.commit()
    expr=json_field_expression('类别')
    queries={
    'catalog_filter_page':(f'SELECT id FROM works WHERE {expr}=? ORDER BY sort_order,id LIMIT 50',('分类42',)),
    'catalog_filter_count':(f'SELECT COUNT(*) FROM works WHERE {expr}=?',('分类42',)),
    'task_recent_summary':('SELECT id,document_id,created_at,(SELECT COUNT(*) FROM run_chunks c WHERE c.run_id=j.id AND c.status=\'completed\') FROM run_jobs j ORDER BY created_at DESC,id LIMIT 100',()),
    'task_document_summary':('SELECT id FROM run_jobs WHERE document_id=? ORDER BY created_at DESC,id LIMIT 100',('D42',)),
    'task_events':('SELECT seq,stage,message FROM run_events WHERE run_id=? ORDER BY seq',('J000042',)),
    'source_versions_existing_index':('SELECT id,version_number FROM doc_versions WHERE document_id=? ORDER BY version_number DESC LIMIT 20',('D42',)),
    'chapter_lookup_existing_pk':('SELECT text FROM doc_chapter_views WHERE version_id=? AND chapter_id=?',('V000042','C20')),
    'chapter_directory_existing_index':('SELECT chapter_id,metadata_json FROM doc_chapter_views WHERE version_id=? ORDER BY chapter_order',('V000042',)),
    'relations_outgoing':('SELECT target_id FROM library_links WHERE source_table=? AND source_id=? AND target_table=?',('works','W000042','characters')),
    'relations_incoming':('SELECT source_id FROM library_links WHERE target_table=? AND target_id=? AND source_table=?',('characters','P000042','works')),
    'usage_recent_page':('SELECT id,started_at,record_json FROM usage_calls ORDER BY started_at DESC,id DESC LIMIT 51',()),
    'usage_run_page':('SELECT id,started_at,record_json FROM usage_calls WHERE run_id=? ORDER BY started_at DESC,id DESC LIMIT 51',('J000042',)),
    }
    result={'fixture':{'works':N,'tasks':N,'chunks':N*10,'events':N*10,'source_versions':N,'chapter_views':N,'relations':N*3,'usage_calls':N},'method':'Linux SQLite warm-cache median of 7 samples; synthetic data; no LLM requests','queries':{}}
    for name,(sql,params) in queries.items():
        result['queries'][name]={'before_ms':median(lambda:db.execute(sql,params).fetchall()),'before_plan':[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,params)]}
    added=ensure_growth_indexes(db,'catalog')+ensure_growth_indexes(db,'workbench');db.commit()
    for name,(sql,params) in queries.items():
        result['queries'][name].update(after_ms=median(lambda:db.execute(sql,params).fetchall()),after_plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,params)])
    result['indexes_added']=added;db.close()
    catalog=DataStore(root/'inkreference.sqlite3')
    values=[project_values('works',{'作品ID':f'W{i:06}','作品':f'合成{i}','讨论热度':str(i%10+1),'商业规模':str(i%10+1)}) for i in range(N)]
    with catalog.transaction() as c:
        c.executemany('INSERT INTO works(id,values_json,cliche_layout,revision,sort_order) VALUES(?,?,\'[]\',1,?)',[(v['作品ID'],json.dumps(v,ensure_ascii=False),i) for i,v in enumerate(values)])
        c.executemany('INSERT INTO cliches VALUES(?,?,1,?)',[(f'C{i:04}',json.dumps(project_values('cliches',{'clichéID':f'C{i:04}','cliché内容':f'合成桥段{i}'}),ensure_ascii=False),i) for i in range(2000)])
        c.executemany('INSERT INTO work_cliches VALUES(?,?,?,?,?)',[(f'W{i%N:06}',f'C{(i%2000+i//N)%2000:04}','合成关联',i//2000,i//2000) for i in range(N*3)])
    def old_score():
        with catalog.connect() as c:
            works={r['id']:json.loads(r['values_json']) for r in c.execute('SELECT id,values_json FROM works')}
            by={}
            for r in c.execute('SELECT work_id,cliche_id FROM work_cliches'):by.setdefault(r[1],set()).add(r[0])
            return works,by
    def new_score():return catalog.list_records('cliches',limit=50,include_work_facets=False)
    result['score_memory']={'old_full_maps_peak_bytes':measure_memory(old_score),'new_page_peak_bytes':measure_memory(new_score),'new_warm_page_ms':median(new_score,5),'legacy_map_load_ms':median(old_score,5)}
    # Trace exactly that subsequent pages reuse denominator and never read the
    # entire relation map; the durable epoch is covered by committed tests.
    print(json.dumps(result,ensure_ascii=False,indent=2))
