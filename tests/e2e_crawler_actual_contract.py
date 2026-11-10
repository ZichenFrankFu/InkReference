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
from inkcrawler.app import create_app
from inkcrawler.providers.models import DownloadRequest,Page
from inkcrawler.providers.model_sources import model_entry

root = Path(sys.argv[1])
app = create_app(root, testing=True)
store, service = app.state.store, app.state.source_downloads
book = store.create_book('实时共享作品', '作者甲', 'https://books.example.org/book/123/', {}, [
    {'title':'第一章 原始标题','url':'https://books.example.org/book/123/1'}])
chapter = store.list_chapters(book['id'])[0]
store.update_chapter(chapter['id'], state='done', content='第一版正文，来自真实爬虫数据库。')
store.update_book(book['id'], is_completed=True)

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

print('READY '+json.dumps({'book':book['id'],'chapter':chapter['id'],'snapshot':snapshot,
      'work':work,'model':model,'model_paths':model_paths}, ensure_ascii=False), flush=True)
for line in sys.stdin:
    if line.strip() == 'update':
        store.update_chapter(chapter['id'], state='done', content='第二版正文，爬虫继续保存。')
        print('UPDATED', flush=True)
    elif line.strip() == 'quit':
        break
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
                snapshots = json_request(prefix + '/crawler/rankings')['items']
                snapshot = next(item for item in snapshots if item['id'] == ids['snapshot'])
                assert snapshot['rank_family'] == '月票榜' and snapshot['works'] == 1, snapshot
                ranking = json_request(prefix + '/crawler/rankings/' + ids['snapshot'])['items'][0]
                assert ranking['work_id'] == ids['work'] and ranking['metadata']['title'] == '真实榜单作品'
                assert ranking['metadata']['author'] == '榜单作者' and ranking['chapter_count'] == 1
                models = json_request(prefix + '/crawler/models')['items']
                actual_paths = {row['path'] for row in models if row['id'] == ids['model'] and row['available']}
                assert actual_paths == set(ids['model_paths']), (models, ids['model_paths'])

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
                latest = json_request(prefix + '/crawler/books/' + ids['book'] + '/open', method='POST', data={})
                assert latest['id'] != original['id']
                assert '第一版正文' in json_request(old_path)['text']
                assert '第二版正文' in json_request(prefix + '/documents/' + latest['id'] + '/versions/' + latest['latest_version_id'])['text']
                print('PASS: real InkCrawler Store/DownloadStore with live Reference HTTP; immutable snapshots, ranking chapters, nested multi-file model paths and live updates')
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
