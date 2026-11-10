"""Synthetic, independent InkCrawler workspaces for the read-only bridge."""
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import threading

import pytest

from inkreference.crawler_bridge import CrawlerBridge
from inkreference.database import DataError, DataStore
from inkreference.documents import DocumentStore
from inkreference.projects import ProjectStore


BOOK = 'a' * 32
SNAP = 'b' * 64
WORK = 'c' * 64
OBS = 'd' * 32
CHAPTER = 'e' * 64
RUN = 'f' * 32


def workspace(root, *, title='测试作品', text='原始正文'):
    root.mkdir()
    path = root / 'inkcrawler.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
          CREATE TABLE books(id TEXT PRIMARY KEY,title TEXT,author TEXT,created_at TEXT,is_completed INTEGER);
          CREATE TABLE chapters(id TEXT PRIMARY KEY,book_id TEXT,position INTEGER,title TEXT,content TEXT,state TEXT,char_count INTEGER);
          CREATE TABLE task_trash(kind TEXT,item_id TEXT,include_work INTEGER);
          CREATE TABLE source_downloads(id TEXT PRIMARY KEY,source TEXT,state TEXT,request TEXT,created_at REAL);
          CREATE TABLE source_files(download_id TEXT,filename TEXT,result TEXT,number INTEGER);
          CREATE TABLE source_pages(download_id TEXT,page INTEGER,payload_hash TEXT);
          CREATE TABLE ranking_migration_pages(download_id TEXT,page INTEGER,payload_hash TEXT,skipped INTEGER);
          CREATE TABLE ranking_works(id TEXT PRIMARY KEY,platform TEXT,platform_novel_id TEXT,metadata_json TEXT);
          CREATE TABLE ranking_metadata_versions(work_id TEXT,digest TEXT,metadata_json TEXT);
          CREATE TABLE ranking_snapshots(id TEXT PRIMARY KEY,platform TEXT,rank_family TEXT,rank_sub_cat TEXT,captured_at REAL);
          CREATE TABLE ranking_observations(id TEXT PRIMARY KEY,work_id TEXT,snapshot_id TEXT,metadata_digest TEXT,observation_json TEXT,page INTEGER,record_index INTEGER,captured_at REAL);
          CREATE TABLE ranking_chapters(id TEXT PRIMARY KEY,work_id TEXT,chapter_json TEXT);
          CREATE TABLE ranking_observation_chapters(observation_id TEXT,chapter_index INTEGER,chapter_id TEXT);
        ''')
        db.execute('INSERT INTO books VALUES (?,?,?,?,?)', (BOOK, title, '甲作者', '2026-01-01', 1))
        db.execute('INSERT INTO chapters VALUES (?,?,?,?,?,?,?)', ('1'*32, BOOK, 0, '第一章', text, 'done', len(text)))
        db.execute('INSERT INTO chapters VALUES (?,?,?,?,?,?,?)', ('2'*32, BOOK, 1, '第二章', '旧第二章', 'done', 4))
        db.execute('INSERT INTO source_downloads VALUES (?,?,?,?,?)', (RUN, 'qidian', 'completed', '{}', 1))
        db.execute('INSERT INTO source_pages VALUES (?,?,?)', (RUN, 1, 'raw'))
        db.execute('INSERT INTO ranking_snapshots VALUES (?,?,?,?,?)', (SNAP, 'qidian', '月票榜', '', 1))
        db.execute('INSERT INTO ranking_works VALUES (?,?,?,?)', (WORK, 'qidian', '123', json.dumps({'title':'未来书名'})))
        db.execute('INSERT INTO ranking_metadata_versions VALUES (?,?,?)', (WORK, 'old', json.dumps({'title': '旧书名', 'author':'旧作者', 'publish_date':'2025-01-01'})))
        db.execute('INSERT INTO ranking_observations VALUES (?,?,?,?,?,?,?,?)', (OBS, WORK, SNAP, 'old', json.dumps({'rank':2,'_stored_chapter_count':1}), 3, 0, 1))
        db.execute('INSERT INTO ranking_chapters VALUES (?,?,?)', (CHAPTER, WORK, json.dumps({'chapter_num':1,'title':'第一章','content':'榜单旧正文'}, ensure_ascii=False)))
        db.execute('INSERT INTO ranking_observation_chapters VALUES (?,?,?)', (OBS, 0, CHAPTER))
    return path


def test_status_configuration_and_backup_owned_settings(tmp_path):
    source = tmp_path / 'crawler'
    workspace(source)
    reference = tmp_path / 'reference'
    bridge = CrawlerBridge(reference)
    assert not bridge.settings_db.exists()
    bridge.source()
    assert not bridge.settings_db.exists()
    status = bridge.configure(str(source))
    assert status['available'] and status['capabilities'] == {'books':True, 'rankings':True, 'models':True}
    assert status['ranking_index_pending'] is True
    assert bridge.settings_db.is_file()
    with pytest.raises(DataError):
        bridge.configure(str(tmp_path / 'missing'))
    assert bridge.source() == source.resolve()
    with sqlite3.connect(bridge.settings_db) as src, sqlite3.connect(tmp_path / 'restored.sqlite3') as dest:
        src.backup(dest)
    with sqlite3.connect(tmp_path / 'restored.sqlite3') as db:
        assert db.execute("SELECT value FROM crawler_source_settings WHERE key='data_dir'").fetchone()[0] == str(source.resolve())


def test_snapshot_does_not_modify_source_and_idempotency_includes_workspace_and_title(tmp_path):
    source = tmp_path / 'crawler'
    path = workspace(source)
    bridge, docs = CrawlerBridge(tmp_path / 'reference'), DocumentStore(tmp_path / 'docs')
    bridge.configure(str(source))
    first = bridge.snapshot_book(BOOK, docs)
    assert bridge.snapshot_book(BOOK, docs)['id'] == first['id']
    origin = bridge.snapshot_origin(first['id'], docs)
    assert origin['kind'] == 'book' and origin['work_id'] == BOOK and origin['author'] == '甲作者'
    assert origin['metadata']['is_completed'] is True
    with sqlite3.connect(docs.db_path) as original, sqlite3.connect(tmp_path / 'docs-backup.sqlite3') as backup:
        original.backup(backup)
    with sqlite3.connect(tmp_path / 'docs-backup.sqlite3') as db:
        saved = db.execute('SELECT origin_json FROM crawler_document_origins WHERE document_id=?', (first['id'],)).fetchone()
        assert json.loads(saved[0]) == origin
    raw, _ = docs.get_original(first['id'])
    assert raw.decode() == '第一章\n原始正文\n\n第二章\n旧第二章\n\n'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE books SET title=? WHERE id=?', ('修改标题', BOOK))
        assert db.execute('SELECT content FROM chapters WHERE id=?', ('1'*32,)).fetchone()[0] == '原始正文'
    renamed = bridge.snapshot_book(BOOK, docs)
    assert renamed['id'] != first['id']
    other = tmp_path / 'other'
    workspace(other, title='修改标题')
    bridge.configure(str(other))
    assert bridge.snapshot_book(BOOK, docs)['id'] not in {first['id'], renamed['id']}


@pytest.mark.parametrize('kind,include_work', [('work', 0), ('book', 1)])
def test_hidden_works_cannot_be_listed_or_opened(tmp_path, kind, include_work):
    source = tmp_path / 'crawler'; path = workspace(source)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO task_trash VALUES (?,?,?)', (kind, BOOK, include_work))
    bridge, docs = CrawlerBridge(tmp_path / 'reference'), DocumentStore(tmp_path / 'docs')
    bridge.configure(str(source))
    assert bridge.books() == []
    with pytest.raises(DataError) as error:
        bridge.snapshot_book(BOOK, docs)
    assert error.value.status_code == 404


def test_snapshot_over_limit_is_explicit_and_creates_no_document(tmp_path, monkeypatch):
    import inkreference.documents as document_module
    source = tmp_path / 'crawler'; workspace(source, text='较长正文' * 50)
    bridge, docs = CrawlerBridge(tmp_path / 'reference'), DocumentStore(tmp_path / 'docs')
    bridge.configure(str(source))
    monkeypatch.setattr(document_module, 'MAX_UPLOAD_BYTES', 100)
    with pytest.raises(DataError) as error:
        bridge.snapshot_book(BOOK, docs)
    assert error.value.status_code == 413
    assert docs.list_documents() == []


def test_ranking_uses_observation_metadata_and_its_own_chapters(tmp_path):
    source = tmp_path / 'crawler'; path = workspace(source)
    bridge, docs = CrawlerBridge(tmp_path / 'reference'), DocumentStore(tmp_path / 'docs')
    bridge.configure(str(source))
    item = bridge.ranking(SNAP)['items'][0]
    assert item['work_id'] == WORK and item['observation_id'] == OBS and item['chapter_count'] == 1
    assert item['metadata']['title'] == '旧书名' and 'publish_date' not in item['metadata']
    document = bridge.snapshot_ranking_work(SNAP, WORK, docs)
    assert bridge.snapshot_ranking_work(SNAP, WORK, docs)['id'] == document['id']
    origin = bridge.snapshot_origin(document['id'], docs)
    assert origin['kind'] == 'ranking' and origin['snapshot_id'] == SNAP and origin['author'] == '旧作者'
    assert origin['metadata']['historical_publish_date_unverified'] == '2025-01-01'
    assert docs.get_original(document['id'])[0].decode() == '第一章\n榜单旧正文\n\n'
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM ranking_observation_chapters')
    with pytest.raises(DataError) as error:
        bridge.snapshot_ranking_work(SNAP, WORK, docs)
    assert error.value.status_code == 409


def test_ranking_snapshot_limits_and_damaged_metadata_are_explicit(tmp_path, monkeypatch):
    import inkreference.documents as document_module
    source = tmp_path / 'crawler'; path = workspace(source)
    bridge, docs = CrawlerBridge(tmp_path / 'reference'), DocumentStore(tmp_path / 'docs')
    bridge.configure(str(source))
    monkeypatch.setattr(document_module, 'MAX_UPLOAD_BYTES', 5)
    with pytest.raises(DataError) as error:
        bridge.snapshot_ranking_work(SNAP, WORK, docs)
    assert error.value.status_code == 413 and docs.list_documents() == []
    monkeypatch.setattr(document_module, 'MAX_UPLOAD_BYTES', 50 * 1024 * 1024)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE ranking_metadata_versions SET metadata_json='invalid'")
    item = bridge.ranking(SNAP)['items'][0]
    assert item['metadata_unavailable'] is True and item['metadata'] == {}
    with pytest.raises(DataError) as error:
        bridge.snapshot_ranking_work(SNAP, WORK, docs)
    assert error.value.status_code == 409


def test_book_read_transaction_pins_both_chapters_during_concurrent_crawl(tmp_path):
    source = tmp_path / 'crawler'; path = workspace(source)
    bridge, docs = CrawlerBridge(tmp_path / 'reference'), DocumentStore(tmp_path / 'docs')
    bridge.configure(str(source))
    original = bridge.database
    changed = threading.Event()
    class Hook:
        def __init__(self, db): self.db, self.first = db, True
        def execute(self, *args): return self.db.execute(*args)
        def blobopen(self, *args, **kwargs):
            if self.first:
                self.first = False
                with sqlite3.connect(path, timeout=3) as writer:
                    writer.execute("UPDATE chapters SET content='更新后' WHERE id=?", ('2'*32,))
                changed.set()
            return self.db.blobopen(*args, **kwargs)
    @contextmanager
    def hooked():
        with original() as db:
            yield Hook(db)
    bridge.database = hooked
    doc = bridge.snapshot_book(BOOK, docs)
    assert changed.is_set() and '旧第二章' in docs.get_original(doc['id'])[0].decode()


def test_model_paths_are_relative_confined_and_checked(tmp_path):
    source = tmp_path / 'crawler'; path = workspace(source)
    directory = source / 'source-downloads' / RUN / 'weights'
    directory.mkdir(parents=True)
    file = directory / 'model.gguf'; file.write_bytes(b'model')
    result = {'relative_path':'weights/model.gguf', 'bytes':5, 'mtime_ns':file.stat().st_mtime_ns}
    with sqlite3.connect(path) as db:
        db.execute('UPDATE source_downloads SET source=? WHERE id=?', ('model_huggingface', RUN))
        db.execute('INSERT INTO source_files VALUES (?,?,?,?)', (RUN, 'hashed-key', json.dumps(result), 0))
    bridge = CrawlerBridge(tmp_path / 'reference'); bridge.configure(str(source))
    model = bridge.models()[0]
    assert model['filename'] == 'weights/model.gguf' and model['available'] and model['path'] == str(file)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE source_downloads ADD COLUMN task_removed INTEGER NOT NULL DEFAULT 0')
        db.execute('UPDATE source_downloads SET task_removed=1 WHERE id=?', (RUN,))
    assert bridge.models() == []
    with sqlite3.connect(path) as db:
        db.execute('UPDATE source_downloads SET task_removed=0 WHERE id=?', (RUN,))
    assert bridge.models()[0]['path'] == str(file)
    result['relative_path'] = '../escape'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE source_files SET result=?', (json.dumps(result),))
    assert bridge.models() == []


def test_ranking_sorted_by_real_rank_and_missing_version_is_explicit(tmp_path):
    source = tmp_path / 'crawler'; path = workspace(source)
    work, observation = '1'*64, '2'*32
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO ranking_works VALUES (?,?,?,?)', (work, 'qidian', '456', json.dumps({'title':'未来值'})))
        db.execute('INSERT INTO ranking_observations VALUES (?,?,?,?,?,?,?,?)',
                   (observation, work, SNAP, 'missing', json.dumps({'rank':1}), 99, 0, 3))
    bridge = CrawlerBridge(tmp_path / 'reference'); bridge.configure(str(source))
    rows = bridge.ranking(SNAP, limit=1)['items']
    assert rows[0]['work_id'] == work and rows[0]['metadata'] == {} and rows[0]['metadata_unavailable'] is True
    assert bridge.ranking(SNAP, limit=1, offset=1)['items'][0]['work_id'] == WORK


def test_model_path_and_database_use_same_workspace_during_source_switch(tmp_path):
    first = tmp_path / 'first'; path = workspace(first)
    second = tmp_path / 'second'; workspace(second)
    target = first / 'source-downloads' / RUN / 'model.gguf'
    target.parent.mkdir(parents=True); target.write_bytes(b'model')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE source_downloads SET source=? WHERE id=?', ('model_github', RUN))
        db.execute('INSERT INTO source_files VALUES (?,?,?,?)', (RUN, 'model.gguf',
            json.dumps({'relative_path':'model.gguf', 'bytes':5, 'mtime_ns':target.stat().st_mtime_ns}), 0))
    bridge = CrawlerBridge(tmp_path / 'reference')
    calls = iter((first, second))
    bridge.source = lambda: next(calls)
    assert bridge.models()[0]['path'] == str(target)


def test_model_nested_symlink_is_never_exposed(tmp_path):
    source = tmp_path / 'crawler'; path = workspace(source)
    model_dir = source / 'source-downloads' / RUN
    model_dir.mkdir(parents=True)
    external = tmp_path / 'external'; external.mkdir()
    target = external / 'model.gguf'; target.write_bytes(b'model')
    (model_dir / 'link').symlink_to(external, target_is_directory=True)
    result = {'relative_path':'link/model.gguf', 'bytes':5, 'mtime_ns':target.stat().st_mtime_ns}
    with sqlite3.connect(path) as db:
        db.execute('UPDATE source_downloads SET source=? WHERE id=?', ('model_huggingface', RUN))
        db.execute('INSERT INTO source_files VALUES (?,?,?,?)', (RUN, 'hash', json.dumps(result), 0))
    bridge = CrawlerBridge(tmp_path / 'reference'); bridge.configure(str(source))
    assert bridge.models() == []


def test_reference_backup_restores_source_configuration_and_provenance(tmp_path):
    source = tmp_path / 'crawler'; workspace(source)
    reference = tmp_path / 'reference'; DataStore(reference / 'inkreference.sqlite3')
    documents = DocumentStore(reference)
    bridge = CrawlerBridge(reference); bridge.configure(str(source))
    document = bridge.snapshot_ranking_work(SNAP, WORK, documents)
    project = ProjectStore(reference)
    restored_path = project.restore(project.export())['data_dir']
    restored_bridge, restored_documents = CrawlerBridge(restored_path), DocumentStore(restored_path)
    assert restored_bridge.source() == source.resolve()
    assert restored_bridge.snapshot_origin(document['id'], restored_documents)['snapshot_id'] == SNAP
    assert restored_documents.get_original(document['id'])[0] == documents.get_original(document['id'])[0]
