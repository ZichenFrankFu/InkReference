"""Validate the shared-data contract against a real InkCrawler store.

Two separate, editable application environments share a temporary directory.
The crawler process owns its real Store/DownloadStore while the reference app
serves HTTP. Only the downloaded model bytes are synthetic; there is no network
request to a third-party model host.

    INKCRAWLER_PYTHON=/path/to/crawler/python \
    INKREFERENCE_PYTHON=/path/to/reference/python \
    python tests/e2e_crawler_actual_contract.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CRAWLER_ROOT = Path(os.environ.get('INKCRAWLER_REPO', '/workspace/InkCrawler'))
CRAWLER_PYTHON = os.environ.get('INKCRAWLER_PYTHON', '/workspace/.onboarding/envs/InkCrawler/bin/python')
REFERENCE_PYTHON = os.environ.get('INKREFERENCE_PYTHON', '/workspace/.onboarding/envs/InkReference/bin/python')


CRAWLER = r'''
import hashlib,json,sys
from pathlib import Path
from fastapi.testclient import TestClient
from inkcrawler.app import create_app
from inkcrawler.providers.models import DownloadRequest,Page
from inkcrawler.providers.model_sources import model_entry

root = Path(sys.argv[1])
app = create_app(root, testing=True)
client = TestClient(app)
client.__enter__()
client.get('/')
store, service = app.state.store, app.state.source_downloads
book = store.create_book('实时共享作品', '作者甲', 'https://www.qidian.com/book/123/', {}, [
    {'title':'第一章 原始标题','url':'https://read.qidian.com/chapter/123/1/'}])
chapter = store.list_chapters(book['id'])[0]
store.update_chapter(chapter['id'], state='done', content='第一版正文，来自真实爬虫数据库。')
store.update_book(book['id'], is_completed=True)
manual = store.create_book('手动标记作品', '作者乙', 'https://books.example.org/book/456/', {}, [])
store.update_book(manual['id'], is_completed=True)

run = service.data.create('qidian', DownloadRequest(ranking_browser='playwright'))['id']
record = {'source_id': f'{run}:月票榜:1:123', 'table':'rankings','kind':'rankings','data':{
    'platform_novel_id':'123','title':'真实榜单作品','author':'榜单作者','status':'completed',
    'url':'https://www.qidian.com/book/123/','rank_type':'月票榜','rank_family':'月票榜',
    'rank_sub_cat':'','rank':1,'captured_at':'2026-01-02T12:00:00+00:00',
    'source_url':'https://www.qidian.com/rank/yuepiao/',
    'first_n_chapters':[{'chapter_num':1,'title':'第一章 榜单',
        'url':'https://read.qidian.com/chapter/123/1/', 'content':'榜单章节原文。','word_count':7}]}}
service.data.save_page(run, None, Page([record], None))
with store._db() as db:
    snapshot = db.execute('SELECT id FROM ranking_snapshots WHERE download_id=?', (run,)).fetchone()[0]
    work = db.execute('SELECT id FROM ranking_works WHERE platform=? AND platform_novel_id=?', ('qidian','123')).fetchone()[0]

# A second prose work is created only after the ranking observation already
# exists. Both source identities use the official URL for the same work ID.
late_book = store.create_book('榜单之后创建的作品', '作者丙', 'https://book.qidian.com/info/123/', {}, [])

model = service.data.create('model_huggingface', DownloadRequest(selections=['合成测试模型']))['id']
entries = [model_entry('huggingface', u) for u in (
    'https://huggingface.co/fictional/sample/resolve/main/config.json',
    'https://huggingface.co/fictional/sample/resolve/main/weights/model.gguf')]
service.data.plan_files(model, entries)
model_paths = []
for number, entry in enumerate(entries):
    path = service.file_path(model, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = ('fixture-' + str(number)).encode()
    path.write_bytes(raw)
    service.data.save_file(model, number, {'bytes':len(raw),'mtime_ns':path.stat().st_mtime_ns,
        'sha256':hashlib.sha256(raw).hexdigest(),'relative_path':entry['relative_path'], 'validation':'local_sha256'})
    model_paths.append(str(path))
service.data.state(model, 'completed')

remove_model = service.data.create('model_github', DownloadRequest(selections=['将删除模型']))['id']
remove_entry = model_entry('github', 'https://github.com/fictional/sample/releases/download/v1/model.gguf')
service.data.plan_files(remove_model, [remove_entry])
remove_path = service.file_path(remove_model, 0)
remove_path.parent.mkdir(parents=True, exist_ok=True)
remove_path.write_bytes(b'remove-fixture')
service.data.save_file(remove_model, 0, {'bytes':remove_path.stat().st_size, 'mtime_ns':remove_path.stat().st_mtime_ns,
    'sha256':hashlib.sha256(remove_path.read_bytes()).hexdigest(), 'relative_path':remove_entry['relative_path']})
service.data.state(remove_model, 'completed')

local = root.parent / '本地合成模型.gguf'
local.write_bytes(b'local-fixture')
registered = client.post('/api/local-models', json={'path':str(local), 'name':'本地登记模型'})
assert registered.status_code == 201, registered.text
local_id = registered.json()['id']

print('READY '+json.dumps({'book':book['id'],'late_book':late_book['id'],'manual':manual['id'],'chapter':chapter['id'],'snapshot':snapshot,
      'work':work,'model':model,'model_paths':model_paths,'remove_model':remove_model,'remove_path':str(remove_path),
      'local_id':local_id,'local_path':str(local)}, ensure_ascii=False), flush=True)
for line in sys.stdin:
    if line.strip() == 'update':
        store.update_chapter(chapter['id'], state='done', content='第二版正文，爬虫继续保存。')
        store.update_book(book['id'], source_completion_status='ongoing', metadata_origin='source', source_metadata_url=book['toc_url'])
        print('UPDATED', flush=True)
    elif line.strip() in ('rank-ongoing', 'rank-completed'):
        status = 'ongoing' if line.strip() == 'rank-ongoing' else 'completed'
        # Use a fresh run and later captured time, as a normal update would.
        next_run = service.data.create('qidian', DownloadRequest(ranking_browser='playwright'))['id']
        data = {**record['data'], 'status':status,
                'captured_at':'2026-01-03T12:00:00+00:00' if status == 'ongoing' else '2026-01-04T12:00:00+00:00'}
        next_record = {**record, 'source_id':f'{next_run}:月票榜:1:123', 'data':data}
        service.data.save_page(next_run, None, Page([next_record], None))
        current, late = store.get_book(book['id']), store.get_book(late_book['id'])
        assert current['completion_source'] == late['completion_source'] == 'source', (current, late)
        assert current['is_completed'] == late['is_completed'] == (status == 'completed'), (current, late)
        if status == 'ongoing':
            rejected = client.patch('/api/books/' + book['id'], json={'is_completed':True})
            assert rejected.status_code == 400, rejected.text
        print('RANK-'+status.upper(), flush=True)
    elif line.strip() == 'switch-source':
        # The manual completion mark set before acquiring rank metadata must
        # reappear when moving away from the official source.
        switched = store.update_book(book['id'], toc_url='https://books.example.org/book/123/')
        assert switched['is_completed'] and switched['completion_source'] == 'manual', switched
        print('SOURCE-SWITCHED', flush=True)
    elif line.strip() == 'keep-trash':
        result = client.delete(f'/api/source-tasks/{model}?include_files=false')
        assert result.status_code == 200, result.text
        print('KEEP-TRASH', flush=True)
    elif line.strip() == 'keep-delete':
        result = client.delete(f'/api/task-trash/source/{model}')
        assert result.status_code == 200, result.text
        print('KEEP-DELETED', flush=True)
    elif line.strip() == 'remove-trash':
        result = client.delete(f'/api/source-tasks/{remove_model}?include_files=true')
        assert result.status_code == 200, result.text
        print('REMOVE-TRASH', flush=True)
    elif line.strip() == 'remove-delete':
        result = client.delete(f'/api/task-trash/source/{remove_model}')
        assert result.status_code == 200, result.text
        print('REMOVE-DELETED', flush=True)
    elif line.strip() == 'quit':
        break
client.__exit__(None, None, None)
'''


REFERENCE = r'''
import sys,uvicorn
from pathlib import Path
from inkreference.api import create_app
app = create_app(Path(sys.argv[1]), Path(sys.argv[2]))
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[3]), log_level='error')
'''


def read_lines(stream, output):
    for line in stream:
        output.put(line.rstrip('\n'))


def json_request(url, *, method='GET', data=None):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    headers = {'Content-Type': 'application/json'} if body is not None else {}
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise AssertionError(f'{method} {url} failed {exc.code}: {exc.read(2000).decode()}') from exc


def main():
    for executable in (CRAWLER_PYTHON, REFERENCE_PYTHON):
        if not Path(executable).is_file():
            raise RuntimeError('Set INKCRAWLER_PYTHON and INKREFERENCE_PYTHON to their application environments')
    with tempfile.TemporaryDirectory(prefix='inkapps-contract-') as folder:
        temp = Path(folder)
        source, reference = temp / 'crawler', temp / 'reference'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with (temp / 'crawler.log').open('w+', encoding='utf-8') as crawler_log, (temp / 'reference.log').open('w+', encoding='utf-8') as ref_log:
            writer = subprocess.Popen([CRAWLER_PYTHON, '-c', CRAWLER, str(source)], cwd=CRAWLER_ROOT,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=crawler_log, text=True, bufsize=1)
            output: queue.Queue[str] = queue.Queue()
            threading.Thread(target=read_lines, args=(writer.stdout, output), daemon=True).start()
            server = None
            try:
                try:
                    ready = output.get(timeout=30)
                except queue.Empty as exc:
                    crawler_log.flush()
                    raise AssertionError('InkCrawler fixture did not start: ' + (temp / 'crawler.log').read_text()) from exc
                assert ready.startswith('READY '), ready
                ids = json.loads(ready[6:])
                server = subprocess.Popen([REFERENCE_PYTHON, '-c', REFERENCE, str(reference),
                    str(temp / 'absent-dist'), str(port)], cwd=ROOT, stdout=ref_log, stderr=ref_log)
                base = f'http://127.0.0.1:{port}/api'
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        ref_log.flush()
                        raise AssertionError('InkReference failed to start: ' + (temp / 'reference.log').read_text())
                    try:
                        assert json_request(base + '/health')['status'] == 'ok'
                        break
                    except (urllib.error.URLError, TimeoutError, OSError):
                        if time.monotonic() >= deadline:
                            raise AssertionError('InkReference fixture startup timed out')
                        time.sleep(.05)
                prefix = base + '/studio'
                status = json_request(prefix + '/crawler/source', method='PUT', data={'data_dir':str(source)})
                assert status['available'] and all(status['capabilities'].values()), status
                assert status['ranking_index_pending'] is False, status
                books = json_request(prefix + '/crawler/books')['items']
                book = next(item for item in books if item['id'] == ids['book'])
                assert (book['title'],book['author'],book['chapters'],book['is_completed']) == ('实时共享作品','作者甲',1,1), book
                assert book['completion_source'] == 'source', book
                manual = next(item for item in books if item['id'] == ids['manual'])
                assert manual['is_completed'] and manual['completion_source'] == 'manual', manual
                late = next(item for item in books if item['id'] == ids['late_book'])
                assert late['is_completed'] and late['completion_source'] == 'source', late
                snapshots = json_request(prefix + '/crawler/rankings')['items']
                snapshot = next(item for item in snapshots if item['id'] == ids['snapshot'])
                assert snapshot['rank_family'] == '月票榜' and snapshot['works'] == 1, snapshot
                ranking = json_request(prefix + '/crawler/rankings/' + ids['snapshot'])['items'][0]
                assert ranking['work_id'] == ids['work'] and ranking['metadata']['title'] == '真实榜单作品'
                assert ranking['metadata']['author'] == '榜单作者' and ranking['chapter_count'] == 1
                models = json_request(prefix + '/crawler/models')['items']
                actual_paths = {row['path'] for row in models if row['id'] == ids['model'] and row['available']}
                assert actual_paths == set(ids['model_paths']), (models, ids['model_paths'])
                assert any(row['id'] == ids['local_id'] and row['path'] == ids['local_path'] and row['available'] for row in models), models
                assert any(row['id'] == ids['remove_model'] and row['path'] == ids['remove_path'] for row in models), models

                writer.stdin.write('keep-trash\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'KEEP-TRASH'
                models = json_request(prefix + '/crawler/models')['items']
                assert {row['path'] for row in models if row['id'] == ids['model'] and row['available']} == set(ids['model_paths']), models
                writer.stdin.write('keep-delete\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'KEEP-DELETED'
                models = json_request(prefix + '/crawler/models')['items']
                assert {row['path'] for row in models if row['id'] == ids['model'] and row['available']} == set(ids['model_paths']), models
                assert all(Path(path).is_file() for path in ids['model_paths'])
                writer.stdin.write('remove-trash\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'REMOVE-TRASH'
                models = json_request(prefix + '/crawler/models')['items']
                assert all(row['id'] != ids['remove_model'] for row in models), models
                assert Path(ids['remove_path']).is_file()
                writer.stdin.write('remove-delete\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'REMOVE-DELETED'
                assert not Path(ids['remove_path']).exists()
                assert Path(ids['local_path']).is_file()

                writer.stdin.write('rank-ongoing\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'RANK-ONGOING'
                updated_books = {item['id']:item for item in json_request(prefix + '/crawler/books')['items']}
                for ident in (ids['book'], ids['late_book']):
                    assert updated_books[ident]['is_completed'] is False and updated_books[ident]['completion_source'] == 'source', updated_books[ident]
                writer.stdin.write('rank-completed\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'RANK-COMPLETED'
                updated_books = {item['id']:item for item in json_request(prefix + '/crawler/books')['items']}
                for ident in (ids['book'], ids['late_book']):
                    assert updated_books[ident]['is_completed'] is True and updated_books[ident]['completion_source'] == 'source', updated_books[ident]

                original = json_request(prefix + '/crawler/books/' + ids['book'] + '/open', method='POST', data={})
                same = json_request(prefix + '/crawler/books/' + ids['book'] + '/open', method='POST', data={})
                assert original['id'] == same['id']
                old_path = prefix + '/documents/' + original['id'] + '/versions/' + original['latest_version_id']
                assert '第一版正文' in json_request(old_path)['text']
                origin = json_request(prefix + '/crawler/documents/' + original['id'] + '/origin')
                assert origin['kind'] == 'book' and origin['author'] == '作者甲', origin
                ranking_doc = json_request(prefix + '/crawler/rankings/' + ids['snapshot'] + '/works/' + ids['work'] + '/open', method='POST', data={})
                rank_path = prefix + '/documents/' + ranking_doc['id'] + '/versions/' + ranking_doc['latest_version_id']
                assert '榜单章节原文' in json_request(rank_path)['text']
                rank_origin = json_request(prefix + '/crawler/documents/' + ranking_doc['id'] + '/origin')
                assert rank_origin['kind'] == 'ranking' and rank_origin['snapshot_id'] == ids['snapshot'], rank_origin

                writer.stdin.write('update\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'UPDATED'
                updated_book = next(item for item in json_request(prefix + '/crawler/books')['items'] if item['id'] == ids['book'])
                assert updated_book['is_completed'] is False and updated_book['completion_source'] == 'source', updated_book
                latest = json_request(prefix + '/crawler/books/' + ids['book'] + '/open', method='POST', data={})
                assert latest['id'] != original['id']
                assert '第一版正文' in json_request(old_path)['text']
                assert '第二版正文' in json_request(prefix + '/documents/' + latest['id'] + '/versions/' + latest['latest_version_id'])['text']
                latest_origin = json_request(prefix + '/crawler/documents/' + latest['id'] + '/origin')
                assert latest_origin['metadata']['is_completed'] is False and latest_origin['metadata']['completion_source'] == 'source', latest_origin
                writer.stdin.write('switch-source\n'); writer.stdin.flush()
                assert output.get(timeout=20) == 'SOURCE-SWITCHED'
                switched_books = {item['id']:item for item in json_request(prefix + '/crawler/books')['items']}
                assert switched_books[ids['book']]['is_completed'] is True and switched_books[ids['book']]['completion_source'] == 'manual', switched_books[ids['book']]
                assert switched_books[ids['late_book']]['is_completed'] is True and switched_books[ids['late_book']]['completion_source'] == 'source', switched_books[ids['late_book']]
                print('PASS: real cross-process apps; source/manual completion and channel switching, rank-first and book-first status changes, registered/retained models, both trash modes, ranking chapters and immutable live-update snapshots')
            finally:
                if writer.poll() is None:
                    try:
                        writer.stdin.write('quit\n'); writer.stdin.flush()
                        writer.wait(timeout=10)
                    except (BrokenPipeError, subprocess.TimeoutExpired):
                        writer.kill(); writer.wait(timeout=5)
                if server is not None and server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill(); server.wait(timeout=5)


if __name__ == '__main__':
    main()
