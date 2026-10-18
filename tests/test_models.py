"""No real provider calls: HTTPX MockTransport exercises wire and ledger paths."""

import json
import sqlite3

import httpx
import pytest

from inkreference.database import DataError
from inkreference.models import ModelService, estimate_messages


MESSAGES = [{"role": "system", "content": "抽取事实"}, {"role": "user", "content": "中文章节与线索"}]


def profile(service, **overrides):
    return service.save_profile({"provider": "openai_compatible", "model": "synthetic-model", "base_url": "http://127.0.0.1:1234/v1", **overrides})


def completion(content='{"events":[]}', usage=None, **extra):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], **({"usage": usage} if usage is not None else {}), **extra})


def invoke(service, configured, **kwargs):
    return service.invoke(configured["id"], MESSAGES, run_id="run-1", chunk_id="chunk-1", **kwargs)


def test_profiles_persist_configuration_but_never_session_keys(tmp_path):
    service = ModelService(tmp_path)
    configured = profile(service, api_key="secret-unique-session-key-12345")
    assert configured["has_session_key"] is True
    assert configured["key_available"] is True
    assert "api_key" not in configured
    assert "secret-unique-session-key-12345" not in json.dumps(service.snapshot(configured["id"]))
    restarted = ModelService(tmp_path)
    assert restarted.list_profiles()[0]["has_session_key"] is False
    assert restarted.get_profile(configured["id"])["key_available"] is False
    for file in tmp_path.iterdir():
        if file.is_file():
            assert b"secret-unique-session-key-12345" not in file.read_bytes()
    assert service.delete_profile(configured["id"]) == {"deleted": 1}
    with pytest.raises(DataError) as missing:
        service.get_profile(configured["id"])
    assert missing.value.status_code == 404


def test_openai_compatible_wire_actual_counts_and_configured_cost(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return completion(usage={"prompt_tokens": 150, "completion_tokens": 50})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, api_key="key-test", input_price_per_million=2, output_price_per_million=4, currency="USD")
    result = invoke(service, configured, options={"temperature": 0.1, "response_format": {"type": "json_object"}})
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "http://127.0.0.1:1234/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer key-test"
    body = json.loads(request.content)
    assert body["stream"] is False
    assert body["max_tokens"] == 2048
    assert body["messages"] == MESSAGES
    assert body["response_format"] == {"type": "json_object"}
    assert result["usage"]["source"] == "actual"
    assert result["usage"]["input_tokens"] == 150
    assert result["cost"] == {"amount": .0005, "currency": "USD", "source": "estimated", "token_source": "actual"}
    ledger = service.list_usage("run-1")
    assert len(ledger) == 1
    assert ledger[0]["status"] == "success"
    assert ledger[0]["attempt"] == 1
    assert ledger[0]["duration_ms"] >= 0
    assert len(ledger[0]["request_sha256"]) == 64
    assert service.list_usage("other") == []
    assert "中文章节与线索" not in json.dumps(ledger, ensure_ascii=False)


def test_native_ollama_sends_context_and_gets_actual_counts(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "本地摘要"}, "done": True, "done_reason": "stop", "prompt_eval_count": 80, "eval_count": 12})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, provider="ollama", base_url="http://127.0.0.1:11434", model="qwen:local", context_window=16384)
    result = invoke(service, configured, options={"max_output_tokens": 100, "response_format": {"type": "json_object"}})
    assert str(requests[0].url) == "http://127.0.0.1:11434/api/chat"
    assert "Authorization" not in requests[0].headers
    body = json.loads(requests[0].content)
    assert body["stream"] is False
    assert body["options"] == {"num_predict": 100, "num_ctx": 16384}
    assert body["format"] == "json"
    assert result["usage"]["input_tokens"] == 80
    assert result["usage"]["output_tokens"] == 12
    assert result["usage"]["source"] == "actual"
    assert result["cost"]["amount"] is None


def test_missing_usage_is_explicit_estimate_not_zero_actual(tmp_path):
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: completion("中文结果")))
    result = invoke(service, profile(service))
    assert result["usage"]["input_tokens"] == estimate_messages(MESSAGES)["input_tokens"]
    assert result["usage"]["output_tokens"] == len("中文结果".encode("utf-8"))
    assert result["usage"]["source"] == "estimated"
    assert result["usage"]["estimate_method"] == "utf8_bytes_with_chat_margin"
    assert result["cost"]["amount"] is None
    summary = service.usage_summary()
    assert summary["actual_input_tokens"] == 0
    assert summary["estimated_output_tokens"] > 0
    assert summary["unpriced_calls"] == 1


def test_missing_one_count_is_mixed_and_actual_zero_is_preserved(tmp_path):
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: completion("OK", usage={"prompt_tokens": 0})))
    result = invoke(service, profile(service))
    assert result["usage"]["input_tokens"] == 0
    assert result["usage"]["input_source"] == "actual"
    assert result["usage"]["output_source"] == "estimated"
    assert result["usage"]["source"] == "mixed"


def test_each_retry_and_failure_is_separately_ledgered_without_secrets(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, json={"error": {"message": "echo secret-never-output-987"}})
        return completion(usage={"prompt_tokens": 30, "completion_tokens": 5})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, api_key="secret-never-output-987")
    with pytest.raises(DataError) as failed:
        invoke(service, configured)
    assert failed.value.status_code == 502
    assert "secret" not in str(failed.value)
    invoke(service, configured, attempt=2)
    ledger = service.list_usage()
    assert len(requests) == len(ledger) == 2
    assert [row["attempt"] for row in ledger] == [1, 2]
    assert ledger[0]["status"] == "error"
    assert ledger[0]["http_status"] == 429
    assert ledger[0]["usage"]["output_tokens"] is None
    assert ledger[0]["usage"]["output_source"] == "unknown"
    assert "secret-never-output-987" not in json.dumps(ledger)
    summary = ModelService(tmp_path).usage_summary("run-1")
    assert summary["failed_calls"] == summary["successful_calls"] == 1
    assert summary["unknown_output_calls"] == 1
    assert summary["actual_input_tokens"] == 30


def test_timeout_ledger_stays_unknown_and_raw_exception_is_never_returned(tmp_path):
    def fail(request):
        raise httpx.ReadTimeout("secret-token https://user:secret-token@host", request=request)
    service = ModelService(tmp_path, transport=httpx.MockTransport(fail))
    configured = profile(service)
    with pytest.raises(DataError) as result:
        invoke(service, configured)
    assert result.value.status_code == 504
    assert "secret-token" not in str(result.value)
    row = service.list_usage()[0]
    assert row["error"] == "timeout"
    assert row["usage"]["output_source"] == "unknown"
    assert "secret-token" not in json.dumps(row)


def test_invalid_response_keeps_provider_usage_if_available(tmp_path):
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"usage": {"prompt_tokens": 12, "completion_tokens": 4}, "choices": []})))
    with pytest.raises(DataError) as result:
        invoke(service, profile(service))
    assert result.value.status_code == 502
    row = service.list_usage()[0]
    assert row["status"] == "error"
    assert row["usage"]["input_tokens"] == 12
    assert row["usage"]["output_tokens"] == 4
    assert row["usage"]["source"] == "actual"


def test_frozen_snapshot_uses_original_model_address_and_prices(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return completion(usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, model="before", input_price_per_million=2, output_price_per_million=4, currency="USD")
    snapshot = service.snapshot(configured["id"])
    service.save_profile({"model": "after", "base_url": "http://different.local/v1", "input_price_per_million": 999}, configured["id"])
    result = invoke(service, configured, profile_snapshot=snapshot)
    assert json.loads(requests[0].content)["model"] == "before"
    assert requests[0].url.host == "127.0.0.1"
    assert result["cost"]["amount"] == 2


def test_env_key_is_loaded_at_call_time_not_saved_and_missing_key_prevents_request(tmp_path, monkeypatch):
    requests = []
    def handle(request):
        requests.append(request)
        return completion()
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, key_env="INKREFERENCE_TEST_API_KEY")
    monkeypatch.delenv("INKREFERENCE_TEST_API_KEY", raising=False)
    with pytest.raises(DataError) as missing:
        invoke(service, configured)
    assert missing.value.status_code == 422
    assert requests == service.list_usage() == []
    monkeypatch.setenv("INKREFERENCE_TEST_API_KEY", "env-secret-12345")
    invoke(service, configured)
    assert requests[0].headers["Authorization"] == "Bearer env-secret-12345"
    assert "env-secret-12345" not in json.dumps(service.list_profiles())
    assert "env-secret-12345" not in json.dumps(service.list_usage())


def test_secret_echo_is_redacted_and_explicit_connection_test_calls_model(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return completion("OK secret-echo-456", usage={"prompt_tokens": 10, "completion_tokens": 2})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, api_key="secret-echo-456", max_tokens_parameter="max_completion_tokens")
    result = service.test_profile(configured["id"])
    assert result["ok"] is True
    assert result["message"] == "OK [REDACTED]"
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["max_completion_tokens"] == 8
    assert "max_tokens" not in body
    assert service.list_usage()[0]["run_id"] == "connection-test"


def test_redirect_is_not_followed_with_credentials(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example/steal"})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    with pytest.raises(DataError):
        invoke(service, profile(service, api_key="stay-local"))
    assert len(requests) == 1
    assert service.list_usage()[0]["http_status"] == 307


@pytest.mark.parametrize("values", [
    {"base_url": "https://user:super-secret@api.example/v1"},
    {"base_url": "https://api.example/v1?api_key=super-secret"},
    {"base_url": "file:///etc/passwd"},
    {"context_window": True},
    {"input_price_per_million": -1},
    {"input_price_per_million": float("nan")},
    {"input_price_per_million": 1},
    {"api_key": "secret\nheader"},
])
def test_invalid_config_rejected_without_exposing_url_secrets(tmp_path, values):
    service = ModelService(tmp_path)
    with pytest.raises(DataError) as result:
        profile(service, **values)
    assert result.value.status_code == 422
    assert "super-secret" not in str(result.value)
    assert service.list_profiles() == []


def test_invalid_inputs_never_reach_transport(tmp_path):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: requests.append(request)))
    configured = profile(service, context_window=256, max_output_tokens=8)
    invalid_options = [{"stream": True}, {"max_output_tokens": True}, {"response_format": {"type": "json_schema", "json_schema": []}}]
    for options in invalid_options:
        with pytest.raises(DataError):
            invoke(service, configured, options=options)
    with pytest.raises(DataError):
        service.invoke(configured["id"], [{"role": "user", "content": "文" * 200}], run_id="run", chunk_id="chunk")
    with pytest.raises(DataError):
        service.invoke("missing", MESSAGES, run_id="run", chunk_id="chunk")
    assert requests == service.list_usage() == []


def test_currency_totals_remain_separate_and_prices_never_invented(tmp_path):
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: completion(usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})))
    for currency, price in [("USD", 2), ("CNY", 8)]:
        invoke(service, profile(service, currency=currency, input_price_per_million=price, output_price_per_million=0))
    invoke(service, profile(service))
    summary = service.usage_summary()
    assert summary["calls"] == 3
    assert summary["costs"] == [{"currency": "CNY", "amount": 8, "source": "estimated"}, {"currency": "USD", "amount": 2, "source": "estimated"}]
    assert summary["unpriced_calls"] == 1
    with sqlite3.connect(tmp_path / "workbench.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM usage_calls").fetchone()[0] == 3
