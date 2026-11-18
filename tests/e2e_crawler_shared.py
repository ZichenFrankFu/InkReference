"""Exercise the real shared-data UI with a synthetic local crawler workspace.

Run after `npm run build` from frontend, using a Python with Playwright:
  BROWSER_EXECUTABLE=/usr/bin/chromium INKREFERENCE_PYTHON=/path/to/ref/python \
    python tests/e2e_crawler_shared.py

The server uses InkReference's own environment; no public network is contacted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SNAP = 'b' * 64
REFERENCE_PYTHON = os.environ.get('INKREFERENCE_PYTHON', '/workspace/.onboarding/envs/InkReference/bin/python')


def create_fixture(folder: Path) -> Path:
    """Reuse the unit fixture and extend it to cover complete pagination."""
    crawler = folder / 'crawler'
    setup = ("import runpy,sys; from pathlib import Path; "
             "runpy.run_path('tests/test_crawler_bridge.py')['workspace'](Path(sys.argv[1]))")
    subprocess.run([REFERENCE_PYTHON, '-c', setup, str(crawler)], cwd=ROOT, check=True)
    database = crawler / 'inkcrawler.sqlite3'
    model_id = '9' * 32
    file = crawler / 'source-downloads' / model_id / 'weights' / 'model.gguf'
    file.parent.mkdir(parents=True)
    file.write_bytes(b'synthetic model file, never executed')
    result = {'relative_path': 'weights/model.gguf', 'bytes': file.stat().st_size,
              'mtime_ns': file.stat().st_mtime_ns, 'sha256': 'synthetic'}
    with sqlite3.connect(database) as db:
        for n in range(1, 51):
            ident = f'{n:032x}'
            db.execute('INSERT INTO books VALUES (?,?,?,?,?)', (ident, f'共享作品{n:02}', '合成作者', '2026-01-01', 0))
            db.execute('INSERT INTO chapters VALUES (?,?,?,?,?,?,?)', (f'{n+100:032x}', ident, 0, '第一章', '共享正文', 'done', 4))
            work, observation, chapter = f'{n+1000:064x}', f'{n+1000:032x}', f'{n+2000:064x}'
            rank = 1 if n == 1 else n + 1
            meta = json.dumps({'title': f'历史作品{rank:02}', 'author': '合成作者'}, ensure_ascii=False)
            db.execute('INSERT INTO ranking_works VALUES (?,?,?,?)', (work, 'qidian', str(1000+n), meta))
            db.execute('INSERT INTO ranking_metadata_versions VALUES (?,?,?)', (work, 'v1', meta))
            db.execute('INSERT INTO ranking_observations VALUES (?,?,?,?,?,?,?,?)',
                       (observation, work, SNAP, 'v1', json.dumps({'rank':rank,'_stored_chapter_count':1}), n, 0, 1))
            db.execute('INSERT INTO ranking_chapters VALUES (?,?,?)',
                       (chapter, work, json.dumps({'chapter_num':1,'title':'第一章','content':'榜单正文'}, ensure_ascii=False)))
            db.execute('INSERT INTO ranking_observation_chapters VALUES (?,?,?)', (observation, 0, chapter))
        db.execute('INSERT INTO source_downloads VALUES (?,?,?,?,?)', (model_id, 'model_huggingface', 'completed', '{}', 2))
        db.execute('INSERT INTO source_files VALUES (?,?,?,?)', (model_id, 'safe-key', json.dumps(result), 0))
    return crawler


SERVER = r'''
import asyncio,sys
from pathlib import Path
import uvicorn
from inkreference.api import create_app
app = create_app(Path(sys.argv[1]), Path(sys.argv[2]))
@app.middleware('http')
async def delay_fixture_ranking(request, call_next):
    # Let the browser switch tabs before an older ranking request resolves.
    if (request.url.path.startswith('/api/studio/crawler/rankings/') or
            request.url.path.startswith('/api/studio/crawler/books/') and request.method == 'POST'):
        await asyncio.sleep(.7)
    return await call_next(request)
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[3]), log_level='error')
'''


def main():
    if not (ROOT / 'frontend/dist/index.html').is_file():
        raise RuntimeError('请先在 frontend 运行 npm run build')
    if not Path(REFERENCE_PYTHON).is_file():
        raise RuntimeError('请设置 INKREFERENCE_PYTHON 为 InkReference 的 Python')
    with tempfile.TemporaryDirectory(prefix='inkreference-crawler-e2e-') as temporary:
        root = Path(temporary)
        crawler = create_fixture(root)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        proc = subprocess.Popen([REFERENCE_PYTHON, '-c', SERVER, str(root / 'reference'),
                                 str(ROOT / 'frontend/dist'), str(port)], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            url = f'http://127.0.0.1:{port}/'
            while True:
                if proc.poll() is not None:
                    raise RuntimeError('InkReference fixture server exited during startup')
                try:
                    with urllib.request.urlopen(url, timeout=1):
                        break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() > deadline:
                        raise RuntimeError('InkReference fixture server did not start')
                    time.sleep(.05)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BROWSER_EXECUTABLE', '/usr/bin/chromium'), args=['--no-sandbox'])
                try:
                    page = browser.new_page(viewport={'width': 1280, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(url + '#documents', wait_until='networkidle')
                    page.get_by_role('button', name='InkCrawler', exact=True).click()
                    panel = page.locator('.crawler-shared')
                    path = panel.get_by_label('InkCrawler 数据目录')
                    path.fill(str(root / 'missing'))
                    panel.get_by_role('button', name='连接', exact=True).click()
                    expect(panel.get_by_text('未找到 InkCrawler 数据库', exact=False)).to_be_visible()
                    path.fill(str(crawler))
                    panel.get_by_role('button', name='连接', exact=True).click()
                    expect(panel.get_by_text('共享作品01')).to_be_visible()
                    expect(panel.get_by_text('第 1 页')).to_be_visible()
                    panel.get_by_role('button', name='下一页').click()
                    expect(panel.get_by_text('测试作品', exact=True)).to_be_visible()
                    expect(panel.get_by_text('第 2 页')).to_be_visible()
                    panel.locator('article').filter(has_text='测试作品').get_by_role('button', name='打开分析').click()
                    expect(page.get_by_text('原始正文', exact=False).first).to_be_visible(timeout=15000)

                    page.get_by_role('button', name='InkCrawler', exact=True).click()
                    panel.get_by_role('tab', name='榜单', exact=True).click()
                    expect(panel.get_by_text('月票榜', exact=False)).to_be_visible()
                    panel.locator('article').filter(has_text='月票榜').get_by_role('button', name='查看').click()
                    expect(panel.get_by_text('旧书名', exact=False)).to_be_visible()
                    expect(panel.get_by_text('历史作品50', exact=False)).to_be_visible()
                    panel.get_by_role('button', name='下一页').click()
                    expect(panel.get_by_text('历史作品51', exact=False)).to_be_visible()
                    panel.get_by_role('button', name='上一页').click()
                    expect(panel.get_by_text('旧书名', exact=False)).to_be_visible()
                    panel.locator('article').filter(has_text='旧书名').get_by_role('button', name='打开分析').click()
                    expect(page.get_by_text('榜单旧正文', exact=False).first).to_be_visible(timeout=15000)

                    page.get_by_role('button', name='InkCrawler', exact=True).click()
                    panel.get_by_role('tab', name='榜单', exact=True).click()
                    expect(panel.get_by_text('月票榜', exact=False)).to_be_visible()
                    panel.locator('article').filter(has_text='月票榜').get_by_role('button', name='查看').click()
                    panel.get_by_role('tab', name='模型', exact=True).click()
                    expect(panel.get_by_text('weights/model.gguf', exact=True)).to_be_visible()
                    page.wait_for_timeout(1000)
                    expect(panel.get_by_role('button', name='返回榜单')).to_have_count(0)
                    expect(panel.get_by_text('weights/model.gguf', exact=True)).to_be_visible()
                    panel.get_by_role('tab', name='作品', exact=True).click()
                    expect(panel.get_by_text('共享作品01')).to_be_visible()
                    panel.locator('article').filter(has_text='共享作品01').get_by_role('button', name='打开分析').click()
                    panel.get_by_role('tab', name='模型', exact=True).click()
                    expect(panel.get_by_text('weights/model.gguf', exact=True)).to_be_visible()
                    page.wait_for_timeout(1000)
                    expect(panel.get_by_text('weights/model.gguf', exact=True)).to_be_visible()
                    panel.get_by_role('tab', name='榜单', exact=True).click()
                    expect(panel.get_by_text('月票榜', exact=False)).to_be_visible()
                    panel.locator('article').filter(has_text='月票榜').get_by_role('button', name='查看').click()
                    expect(panel.get_by_text('旧书名', exact=False)).to_be_visible()
                    panel.locator('article').filter(has_text='旧书名').get_by_role('button', name='打开分析').click()
                    panel.get_by_role('tab', name='模型', exact=True).click()
                    expect(panel.get_by_text('weights/model.gguf', exact=True)).to_be_visible()
                    page.wait_for_timeout(1000)
                    expect(panel.get_by_text('weights/model.gguf', exact=True)).to_be_visible()
                    assert not errors, errors
                finally:
                    browser.close()
            with sqlite3.connect(crawler / 'inkcrawler.sqlite3') as db:
                assert db.execute("SELECT content FROM chapters WHERE id=?", ('1'*32,)).fetchone()[0] == '原始正文'
            print('PASS: shared workspace, snapshots, pagination, model, stale responses, read-only source')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == '__main__':
    main()
