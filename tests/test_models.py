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


def test_frozen_snapshot_uses_original_model_and_prices_at_same_endpoint(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return completion(usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, model="before", input_price_per_million=2, output_price_per_million=4, currency="USD")
    snapshot = service.snapshot(configured["id"])
    service.save_profile({"model": "after", "input_price_per_million": 999}, configured["id"])
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
    assert body["max_completion_tokens"] == 256
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


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.12.4.5:8080/v1", "https://[::1]:8080/v1"])
def test_local_profiles_only_accept_loopback_without_network_io(tmp_path, url):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req)))
    configured = profile(service, base_url=url, connection_mode="local")
    assert configured["connection_mode"] == "local" and requests == []


@pytest.mark.parametrize("url", [
    "http://192.168.0.3/v1", "https://public.example/v1", "http://localhost.example/v1",
    "http://2130706433/v1", "http://127.1/v1", "http://[::ffff:127.0.0.1]/v1", "http://localhost./v1",
])
def test_local_classification_rejects_lan_dns_and_ambiguous_hosts(tmp_path, url):
    with pytest.raises(DataError):
        profile(ModelService(tmp_path), base_url=url, connection_mode="local")


def test_remote_requires_https_and_explicit_test_action_is_sufficient(tmp_path):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req) or completion("OK")))
    with pytest.raises(DataError):
        profile(service, connection_mode="remote", base_url="http://api.example/v1")
    configured = profile(service, connection_mode="remote", base_url="https://api.example/v1", api_key="remote-secret")
    assert requests == []
    result = service.test_profile(configured["id"])
    assert result["ok"] and result["duration_ms"] >= 0
    assert json.loads(requests[0].content)["messages"] == [{"role": "user", "content": "仅回复 OK"}]


@pytest.mark.parametrize("change", [
    {"base_url": "http://127.0.0.1:9001/v1"},
    {"provider": "ollama"},
    {"connection_mode": "remote", "base_url": "https://api.example/v1"},
])
def test_endpoint_changes_drop_session_credentials_and_block_old_snapshots(tmp_path, change):
    requests = []
    def handle(req):
        requests.append(req)
        return httpx.Response(200, json={"message": {"content": "OK"}, "done": True}) if req.url.path.endswith("/api/chat") else completion()
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, api_key="must-not-follow-endpoint")
    snapshot = service.snapshot(configured["id"])
    updated = service.save_profile(change, configured["id"])
    assert updated["has_session_key"] is False and updated["key_available"] is False
    with pytest.raises(DataError) as failed:
        invoke(service, configured, profile_snapshot=snapshot)
    assert failed.value.status_code == 409 and requests == []
    invoke(service, updated)
    assert "Authorization" not in requests[0].headers


def test_endpoint_change_drops_inherited_environment_reference_and_clear_does_not_change_os_env(tmp_path, monkeypatch):
    monkeypatch.setenv("INKREFERENCE_ENV_KEY", "environment-do-not-follow")
    service = ModelService(tmp_path)
    configured = profile(service, key_env="INKREFERENCE_ENV_KEY")
    changed = service.save_profile({"base_url": "http://127.0.0.1:9002/v1"}, configured["id"])
    assert changed["key_env"] == "" and changed["credential_mode"] == "session"
    configured = service.save_profile({"credential_mode": "environment", "key_env": "INKREFERENCE_ENV_KEY"}, configured["id"])
    assert configured["key_available"]
    cleared = service.clear_credentials(configured["id"])
    assert cleared["key_env"] == "" and not cleared["key_available"]
    import os
    assert os.environ["INKREFERENCE_ENV_KEY"] == "environment-do-not-follow"


def test_draft_testing_does_not_save_configuration_or_secret(tmp_path):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req) or completion("OK draft-secret")))
    result = service.test_draft({"provider": "openai_compatible", "connection_mode": "local", "base_url": "http://127.0.0.1:1234/v1", "model": "draft", "api_key": "draft-secret"})
    assert result["message"] == "OK [REDACTED]" and len(requests) == 1
    assert service.list_profiles() == [] and service._session_keys == {}
    assert "draft-secret" not in json.dumps(service.list_usage())
    for path in tmp_path.iterdir():
        if path.is_file():
            assert b"draft-secret" not in path.read_bytes()


def test_saved_draft_key_cannot_be_reused_at_another_destination(tmp_path):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req) or completion("OK")))
    configured = profile(service, api_key="endpoint-specific-secret")
    service.test_draft({"use_saved_key": configured["id"], "model": "draft-next"})
    assert requests[0].headers["Authorization"] == "Bearer endpoint-specific-secret"
    with pytest.raises(DataError):
        service.test_draft({"use_saved_key": configured["id"], "base_url": "http://127.0.0.1:9003/v1"})
    assert len(requests) == 1


@pytest.mark.parametrize("provider,base,path,payload", [
    ("ollama", "http://127.0.0.1:11434", "/api/tags", {"models": [{"name": "qwen:latest"}, {"name": "qwen:latest"}]}),
    ("ollama", "http://127.0.0.1:11434/api", "/api/tags", {"models": [{"name": "qwen:latest"}]}),
    ("openai_compatible", "http://127.0.0.1:1234/v1", "/v1/models", {"data": [{"id": "qwen:latest"}]}),
])
def test_model_discovery_uses_get_without_text_or_generation(tmp_path, provider, base, path, payload):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req) or httpx.Response(200, json=payload)))
    result = service.discover_models({"provider": provider, "base_url": base})
    assert result == {"models": [{"id": "qwen:latest"}]}
    assert requests[0].method == "GET" and requests[0].url.path == path and requests[0].content == b""
    assert service.list_usage() == [] and service.list_profiles() == []


def test_explicit_remote_discovery_and_draft_tests_need_no_extra_confirmation(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "test"}]}) if request.method == "GET" else completion("OK")
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    draft = {"provider": "openai_compatible", "connection_mode": "remote", "base_url": "https://api.example/v1", "model": "test", "api_key": "keep-private"}
    for action in [service.test_draft, service.discover_models]:
        action(draft)
    assert [request.method for request in requests] == ["POST", "GET"]
    assert len(service.list_usage()) == 1 and service.list_profiles() == []


def test_discovery_does_not_follow_redirect_or_leak_raw_provider_error(tmp_path):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req) or httpx.Response(307, headers={"Location": "https://other.example/key"}, text="provider echoes secret-abc")))
    with pytest.raises(DataError) as result:
        service.discover_models({"provider": "ollama", "base_url": "http://127.0.0.1:11434", "api_key": "secret-abc"})
    assert "secret-abc" not in str(result.value) and len(requests) == 1


def test_legacy_remote_http_profiles_remain_editable_but_cannot_send(tmp_path):
    service = ModelService(tmp_path)
    configured = profile(service)
    old = service.snapshot(configured["id"])
    for name in ("connection_mode", "credential_mode"):
        old.pop(name)
    old["base_url"] = "http://old.example/v1"
    with sqlite3.connect(service.db_path) as connection:
        connection.execute("UPDATE model_profiles SET config_json=? WHERE id=?", (json.dumps(old), configured["id"]))
    assert service.list_profiles()[0]["connection_mode"] == "remote"
    with pytest.raises(DataError):
        service.snapshot(configured["id"])
    updated = service.save_profile({"base_url": "https://old.example/v1"}, configured["id"])
    assert updated["connection_mode"] == "remote"


@pytest.mark.parametrize("draft", [False, True], ids=["saved", "draft"])
def test_reasoning_connection_test_accepts_known_usage_length_without_visible_output(tmp_path, draft):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "length"}], "usage": {"prompt_tokens": 7, "completion_tokens": 256, "completion_tokens_details": {"reasoning_tokens": 256}}})
    service = ModelService(tmp_path, transport=httpx.MockTransport(handle))
    configured = profile(service, max_tokens_parameter="max_completion_tokens")
    result = service.test_draft({"use_saved_key": configured["id"]}) if draft else service.test_profile(configured["id"])
    assert result["ok"] is True and "测试输出预算不足" in result["message"]
    assert json.loads(requests[0].content)["max_completion_tokens"] == 256
    assert result["usage"]["source"] == "actual"
    assert result["usage"]["input_tokens"] == 7 and result["usage"]["output_tokens"] == 256
    row = service.list_usage()[0]
    assert row["status"] == "success" and row["error"] is None
    assert row["usage"] == result["usage"]
    with pytest.raises(DataError):
        invoke(service, configured)
    assert service.list_usage()[-1]["error"] == "invalid_response"
    assert service.list_usage()[-1]["usage"]["output_tokens"] == 256


def test_reasoning_connection_test_does_not_accept_null_output_without_known_usage(tmp_path):
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "length"}]})))
    configured = profile(service)
    with pytest.raises(DataError):
        service.test_profile(configured["id"])
    assert service.list_usage()[0]["usage"]["output_source"] == "unknown"


@pytest.mark.parametrize("context_window,max_output,expected", [(8192, 32, 32), (128, 64, 20)])
def test_connection_test_respects_output_and_context_limits(tmp_path, context_window, max_output, expected):
    requests = []
    service = ModelService(tmp_path, transport=httpx.MockTransport(lambda request: requests.append(request) or completion("OK")))
    configured = profile(service, context_window=context_window, max_output_tokens=max_output)
    assert service.test_profile(configured["id"])["ok"]
    assert json.loads(requests[0].content)["max_tokens"] == expected
