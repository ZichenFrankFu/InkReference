"""Local Ollama embedding downloads with real streamed byte counts.

Implements Ollama's POST /api/pull NDJSON protocol, followed by a small /api/embed
capability check. Weights stay in Ollama's model directory; the app persists only
bounded task metadata, never credentials or a copy of the weights.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .database import DataError
from .models import _validate_profile, _endpoint_binding, _usage, _cost


def now(): return datetime.now(timezone.utc).isoformat()
def dump(value): return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
ACTIVE = {'queued', 'running', 'cancelling', 'verifying'}


class EmbeddingDownloads:
    def __init__(self, data_dir: str | Path, models):
        self.path = Path(data_dir) / 'workbench.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.models = models
        self.lock = threading.RLock()
        self.workers: dict[str, threading.Thread] = {}
        self.stops: dict[str, threading.Event] = {}
        self.responses: dict[str, httpx.Response] = {}
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS embedding_downloads (id TEXT PRIMARY KEY,data_json TEXT NOT NULL)')
            for identity, raw in db.execute('SELECT id,data_json FROM embedding_downloads').fetchall():
                job = json.loads(raw)
                if job['status'] in ACTIVE:
                    job.update(status='paused', stage='下载已中断', updated_at=now())
                    db.execute('UPDATE embedding_downloads SET data_json=? WHERE id=?', (dump(job), identity))

    @contextlib.contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            try:
                with db: yield db
            finally: db.close()

    def target(self, model: str, profile_id: str | None = None, base_url: str | None = None) -> dict:
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,180}(?::[A-Za-z0-9._-]{1,40})?', model) or '..' in model or '//' in model:
            raise DataError(422, '请填写有效的 Ollama 模型名称')
        config = self.models.snapshot(profile_id) if profile_id else _validate_profile({'id': 'embedding-download-local', 'provider': 'ollama',
            'model': model, 'base_url': base_url or 'http://127.0.0.1:11434', 'connection_mode': 'local', 'credential_mode': 'session'})
        # Revalidate older saved profiles against today's local transport rules.
        config = _validate_profile(config)
        if config['provider'] != 'ollama' or config['connection_mode'] != 'local':
            raise DataError(422, '模型下载仅支持本机 Ollama 连接')
        if profile_id and base_url is not None and base_url.rstrip('/') != config['base_url']:
            raise DataError(409, '已保存连接的地址已变化，请重新选择')
        binding = _endpoint_binding(config)
        fingerprint = hashlib.sha256(dump([binding, model]).encode()).hexdigest()
        return {'profile_id': profile_id, 'model': model, 'endpoint': config['base_url'], 'fingerprint': fingerprint}

    def _config(self, job):
        target = self.target(job['model'], job['profile_id'], None if job['profile_id'] else job['endpoint'])
        if target['fingerprint'] != job['target_fingerprint']: raise DataError(409, 'Ollama 连接已变化，请重新创建下载')
        if job['profile_id']:
            config = self.models.snapshot(job['profile_id'])
            key = self.models._key(job['profile_id'], config)
        else:
            config = _validate_profile({'id': 'embedding-download-local', 'provider': 'ollama', 'model': job['model'],
                'base_url': job['endpoint'], 'connection_mode': 'local', 'credential_mode': 'session'})
            key = None
        return config, key

    def list(self):
        with self.connection() as db:
            return sorted((json.loads(row[0]) for row in db.execute('SELECT data_json FROM embedding_downloads')), key=lambda job: job['created_at'], reverse=True)

    def get(self, identity):
        with self.connection() as db:
            row = db.execute('SELECT data_json FROM embedding_downloads WHERE id=?', (identity,)).fetchone()
        if row is None: raise DataError(404, '模型下载任务不存在')
        return json.loads(row[0])

    def _put(self, job):
        job['updated_at'] = now()
        with self.connection() as db:
            db.execute('INSERT INTO embedding_downloads VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json', (job['id'], dump(job)))

    def create(self, model, *, profile_id=None, base_url=None, expected_target_fingerprint=None, start=True):
        target = self.target(model, profile_id, base_url)
        if expected_target_fingerprint != target['fingerprint']: raise DataError(409, '下载目标已变化，请刷新后重试')
        with self.lock:
            if self.has_active_runs(): raise DataError(409, '请先完成或取消当前模型下载')
            job = {'id': uuid4().hex, 'profile_id': profile_id, 'endpoint': target['endpoint'], 'model': model,
                   'target_fingerprint': target['fingerprint'], 'status': 'queued', 'stage': '等待下载', 'completed': 0,
                   'total': None, 'layers': {}, 'embedding_dimensions': None, 'error': None, 'registered_profile_id': None,
                   'created_at': now(), 'updated_at': now(), 'attempt': 1}
            self._put(job)
            # Bounded completed history; active jobs and downloaded files are not deleted.
            old = [item for item in self.list() if item['status'] not in ACTIVE]
            with self.connection() as db:
                db.executemany('DELETE FROM embedding_downloads WHERE id=?', [(item['id'],) for item in old[49:]])
            if start: self._start(job['id'])
            return self.get(job['id'])

    def _start(self, identity):
        with self.lock:
            self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
            self.stops = {key: stop for key, stop in self.stops.items() if key in self.workers}
            stop = threading.Event(); self.stops[identity] = stop
            thread = threading.Thread(target=self.execute, args=(identity,), name='embedding-download-' + identity[:8], daemon=True)
            self.workers[identity] = thread
            thread.start()

    @staticmethod
    def _stage(status):
        if status.startswith('pulling manifest'): return '读取模型清单'
        if status.startswith(('pulling ', 'downloading ')): return '下载模型文件'
        if 'verif' in status: return '校验模型文件'
        if 'manifest' in status: return '写入模型清单'
        if status == 'success': return '下载完成'
        return '处理模型文件'

    def _check(self, identity):
        if self.stops.setdefault(identity, threading.Event()).is_set(): raise InterruptedError

    def execute(self, identity):
        job = self.get(identity)
        config = key = None
        try:
            self._check(identity)
            config, key = self._config(job)
            headers = {'Authorization': 'Bearer ' + key} if key else {}
            path = '/pull' if config['base_url'].endswith('/api') else '/api/pull'
            job.update(status='running', stage='连接 Ollama', error=None); self._put(job)
            success = False
            # Bounded reads make cancellation responsive when a server stalls.
            with httpx.Client(transport=self.models.transport, timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False) as client:
                with client.stream('POST', config['base_url'] + path, json={'model': job['model'], 'stream': True}, headers=headers) as response:
                    with self.lock: self.responses[identity] = response
                    if not 200 <= response.status_code < 300: raise DataError(502, f'Ollama 下载返回 HTTP {response.status_code}')
                    buffer = b''
                    last_write, last_phase = 0.0, None
                    for chunk in response.iter_bytes():
                        self._check(identity)
                        buffer += chunk
                        if len(buffer) > 256000 and b'\n' not in buffer: raise DataError(502, 'Ollama 下载进度格式无效')
                        while b'\n' in buffer:
                            self._check(identity)
                            line, buffer = buffer.split(b'\n', 1)
                            if not line.strip(): continue
                            success = self._progress(job, line) or success
                        phase = (job['stage'], job['total'])
                        if phase != last_phase or time.monotonic() - last_write >= .25 or success:
                            self._check(identity)
                            self._put(job); last_write, last_phase = time.monotonic(), phase
                    if buffer.strip(): success = self._progress(job, buffer) or success
                with self.lock: self.responses.pop(identity, None)
                self._check(identity)
                if not success: raise DataError(502, 'Ollama 下载连接提前结束，请重试')
                job.update(status='verifying', stage='验证向量模型'); self._put(job)
                self._verify(client, job, config, key, headers)
            self._check(identity)
            job.update(status='completed', stage='已就绪', error=None)
        except InterruptedError:
            job.update(status='cancelled', stage='已取消', error=None)
        except DataError as exc:
            job.update(status='failed', stage='下载失败', error=exc.detail if isinstance(exc.detail, str) else '模型下载失败')
        except httpx.TimeoutException:
            job.update(status='failed', stage='连接超时', error='Ollama 下载响应超时，请检查本机服务后重试')
        except httpx.HTTPError:
            job.update(status='failed', stage='连接失败', error='无法连接本机 Ollama，请检查服务后重试')
        except Exception:
            job.update(status='failed', stage='下载失败', error='Ollama 返回了无效的下载进度')
        finally:
            with self.lock:
                self.responses.pop(identity, None)
                if self.stops.get(identity, threading.Event()).is_set(): job.update(status='cancelled', stage='已取消', error=None)
            self._put(job)
        return self.get(identity)

    def _progress(self, job, line):
        if len(line) > 256000: raise DataError(502, 'Ollama 下载进度过大')
        data = json.loads(line)
        if not isinstance(data, dict) or data.get('error'): raise DataError(502, 'Ollama 无法下载此模型，请检查名称及服务日志')
        status = data.get('status')
        if not isinstance(status, str): raise DataError(502, 'Ollama 下载进度缺少状态')
        job['stage'] = self._stage(status)
        digest = data.get('digest')
        if digest is not None:
            if not isinstance(digest, str) or len(digest) > 200 or len(job['layers']) >= 128 and hashlib.sha256(digest.encode()).hexdigest() not in job['layers']: raise DataError(502, 'Ollama 模型文件清单无效')
            total, completed = data.get('total'), data.get('completed', 0)
            if type(total) is not int or not 0 <= total <= 10**14 or type(completed) is not int or not 0 <= completed <= total: raise DataError(502, 'Ollama 下载字节统计无效')
            digest = hashlib.sha256(digest.encode()).hexdigest()
            previous = job['layers'].get(digest, {})
            if previous and previous['total'] != total: raise DataError(502, 'Ollama 同一模型文件的大小发生变化')
            job['layers'][digest] = {'total': total, 'completed': max(previous.get('completed', 0), completed)}
            job['total'] = sum(layer['total'] for layer in job['layers'].values())
            job['completed'] = sum(layer['completed'] for layer in job['layers'].values())
        return status == 'success'

    def _verify(self, client, job, config, key, headers):
        path = '/embed' if config['base_url'].endswith('/api') else '/api/embed'
        started = time.monotonic()
        billing = {**config, 'model': job['model']}
        record = {'id': uuid4().hex, 'run_id': 'embedding-download-' + job['id'], 'chunk_id': 'embedding-capability',
                  'profile_id': job['profile_id'] or config['id'], 'provider': 'ollama', 'model': job['model'], 'attempt': job['attempt'],
                  'status': 'pending', 'http_status': None, 'started_at': now(), 'duration_ms': None,
                  'usage': _usage(None, 0, len('InkReference')), 'error': None, 'operation': 'embedding_capability',
                  'profile_snapshot': billing, 'request_sha256': hashlib.sha256(b'InkReference').hexdigest()}
        try:
            response = client.post(config['base_url'] + path, json={'model': job['model'], 'input': ['InkReference'], 'truncate': False}, headers=headers)
            record['http_status'] = response.status_code
            if len(response.content) > 8000000: raise DataError(502, '向量模型验证响应过大')
            try: data = response.json()
            except ValueError: data = None
            # A received response may already have consumed tokens even if the
            # user cancelled while it was in flight. Account before stopping.
            if isinstance(data, dict):
                count = data.get('prompt_eval_count')
                if type(count) is int and count >= 0: record['usage'] = _usage(count, 0, len('InkReference'))
            self._check(job['id'])
            if not 200 <= response.status_code < 300: raise DataError(502, '模型已下载，但不支持向量生成；请更换 embedding 模型')
            if not isinstance(data, dict): raise DataError(502, '模型已下载，但向量验证响应格式无效')
            vectors = data.get('embeddings')
            if not isinstance(vectors, list) or len(vectors) != 1 or not isinstance(vectors[0], list) or not 1 <= len(vectors[0]) <= 65536 or any(type(x) not in (int, float) or not math.isfinite(x) for x in vectors[0]) or not any(vectors[0]):
                raise DataError(502, '模型已下载，但未返回有效向量')
            job['embedding_dimensions'] = len(vectors[0])
            count = data.get('prompt_eval_count')
            record['usage'] = _usage(count if type(count) is int and count >= 0 else None, 0, len('InkReference'))
            record['status'] = 'success'
        finally:
            record['cost'] = _cost(billing, record['usage'])
            record['duration_ms'] = round((time.monotonic() - started) * 1000, 3)
            if record['status'] == 'pending': record.update(status='error', error='embedding_capability_failed')
            self.models._write_call(record)

    def cancel(self, identity):
        with self.lock:
            job = self.get(identity)
            if job['status'] not in ACTIVE: return job
            self.stops.setdefault(identity, threading.Event()).set()
            job.update(status='cancelling', stage='正在取消'); self._put(job)
            response = self.responses.get(identity)
        if response is not None:
            try: response.close()
            except Exception: pass
        if identity not in self.workers or not self.workers[identity].is_alive():
            job.update(status='cancelled', stage='已取消'); self._put(job)
        return self.get(identity)

    def retry(self, identity):
        with self.lock:
            job = self.get(identity)
            if job['status'] not in {'failed', 'cancelled', 'paused'}: raise DataError(409, '此下载任务不能重试')
            if self.has_active_runs(): raise DataError(409, '请先完成或取消当前模型下载')
            self._config(job)
            job.update(status='queued', stage='等待重试', error=None, layers={}, total=None, completed=0, attempt=job['attempt'] + 1)
            self._put(job); self._start(identity)
            return self.get(identity)

    def register(self, identity):
        with self.lock:
            job = self.get(identity)
            if job['status'] != 'completed' or not job['embedding_dimensions']: raise DataError(409, '请先完成向量模型下载及验证')
            config, key = self._config(job)
            for profile in self.models.list_profiles():
                if profile['provider'] == 'ollama' and profile['base_url'] == job['endpoint'] and profile['model'] == job['model']:
                    job['registered_profile_id'] = profile['id']; self._put(job)
                    return {'job': job, 'profile': profile, 'reused': True}
            values = {**config, 'id': 'embedding-' + uuid4().hex, 'name': 'Embedding · ' + job['model'], 'model': job['model'],
                      'context_window': 8192, 'max_output_tokens': 1, 'input_price_per_million': None, 'output_price_per_million': None}
            # Reuse the selected connection's credential source without persisting keys.
            if key and values['credential_mode'] in {'session', 'vault'}: values['api_key'] = key
            profile = self.models.save_profile(values)
            job['registered_profile_id'] = profile['id']; self._put(job)
            return {'job': job, 'profile': profile, 'reused': False}

    def active_threads(self):
        with self.lock: return [thread for thread in self.workers.values() if thread.is_alive()]

    def has_active_runs(self):
        return any(job['status'] in ACTIVE for job in self.list())

    def shutdown(self):
        for job in self.list():
            if job['status'] in ACTIVE: self.cancel(job['id'])
        for thread in self.active_threads(): thread.join(timeout=2)
