"""Real Ollama NDJSON/embedding HTTP protocol fixtures; no weights are fetched."""
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from inkreference.api import create_app
from inkreference.database import DataError
from inkreference.embedding_downloads import EmbeddingDownloads
from inkreference.models import ModelService


class Lines(httpx.SyncByteStream):
    def __init__(self, rows, *, entered=None, release=None):
        self.rows, self.entered, self.release = rows, entered, release
        self.closed = threading.Event()
    def __iter__(self):
        for index, row in enumerate(self.rows):
            if self.closed.is_set(): return
            yield (json.dumps(row) + '\n').encode()
            if index == 1 and self.entered is not None:
                self.entered.set()
                assert self.release.wait(5), 'fixture must release response'
    def close(self):
        self.closed.set()
        if self.release is not None: self.release.set()


ROWS = [{'status': 'pulling manifest'}, {'status': 'pulling aaa', 'digest': 'sha256:aaa', 'total': 1000, 'completed': 250},
        {'status': 'pulling aaa', 'digest': 'sha256:aaa', 'total': 1000, 'completed': 1000},
        {'status': 'pulling bbb', 'digest': 'sha256:bbb', 'total': 500, 'completed': 500}, {'status': 'verifying sha256 digest'}, {'status': 'success'}]


def setup(tmp_path, handler=None):
    requests = []
    def handle(request):
        requests.append(request)
        if handler is not None: return handler(request)
        if request.url.path.endswith('/pull'): return httpx.Response(200, stream=Lines(ROWS))
        return httpx.Response(200, json={'embeddings': [[1, 0, .2]], 'prompt_eval_count': 3})
    models = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    service = EmbeddingDownloads(tmp_path, models)
    return service, models, requests


def create(service, **values):
    target = service.target('embeddinggemma', values.get('profile_id'), values.get('base_url'))
    return service.create('embeddinggemma', expected_target_fingerprint=target['fingerprint'], start=False, **values)


def wait(service, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.get(identity)
        if job['status'] not in {'queued', 'running', 'verifying', 'cancelling'}: return job
        time.sleep(.01)
    pytest.fail('synthetic download did not settle')


def test_actual_ndjson_download_totals_capability_probe_and_idempotent_register(tmp_path):
    service, models, requests = setup(tmp_path)
    job = create(service)
    final = service.execute(job['id'])
    assert final['status'] == 'completed', final
    assert final['completed'] == final['total'] == 1500
    assert final['embedding_dimensions'] == 3
    assert [request.url.path for request in requests] == ['/api/pull', '/api/embed']
    assert json.loads(requests[0].content) == {'model': 'embeddinggemma', 'stream': True}
    assert json.loads(requests[1].content)['input'] == ['InkReference']
    assert models.list_profiles() == []  # Adding the connection is an explicit action.
    registered = service.register(job['id'])
    assert registered['profile']['provider'] == 'ollama' and registered['profile']['model'] == 'embeddinggemma'
    assert registered['reused'] is False
    assert service.register(job['id'])['reused'] is True
    assert len(models.list_profiles()) == 1
    usage = models.list_usage()
    assert len(usage) == 1 and usage[0]['operation'] == 'embedding_capability'
    assert usage[0]['usage']['input_tokens'] == 3


@pytest.mark.parametrize('base_url', ['https://example.com', 'http://192.168.1.2:11434', 'http://169.254.169.254', 'https://user:password@127.0.0.1', 'file:///tmp/model', 'http://127.0.0.1:11434?api_key=secret'])
def test_download_refuses_external_or_unsafe_targets_without_any_request(tmp_path, base_url):
    service, _, requests = setup(tmp_path)
    with pytest.raises(DataError): service.target('embeddinggemma', base_url=base_url)
    assert requests == []


def test_saved_openai_and_remote_ollama_profiles_are_not_download_targets(tmp_path):
    service, models, requests = setup(tmp_path)
    for provider, endpoint, mode in [('openai_compatible', 'http://127.0.0.1:1234/v1', 'local'), ('ollama', 'https://example.com', 'remote')]:
        profile = models.save_profile({'provider': provider, 'model': 'model', 'base_url': endpoint, 'connection_mode': mode})
        with pytest.raises(DataError, match='本机'): service.target('embeddinggemma', profile['id'])
    assert requests == []


def test_saved_local_credentials_reused_and_never_persisted_in_download_or_usage(tmp_path):
    service, models, requests = setup(tmp_path)
    secret = 'invented-local-ollama-secret'
    profile = models.save_profile({'provider': 'ollama', 'model': 'chat', 'base_url': 'http://127.0.0.1:11434/api', 'api_key': secret})
    job = create(service, profile_id=profile['id'])
    final = service.execute(job['id'])
    assert final['status'] == 'completed'
    assert [request.url.path for request in requests] == ['/api/pull', '/api/embed']
    assert all(request.headers['authorization'] == 'Bearer ' + secret for request in requests)
    registered = service.register(job['id'])
    assert registered['profile']['key_available'] is True
    assert secret not in json.dumps(service.list())
    assert secret not in json.dumps(models.list_usage())
    for path in tmp_path.iterdir():
        if path.is_file(): assert secret.encode() not in path.read_bytes()


def test_endpoint_change_blocks_retry_before_network(tmp_path):
    service, models, requests = setup(tmp_path)
    profile = models.save_profile({'provider': 'ollama', 'model': 'chat', 'base_url': 'http://127.0.0.1:11434'})
    job = create(service, profile_id=profile['id'])
    service.cancel(job['id'])
    models.save_profile({'base_url': 'http://127.0.0.1:11435'}, profile['id'])
    with pytest.raises(DataError, match='变化'): service.retry(job['id'])
    assert requests == []


def test_actual_streamed_partial_progress_cancel_and_retry(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def handle(request):
        if request.url.path.endswith('/pull'):
            calls.append(1)
            return httpx.Response(200, stream=Lines(ROWS, entered=entered if len(calls) == 1 else None, release=release))
        return httpx.Response(200, json={'embeddings': [[1, 0]]})
    service, models, _ = setup(tmp_path, handle)
    target = service.target('embeddinggemma')
    job = service.create('embeddinggemma', expected_target_fingerprint=target['fingerprint'])
    try:
        assert entered.wait(5)
        partial = service.get(job['id'])
        assert partial['completed'] == 250 and partial['total'] == 1000
        cancelled = service.cancel(job['id'])
        assert cancelled['status'] in {'cancelling', 'cancelled'}
        assert wait(service, job['id'])['status'] == 'cancelled'
        retried = service.retry(job['id'])
        final = wait(service, job['id'])
        assert final['status'] == 'completed' and final['attempt'] == 2
        assert len(calls) == 2
    finally:
        release.set(); service.shutdown()


@pytest.mark.parametrize('response_rows,error', [
    ([{'status': 'pulling manifest'}], '提前结束'),
    ([{'error': 'server secret should never surface'}], '无法下载'),
    ([{'status': 'pulling', 'digest': 'one', 'total': 5, 'completed': 6}], '字节'),
])
def test_bad_or_truncated_ndjson_is_not_success_and_raw_errors_are_hidden(tmp_path, response_rows, error):
    service, models, requests = setup(tmp_path, lambda request: httpx.Response(200, stream=Lines(response_rows)))
    final = service.execute(create(service)['id'])
    assert final['status'] == 'failed' and error in final['error']
    assert 'server secret' not in json.dumps(final)
    assert len(requests) == 1
    with pytest.raises(DataError): service.register(final['id'])


def test_chat_only_model_is_not_registered_as_embedding(tmp_path):
    def handle(request):
        if request.url.path.endswith('/pull'): return httpx.Response(200, stream=Lines(ROWS))
        return httpx.Response(400, json={'error': 'this is not an embedding model'})
    service, models, _ = setup(tmp_path, handle)
    final = service.execute(create(service)['id'])
    assert final['status'] == 'failed' and '不支持向量' in final['error']
    assert models.list_profiles() == []
    assert models.list_usage()[0]['status'] == 'error'


def test_restart_marks_interrupted_download_retryable(tmp_path):
    service, models, _ = setup(tmp_path)
    job = create(service)
    recovered = EmbeddingDownloads(tmp_path, models)
    assert recovered.get(job['id'])['status'] == 'paused'
    recovered.cancel(job['id'])  # Already paused is a stable no-op.
    assert recovered.get(job['id'])['status'] == 'paused'


def test_real_http_download_api_is_lazy_then_downloads_and_registers(tmp_path):
    app = create_app(tmp_path)
    assert app.state.runtime._studio is None
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/api/health').status_code == 200
        assert app.state.runtime._studio is None
        runtime = app.state.runtime
        runtime.studio.models.transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Lines(ROWS)) if request.url.path.endswith('/pull') else httpx.Response(200, json={'embeddings': [[1, 0]]}))
        target = client.get('/api/studio/embedding-downloads/targets', params={'model': 'embeddinggemma'})
        assert target.status_code == 200, target.text
        created = client.post('/api/studio/embedding-downloads/jobs', json={'model': 'embeddinggemma', 'expected_target_fingerprint': target.json()['fingerprint']})
        assert created.status_code == 201, created.text
        final = wait(runtime.studio.embedding_downloads, created.json()['id'])
        assert final['status'] == 'completed'
        response = client.post('/api/studio/embedding-downloads/jobs/' + final['id'] + '/register')
        assert response.status_code == 200, response.text
        assert response.json()['profile']['model'] == 'embeddinggemma'


def test_completed_download_metadata_history_is_bounded_without_weight_operations(tmp_path):
    service, _, requests = setup(tmp_path)
    for _ in range(54):
        job = create(service)
        assert service.cancel(job['id'])['status'] == 'cancelled'
    assert len(service.list()) == 50
    assert requests == []
