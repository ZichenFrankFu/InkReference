"""Configured model transports and an honest, durable per-request usage ledger.

No endpoint is contacted implicitly. API keys live in environment variables or
this service's memory; neither profile snapshots nor usage rows contain keys or
prompt/response text. HTTPX supplies transport, timeouts and mock injection.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .database import DataError

_CONFIG_FIELDS = {
    "id", "name", "provider", "base_url", "model", "context_window",
    "max_output_tokens", "timeout_seconds", "key_env", "input_price_per_million",
    "output_price_per_million", "currency", "max_tokens_parameter",
}
_PUBLIC_FIELDS = {"has_session_key", "key_available", "updated_at"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DataError(422, f"{name} 必须为 {minimum} 至 {maximum} 的整数")
    return value


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise DataError(422, f"{name} 数值超出允许范围")
    return float(value)


def _text(value: Any, name: str, *, maximum: int = 500, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or any(ord(char) < 32 for char in value):
        raise DataError(422, f"{name} 格式无效")
    return value.strip()


def _validate_profile(values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict) or set(values) - _CONFIG_FIELDS:
        raise DataError(422, "模型配置包含未知字段")
    provider = values.get("provider")
    if not isinstance(provider, str) or provider not in {"openai_compatible", "ollama"}:
        raise DataError(422, "provider 必须为 openai_compatible 或 ollama")
    model = _text(values.get("model"), "模型名称")
    base = _text(values.get("base_url"), "模型服务地址", maximum=2048).rstrip("/")
    try:
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError
        parsed.port
    except ValueError:
        # Never interpolate an untrusted URL, which might contain a password.
        raise DataError(422, "服务地址必须为 HTTP(S)，且不能包含账号、密码、查询参数或片段") from None
    key_env = _text(values.get("key_env", ""), "密钥环境变量", maximum=200, empty=True)
    if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise DataError(422, "密钥环境变量名称格式无效")
    context_window = _integer(values.get("context_window", 8192), "上下文窗口", 128, 10_000_000)
    max_output = _integer(values.get("max_output_tokens", 2048), "最大输出 token", 1, context_window - 1)
    prices = {}
    for field in ("input_price_per_million", "output_price_per_million"):
        value = values.get(field)
        prices[field] = None if value is None else _number(value, field, 0, 1_000_000_000)
    currency = _text(values.get("currency", ""), "币种", maximum=3, empty=True).upper()
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        raise DataError(422, "币种应为 USD、CNY 等三字母代码")
    if any(price is not None for price in prices.values()) and not currency:
        raise DataError(422, "配置价格时必须填写币种")
    token_parameter = values.get("max_tokens_parameter", "max_tokens")
    if not isinstance(token_parameter, str) or token_parameter not in {"max_tokens", "max_completion_tokens"}:
        raise DataError(422, "最大输出参数必须为 max_tokens 或 max_completion_tokens")
    return {
        "id": _text(values.get("id"), "模型配置 ID", maximum=100),
        "name": _text(values.get("name", model), "配置名称", maximum=200),
        "provider": provider, "base_url": base, "model": model,
        "context_window": context_window, "max_output_tokens": max_output,
        "timeout_seconds": _number(values.get("timeout_seconds", 180), "超时秒数", 1, 3600),
        "key_env": key_env, "currency": currency,
        "max_tokens_parameter": token_parameter, **prices,
    }


def _validate_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list) or not 1 <= len(messages) <= 1000:
        raise DataError(422, "messages 必须为非空消息列表")
    result = []
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise DataError(422, "每条消息仅支持 role 与文本 content")
        if not isinstance(message["role"], str) or message["role"] not in {"system", "user", "assistant"} or not isinstance(message["content"], str):
            raise DataError(422, "消息角色或文本类型无效")
        result.append({"role": message["role"], "content": message["content"]})
    return result


def estimate_messages(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Conservative planning estimate, never a provider tokenizer measurement.

    UTF-8 bytes plus a chat-format margin tend to overestimate common byte-based
    tokenizers. This is not a universal mathematical bound for arbitrary models.
    Nothing is downloaded and no request is sent to calculate this estimate.
    """
    messages = _validate_messages(messages)
    return {
        "input_tokens": sum(len(message["content"].encode("utf-8")) + 32 for message in messages) + 64,
        "source": "estimated", "method": "utf8_bytes_with_chat_margin",
    }


def _provider_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(input_count: int | None, output_count: int | None, estimated_input: int, content: str | None = None) -> dict[str, Any]:
    input_source = "actual" if input_count is not None else "estimated"
    if input_count is None:
        input_count = estimated_input
    if output_count is not None:
        output_source = "actual"
    elif content is not None:
        output_count = len(content.encode("utf-8"))
        output_source = "estimated"
    else:
        output_source = "unknown"
    source = input_source if input_source == output_source else "mixed"
    return {
        "input_tokens": input_count, "output_tokens": output_count,
        "input_source": input_source, "output_source": output_source, "source": source,
        "estimate_method": "utf8_bytes_with_chat_margin" if "estimated" in {input_source, output_source} else None,
    }


def _cost(profile: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    amount = None
    incoming, outgoing = profile["input_price_per_million"], profile["output_price_per_million"]
    if incoming is not None and outgoing is not None and usage["input_tokens"] is not None and usage["output_tokens"] is not None:
        amount = float((Decimal(str(incoming)) * usage["input_tokens"] + Decimal(str(outgoing)) * usage["output_tokens"]) / Decimal(1_000_000))
    return {"amount": amount, "currency": profile["currency"] or None, "source": "estimated" if amount is not None else "unknown", "token_source": usage["source"]}


class ModelService:
    def __init__(self, data_dir: str | Path, *, transport: httpx.BaseTransport | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        self.transport = transport
        self._session_keys: dict[str, str] = {}
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS model_profiles (id TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS usage_calls (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, profile_id TEXT NOT NULL, started_at TEXT NOT NULL, record_json TEXT NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS usage_calls_run ON usage_calls(run_id,started_at)")
            # A process that died after recording a request cannot know whether
            # the remote service completed or charged it. Keep this visible.
            # Do not rewrite pending rows here: another ModelService in the same
            # live process can own a currently running call.

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _config(self, profile_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT config_json FROM model_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise DataError(404, "模型配置不存在，请先创建本地或 API 模型配置")
        return json.loads(row["config_json"])

    def _public(self, config: dict[str, Any], updated_at: str | None = None) -> dict[str, Any]:
        identifier = config["id"]
        with self._lock:
            has_session = bool(self._session_keys.get(identifier))
        result = {**config, "has_session_key": has_session, "key_available": has_session or bool(config["key_env"] and os.environ.get(config["key_env"]))}
        if updated_at is not None:
            result["updated_at"] = updated_at
        return result

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT config_json,updated_at FROM model_profiles ORDER BY updated_at,id").fetchall()
        return [self._public(json.loads(row["config_json"]), row["updated_at"]) for row in rows]

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        return self._public(self._config(profile_id))

    def save_profile(self, values: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
        if not isinstance(values, dict) or set(values) - (_CONFIG_FIELDS | _PUBLIC_FIELDS | {"api_key"}):
            raise DataError(422, "模型配置包含未知字段")
        values = dict(values)
        if profile_id is not None and "id" in values and values["id"] != profile_id:
            raise DataError(422, "不能更改已有模型配置 ID")
        identifier = profile_id or values.get("id") or uuid.uuid4().hex
        identifier = _text(identifier, "模型配置 ID", maximum=100)
        previous: dict[str, Any] = {}
        with self._connect() as connection:
            row = connection.execute("SELECT config_json FROM model_profiles WHERE id=?", (identifier,)).fetchone()
            if row:
                previous = json.loads(row["config_json"])
        api_key = values.pop("api_key", None)
        if api_key is not None and (not isinstance(api_key, str) or len(api_key) > 4096 or any(ord(char) < 32 for char in api_key)):
            raise DataError(422, "本次会话 API Key 格式无效")
        config = _validate_profile({**previous, **{key: value for key, value in values.items() if key in _CONFIG_FIELDS}, "id": identifier})
        updated = _now()
        with self._lock:
            with self._connect() as connection:
                connection.execute("INSERT INTO model_profiles VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at", (identifier, _json(config), updated))
            if api_key is not None:
                if api_key:
                    self._session_keys[identifier] = api_key
                else:
                    self._session_keys.pop(identifier, None)
        return self._public(config, updated)

    def delete_profile(self, profile_id: str) -> dict[str, int]:
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM model_profiles WHERE id=?", (profile_id,))
                if not cursor.rowcount:
                    raise DataError(404, "模型配置不存在")
            self._session_keys.pop(profile_id, None)
        return {"deleted": 1}

    def snapshot(self, profile_id: str) -> dict[str, Any]:
        return self._config(profile_id)

    @staticmethod
    def estimate_messages(messages: list[dict[str, str]]) -> dict[str, Any]:
        return estimate_messages(messages)

    def _key(self, profile_id: str, config: dict[str, Any]) -> str | None:
        with self._lock:
            key = self._session_keys.get(profile_id)
        key = key or (os.environ.get(config["key_env"]) if config["key_env"] else None)
        if not key and config["key_env"]:
            raise DataError(422, "密钥环境变量尚未设置，请设置该环境变量或提供本次会话 API Key")
        if key is not None and any(ord(char) < 32 for char in key):
            raise DataError(422, "模型密钥格式无效")
        return key

    def _write_call(self, record: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO usage_calls VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json", (record["id"], record["run_id"], record["profile_id"], record["started_at"], _json(record)))

    def invoke(self, profile_id: str, messages: list[dict[str, str]], *, run_id: str, chunk_id: str, attempt: int = 1, options: dict[str, Any] | None = None, profile_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        messages = _validate_messages(messages)
        config = _validate_profile(profile_snapshot) if profile_snapshot is not None else self._config(profile_id)
        if config["id"] != profile_id:
            raise DataError(422, "模型快照与配置 ID 不一致")
        _integer(attempt, "尝试次数", 1, 1000)
        run_id = _text(run_id, "运行 ID", maximum=200)
        chunk_id = _text(chunk_id, "文本块 ID", maximum=200)
        options = self._options({} if options is None else options, config)
        estimate = estimate_messages(messages)
        if estimate["input_tokens"] + options["max_output_tokens"] > config["context_window"]:
            raise DataError(422, "输入的保守 token 估算与最大输出超过上下文预算，请减小文本块或核对上下文窗口")
        key = self._key(profile_id, config)
        provider = config["provider"]
        if provider == "ollama":
            path = "/chat" if config["base_url"].endswith("/api") else "/api/chat"
            payload: dict[str, Any] = {"model": config["model"], "messages": messages, "stream": False, "options": {"num_predict": options["max_output_tokens"], "num_ctx": config["context_window"]}}
            for name in ("temperature", "top_p", "seed", "stop"):
                if name in options:
                    payload["options"][name] = options[name]
            if "response_format" in options:
                requested_format = options["response_format"]
                payload["format"] = requested_format.get("json_schema", {}).get("schema", "json") if requested_format.get("type") == "json_schema" else "json"
        else:
            path = "/chat/completions"
            payload = {"model": config["model"], "messages": messages, "stream": False, config["max_tokens_parameter"]: options["max_output_tokens"]}
            payload.update({name: value for name, value in options.items() if name != "max_output_tokens"})
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        usage = _usage(None, None, estimate["input_tokens"])
        record = {
            "id": uuid.uuid4().hex, "run_id": run_id, "chunk_id": chunk_id,
            "profile_id": profile_id, "provider": provider, "model": config["model"],
            "attempt": attempt, "status": "pending", "http_status": None,
            "started_at": _now(), "duration_ms": None, "usage": usage,
            "cost": _cost(config, usage), "error": None, "profile_snapshot": config,
            "request_sha256": hashlib.sha256(_json(messages).encode("utf-8")).hexdigest(),
        }
        self._write_call(record)
        started = time.monotonic()
        result = None
        failure: DataError | None = None
        try:
            # No transport retries or hidden fallback requests: each invocation
            # corresponds to exactly one ledger row and one HTTP request.
            with httpx.Client(transport=self.transport, timeout=config["timeout_seconds"], follow_redirects=False, trust_env=False) as client:
                response = client.post(config["base_url"] + path, json=payload, headers=headers)
            record["http_status"] = response.status_code
            if not 200 <= response.status_code < 300:
                record["error"] = f"http_{response.status_code}"
                failure = DataError(502, f"模型服务返回 HTTP {response.status_code}，请检查模型名称、服务地址、密钥及服务日志")
            else:
                try:
                    data = response.json()
                    if not isinstance(data, dict) or data.get("error"):
                        raise ValueError
                    if provider == "ollama":
                        incoming = _provider_count(data.get("prompt_eval_count"))
                        outgoing = _provider_count(data.get("eval_count"))
                        record["usage"] = _usage(incoming, outgoing, estimate["input_tokens"])
                        record["cost"] = _cost(config, record["usage"])
                        content = data["message"]["content"]
                        if data.get("done") is not True:
                            raise ValueError
                        finish = data.get("done_reason", "stop")
                    else:
                        counts = data.get("usage") or {}
                        if not isinstance(counts, dict):
                            raise ValueError
                        incoming = _provider_count(counts.get("prompt_tokens"))
                        outgoing = _provider_count(counts.get("completion_tokens"))
                        record["usage"] = _usage(incoming, outgoing, estimate["input_tokens"])
                        record["cost"] = _cost(config, record["usage"])
                        choice = data["choices"][0]
                        content = choice["message"]["content"]
                        finish = choice.get("finish_reason", "")
                    if not isinstance(content, str) or not isinstance(finish, str):
                        raise ValueError
                    if key:
                        content = content.replace(key, "[REDACTED]")
                        finish = finish.replace(key, "[REDACTED]")
                    record["usage"] = _usage(incoming, outgoing, estimate["input_tokens"], content)
                    record["cost"] = _cost(config, record["usage"])
                    record["status"] = "success"
                    result = {"content": content, "usage": record["usage"], "cost": record["cost"], "call_id": record["id"], "finish_reason": finish, "model": config["model"]}
                except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                    record["error"] = "invalid_response"
                    failure = DataError(502, "模型响应格式无效；已记录本次请求，无法确认的输出用量保持未知")
        except httpx.TimeoutException:
            record["error"] = "timeout"
            failure = DataError(504, "模型请求超时；服务可能已处理该请求，输出用量未知，请查看用量记录后重试")
        except httpx.HTTPError:
            record["error"] = "transport_error"
            failure = DataError(502, "无法连接模型服务，请检查服务地址及服务运行状态")
        except Exception:
            # Transport exceptions can embed Authorization or URLs. Never return
            # their raw message, traceback, response body or request headers.
            record["error"] = "request_error"
            failure = DataError(502, "模型请求失败；详情已归类到用量记录")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            if failure is not None:
                record["status"] = "error"
            self._write_call(record)
        if failure is not None:
            raise failure from None
        assert result is not None
        return result

    @staticmethod
    def _options(values: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict) or set(values) - {"max_output_tokens", "temperature", "top_p", "seed", "stop", "response_format"}:
            raise DataError(422, "不支持的模型生成选项")
        result = {"max_output_tokens": _integer(values.get("max_output_tokens", config["max_output_tokens"]), "最大输出 token", 1, config["context_window"] - 1)}
        if "temperature" in values:
            result["temperature"] = _number(values["temperature"], "temperature", 0, 2)
        if "top_p" in values:
            result["top_p"] = _number(values["top_p"], "top_p", 0, 1)
        if "seed" in values:
            result["seed"] = _integer(values["seed"], "seed", -2**31, 2**31 - 1)
        if "stop" in values:
            stop = values["stop"]
            if not isinstance(stop, list) or len(stop) > 4 or any(not isinstance(item, str) or not item or len(item) > 100 for item in stop):
                raise DataError(422, "stop 必须为最多 4 项的非空文本列表")
            result["stop"] = stop
        if "response_format" in values:
            requested = values["response_format"]
            if not isinstance(requested, dict) or not isinstance(requested.get("type"), str) or requested.get("type") not in {"json_object", "json_schema"}:
                raise DataError(422, "response_format 仅支持 json_object 或 json_schema")
            if requested["type"] == "json_schema":
                schema = requested.get("json_schema")
                if not isinstance(schema, dict) or not isinstance(schema.get("schema"), dict):
                    raise DataError(422, "json_schema 必须包含 schema 对象")
            try:
                if len(_json(requested)) > 100_000:
                    raise ValueError
            except (ValueError, TypeError):
                raise DataError(422, "response_format 超出大小限制或包含无效值") from None
            result["response_format"] = requested
        return result

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        """One explicitly requested small generation; charges/counts normally."""
        result = self.invoke(profile_id, [{"role": "user", "content": "仅回复 OK"}], run_id="connection-test", chunk_id="connection-test", options={"max_output_tokens": 8})
        return {"ok": True, "model": result["model"], "message": result["content"], "usage": result["usage"], "cost": result["cost"], "call_id": result["call_id"]}

    def list_usage(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT record_json FROM usage_calls" + (" WHERE run_id=?" if run_id is not None else "") + " ORDER BY started_at,id", (run_id,) if run_id is not None else ()).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def usage_summary(self, run_id: str | None = None) -> dict[str, Any]:
        records = self.list_usage(run_id)
        summary: dict[str, Any] = {
            "calls": len(records), "successful_calls": 0, "failed_calls": 0, "pending_calls": 0,
            "actual_input_tokens": 0, "actual_output_tokens": 0,
            "estimated_input_tokens": 0, "estimated_output_tokens": 0,
            "unknown_input_calls": 0, "unknown_output_calls": 0,
            "costs": [], "unpriced_calls": 0,
        }
        costs: dict[str, Decimal] = {}
        for record in records:
            summary[{"success": "successful_calls", "error": "failed_calls", "pending": "pending_calls"}[record["status"]]] += 1
            for direction in ("input", "output"):
                source = record["usage"][f"{direction}_source"]
                count = record["usage"][f"{direction}_tokens"]
                if source in {"actual", "estimated"} and count is not None:
                    summary[f"{source}_{direction}_tokens"] += count
                else:
                    summary[f"unknown_{direction}_calls"] += 1
            cost = record["cost"]
            if cost["amount"] is None:
                summary["unpriced_calls"] += 1
            else:
                costs[cost["currency"]] = costs.get(cost["currency"], Decimal(0)) + Decimal(str(cost["amount"]))
        summary["costs"] = [{"currency": currency, "amount": float(amount), "source": "estimated"} for currency, amount in sorted(costs.items())]
        return summary
