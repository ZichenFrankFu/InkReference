"""Configured model transports and an honest, durable per-request usage ledger.

No endpoint is contacted implicitly. API keys live in environment variables,
this service's memory or an explicitly selected native OS credential store;
profile snapshots and usage rows contain no keys or prompt/response text.
HTTPX supplies transport, timeouts and mock injection.
"""

from __future__ import annotations

import hashlib
import ipaddress
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
from .local_security import target_fingerprint
from .credentials import CredentialVault

_CONFIG_FIELDS = {
    "id", "name", "provider", "base_url", "model", "context_window",
    "max_output_tokens", "timeout_seconds", "key_env", "input_price_per_million",
    "output_price_per_million", "currency", "max_tokens_parameter", "connection_mode", "credential_mode",
}
_PUBLIC_FIELDS = {"has_session_key", "has_vault_key", "key_available", "credential_status", "updated_at", "target_fingerprint"}


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


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        # Python versions differ on IPv4-mapped IPv6 classification. Keep the
        # accepted spelling small and consistent across supported versions.
        return "%" not in host and not getattr(address, "ipv4_mapped", None) and address.is_loopback
    except ValueError:
        return False


def _endpoint_binding(config: dict[str, Any]) -> str:
    parsed = urlsplit(config["base_url"])
    identity = [config["provider"], config["connection_mode"], parsed.scheme.lower(),
                (parsed.hostname or "").lower(), parsed.port or (443 if parsed.scheme == "https" else 80), parsed.path.rstrip("/")]
    return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()


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
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or "\\" in base or any(char.isspace() for char in base):
            raise ValueError
        parsed.port
    except ValueError:
        # Never interpolate an untrusted URL, which might contain a password.
        raise DataError(422, "服务地址必须为 HTTP(S)，且不能包含账号、密码、查询参数或片段") from None
    # Old profiles/snapshots receive a conservative classification, never a
    # silent exemption from the new transport rules.
    connection_mode = values.get("connection_mode", "local" if _is_loopback(parsed.hostname.lower()) else "remote")
    if not isinstance(connection_mode, str) or connection_mode not in {"local", "remote"}:
        raise DataError(422, "连接类型必须为 local 或 remote")
    if connection_mode == "local" and not _is_loopback(parsed.hostname.lower()):
        raise DataError(422, "本地模型地址仅允许 localhost、127.0.0.0/8 或 ::1")
    if connection_mode == "remote" and parsed.scheme != "https":
        raise DataError(422, "远程 API 必须使用 HTTPS，请修改服务地址")
    key_env = _text(values.get("key_env", ""), "密钥环境变量", maximum=200, empty=True)
    if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise DataError(422, "密钥环境变量名称格式无效")
    credential_mode = values.get("credential_mode", "environment" if key_env else "session")
    if not isinstance(credential_mode, str) or credential_mode not in {"session", "vault", "environment"}:
        raise DataError(422, "密钥保存方式必须为 session、vault 或 environment")
    if credential_mode == "environment" and not key_env:
        raise DataError(422, "请填写密钥环境变量名称")
    if credential_mode != "environment":
        key_env = ""
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
        "connection_mode": connection_mode, "credential_mode": credential_mode,
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
        self.vault = CredentialVault(self.data_dir)
        self._session_keys: dict[str, tuple[str, str]] = {}
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS model_profiles (id TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS usage_calls (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, profile_id TEXT NOT NULL, started_at TEXT NOT NULL, record_json TEXT NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS usage_calls_run ON usage_calls(run_id,started_at)")
            connection.execute("CREATE TABLE IF NOT EXISTS model_vault_entries (profile_id TEXT PRIMARY KEY, binding TEXT NOT NULL, stored_at TEXT NOT NULL)")
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

    @staticmethod
    def _legacy_defaults(config: dict[str, Any]) -> dict[str, Any]:
        config = dict(config)
        host = (urlsplit(config["base_url"]).hostname or "").lower()
        config.setdefault("connection_mode", "local" if _is_loopback(host) else "remote")
        config.setdefault("credential_mode", "environment" if config.get("key_env") else "session")
        return config

    def _config(self, profile_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT config_json FROM model_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise DataError(404, "模型配置不存在，请先创建本地或 API 模型配置")
        return self._legacy_defaults(json.loads(row["config_json"]))

    def security_status(self) -> dict[str, Any]:
        return {**self.vault.status(), "credential_modes": ["session", "vault", "environment"]}

    def _vault_binding(self, profile_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT binding FROM model_vault_entries WHERE profile_id=?", (profile_id,)).fetchone()
        return row["binding"] if row else None

    def _public(self, config: dict[str, Any], updated_at: str | None = None) -> dict[str, Any]:
        config = self._legacy_defaults(config)
        identifier, binding = config["id"], _endpoint_binding(config)
        with self._lock:
            entry = self._session_keys.get(identifier)
            has_session = bool(entry and entry[0] == binding)
        has_vault = self._vault_binding(identifier) == binding
        mode = config["credential_mode"]
        vault_available = self.vault.status()["vault_available"] if mode == "vault" else False
        available = has_session if mode == "session" else bool(config["key_env"] and os.environ.get(config["key_env"])) if mode == "environment" else has_vault and vault_available
        status = "available" if available else "vault_unavailable" if mode == "vault" and not vault_available else "missing"
        result = {**config, "target_fingerprint": target_fingerprint(config), "has_session_key": has_session, "has_vault_key": has_vault,
                  "key_available": available, "credential_status": status}
        if updated_at is not None:
            result["updated_at"] = updated_at
        return result

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT config_json,updated_at FROM model_profiles ORDER BY updated_at,id").fetchall()
        return [self._public(json.loads(row["config_json"]), row["updated_at"]) for row in rows]

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        return self._public(self._config(profile_id))

    @staticmethod
    def _validate_key(api_key: Any) -> str | None:
        if api_key is not None and (not isinstance(api_key, str) or len(api_key) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in api_key)):
            raise DataError(422, "API Key 格式无效")
        return api_key

    def _forget_vault(self, profile_id: str) -> None:
        binding = self._vault_binding(profile_id)
        if binding is not None:
            self.vault.delete(profile_id, binding)
            with self._connect() as connection:
                connection.execute("DELETE FROM model_vault_entries WHERE profile_id=?", (profile_id,))

    def save_profile(self, values: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
        if not isinstance(values, dict) or set(values) - (_CONFIG_FIELDS | _PUBLIC_FIELDS | {"api_key"}):
            raise DataError(422, "模型配置包含未知字段")
        values = dict(values)
        if profile_id is not None and "id" in values and values["id"] != profile_id:
            raise DataError(422, "不能更改已有模型配置 ID")
        identifier = _text(profile_id or values.get("id") or uuid.uuid4().hex, "模型配置 ID", maximum=100)
        api_key = self._validate_key(values.pop("api_key", None))
        with self._lock:
            previous: dict[str, Any] = {}
            with self._connect() as connection:
                row = connection.execute("SELECT config_json FROM model_profiles WHERE id=?", (identifier,)).fetchone()
                if row:
                    previous = self._legacy_defaults(json.loads(row["config_json"]))
            merged = {**previous, **{key: value for key, value in values.items() if key in _CONFIG_FIELDS}, "id": identifier}
            # An inherited environment reference is never silently reassigned to
            # another endpoint. The GUI clears explicitly submitted references
            # as well; a new explicit reference counts as a new user setting.
            probe = {**merged, "credential_mode": "session", "key_env": ""}
            probe = _validate_profile(probe)
            endpoint_changed = bool(previous and _endpoint_binding(previous) != _endpoint_binding(probe))
            if endpoint_changed and "key_env" not in values:
                merged["key_env"] = ""
                if merged.get("credential_mode") == "environment":
                    merged["credential_mode"] = "session"
            config = _validate_profile(merged)
            if api_key and config["credential_mode"] == "environment":
                raise DataError(422, "环境变量模式请仅填写变量名；API Key 可使用本次会话或系统凭据库")
            mode_changed = bool(previous and previous["credential_mode"] != config["credential_mode"])
            if config["credential_mode"] == "vault" and not self.vault.status()["vault_available"]:
                raise DataError(503, "系统凭据库不可用，请使用本次会话或环境变量")
            if endpoint_changed or mode_changed or api_key is not None:
                self._forget_vault(identifier)
                self._session_keys.pop(identifier, None)
            binding = _endpoint_binding(config)
            if api_key:
                if config["credential_mode"] == "vault":
                    self.vault.set(identifier, binding, api_key)
                    with self._connect() as connection:
                        connection.execute("INSERT OR REPLACE INTO model_vault_entries VALUES (?,?,?)", (identifier, binding, _now()))
                else:
                    self._session_keys[identifier] = (binding, api_key)
            updated = _now()
            with self._connect() as connection:
                connection.execute("INSERT INTO model_profiles VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at", (identifier, _json(config), updated))
        return self._public(config, updated)

    def clear_credentials(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            config = self._config(profile_id)
            self._forget_vault(profile_id)
            self._session_keys.pop(profile_id, None)
            config.update(key_env="", credential_mode="session")
            with self._connect() as connection:
                connection.execute("UPDATE model_profiles SET config_json=?,updated_at=? WHERE id=?", (_json(config), _now(), profile_id))
        return self._public(config)

    def delete_profile(self, profile_id: str) -> dict[str, int]:
        with self._lock:
            self._config(profile_id)
            self._forget_vault(profile_id)
            with self._connect() as connection:
                connection.execute("DELETE FROM model_profiles WHERE id=?", (profile_id,))
            self._session_keys.pop(profile_id, None)
        return {"deleted": 1}

    def snapshot(self, profile_id: str) -> dict[str, Any]:
        return _validate_profile(self._config(profile_id))

    @staticmethod
    def estimate_messages(messages: list[dict[str, str]]) -> dict[str, Any]:
        return estimate_messages(messages)

    def _key(self, profile_id: str, config: dict[str, Any], *, require_current: bool = True) -> str | None:
        binding = _endpoint_binding(config)
        if require_current and binding != _endpoint_binding(self._config(profile_id)):
            raise DataError(409, "模型服务地址已变更，请重新选择模型并确认连接")
        mode = config["credential_mode"]
        if mode == "session":
            with self._lock:
                entry = self._session_keys.get(profile_id)
            key = entry[1] if entry and entry[0] == binding else None
        elif mode == "vault":
            if self._vault_binding(profile_id) != binding:
                raise DataError(422, "该模型尚未保存系统凭据，请重新填写 API Key")
            key = self.vault.get(profile_id, binding)
            if not key:
                raise DataError(422, "系统凭据已失效，请重新填写 API Key")
        else:
            # Only the currently selected reference may resolve a secret. A
            # stale workflow snapshot cannot resurrect a cleared variable name.
            if require_current and self._config(profile_id).get("key_env") != config["key_env"]:
                raise DataError(409, "模型密钥来源已变更，请重新确认模型配置")
            key = os.environ.get(config["key_env"])
            if not key:
                raise DataError(422, "密钥环境变量尚未设置，请设置环境变量后重试")
        return self._validate_key(key)

    def _write_call(self, record: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO usage_calls VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json", (record["id"], record["run_id"], record["profile_id"], record["started_at"], _json(record)))

    def invoke(self, profile_id: str, messages: list[dict[str, str]], *, run_id: str, chunk_id: str, attempt: int = 1, options: dict[str, Any] | None = None, profile_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        config = _validate_profile(profile_snapshot) if profile_snapshot is not None else _validate_profile(self._config(profile_id))
        if config["id"] != profile_id:
            raise DataError(422, "模型快照与配置 ID 不一致")
        return self._invoke_config(config, messages, key=self._key(profile_id, config), run_id=run_id, chunk_id=chunk_id, attempt=attempt, options=options)

    def _invoke_config(self, config: dict[str, Any], messages: list[dict[str, str]], *, key: str | None, run_id: str, chunk_id: str, attempt: int = 1, options: dict[str, Any] | None = None, connection_test: bool = False) -> dict[str, Any]:
        messages = _validate_messages(messages)
        profile_id = config["id"]
        _integer(attempt, "尝试次数", 1, 1000)
        run_id = _text(run_id, "运行 ID", maximum=200)
        chunk_id = _text(chunk_id, "文本块 ID", maximum=200)
        options = self._options({} if options is None else options, config)
        estimate = estimate_messages(messages)
        if estimate["input_tokens"] + options["max_output_tokens"] > config["context_window"]:
            raise DataError(422, "输入的保守 token 估算与最大输出超过上下文预算，请减小文本块或核对上下文窗口")
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
                    # A reasoning model can spend the entire small test budget
                    # before producing visible text. Known billed output plus
                    # an explicit length finish proves a valid connection, but
                    # is never accepted as an extraction result.
                    if connection_test and provider == "openai_compatible" and content in (None, "") and finish == "length" and outgoing is not None:
                        content = "连接成功；测试输出预算不足，模型未返回可见文本"
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
        result["duration_ms"] = record["duration_ms"]
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

    def _draft(self, values: dict[str, Any], *, discovery: bool = False) -> tuple[dict[str, Any], str | None]:
        allowed = _CONFIG_FIELDS | _PUBLIC_FIELDS | {"api_key", "use_saved_key", "remote_consent"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise DataError(422, "模型草稿包含未知字段")
        values = dict(values)
        saved_id = values.pop("use_saved_key", None)
        if saved_id is not None:
            saved_id = _text(saved_id, "已保存配置 ID", maximum=100)
        previous = self._config(saved_id) if saved_id is not None else {}
        identifier = saved_id or "draft-" + uuid.uuid4().hex
        api_key = self._validate_key(values.pop("api_key", None))
        config_values = {**previous, **{key: value for key, value in values.items() if key in _CONFIG_FIELDS}, "id": identifier}
        if discovery and not config_values.get("model"):
            config_values["model"] = "model-discovery"
        config = _validate_profile(config_values)
        if saved_id is not None and _endpoint_binding(config) != _endpoint_binding(previous):
            raise DataError(409, "服务地址、协议或连接类型已变更，请重新填写 API Key")
        if api_key is not None:
            if config["credential_mode"] == "environment" and api_key:
                raise DataError(422, "环境变量模式请仅填写变量名")
            key = api_key or None
        elif saved_id is not None:
            if config["credential_mode"] != previous["credential_mode"] or config["key_env"] != previous["key_env"]:
                raise DataError(409, "密钥来源已变更，请重新填写 API Key 或环境变量")
            key = self._key(saved_id, config)
        elif config["credential_mode"] == "environment":
            key = self._key(identifier, config, require_current=False)
        elif config["credential_mode"] == "vault":
            raise DataError(422, "请填写 API Key，或选择已保存的系统凭据")
        else:
            key = None
        return config, key

    def test_profile(self, profile_id: str, *, allow_remote: bool = False) -> dict[str, Any]:
        """One explicitly requested synthetic generation; no document is read."""
        config = self.snapshot(profile_id)
        return self._test_connection(config, self._key(profile_id, config))

    def test_draft(self, values: dict[str, Any], *, allow_remote: bool = False) -> dict[str, Any]:
        config, key = self._draft(values)
        return self._test_connection(config, key)

    def _test_connection(self, config: dict[str, Any], key: str | None) -> dict[str, Any]:
        messages = [{"role": "user", "content": "仅回复 OK"}]
        budget = min(256, config["max_output_tokens"], config["context_window"] - estimate_messages(messages)["input_tokens"])
        result = self._invoke_config(config, messages, key=key, run_id="connection-test", chunk_id="connection-test", options={"max_output_tokens": budget}, connection_test=True)
        return {"ok": True, **{name: result[name] for name in ("model", "usage", "cost", "call_id", "duration_ms")}, "message": result["content"]}

    def discover_models(self, values: dict[str, Any], *, allow_remote: bool = False) -> dict[str, Any]:
        """Fetch names only after an explicit action; no generation or body text."""
        config, key = self._draft(values, discovery=True)
        if config["provider"] == "ollama":
            path = "/tags" if config["base_url"].endswith("/api") else "/api/tags"
        else:
            path = "/models"
        headers = {"Authorization": "Bearer " + key} if key else {}
        try:
            with httpx.Client(transport=self.transport, timeout=min(config["timeout_seconds"], 30), follow_redirects=False, trust_env=False) as client:
                response = client.get(config["base_url"] + path, headers=headers)
            if not 200 <= response.status_code < 300:
                raise DataError(502, f"模型列表请求返回 HTTP {response.status_code}，请检查连接配置")
            if len(response.content) > 5_000_000:
                raise DataError(502, "模型列表超过大小限制")
            data = response.json()
            entries = data.get("models" if config["provider"] == "ollama" else "data")
            if not isinstance(entries, list) or len(entries) > 10_000:
                raise ValueError
            names = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError
                name = entry.get("name") if config["provider"] == "ollama" else entry.get("id")
                name = _text(name, "模型名称", maximum=500)
                if key:
                    name = name.replace(key, "[REDACTED]")
                if name not in names:
                    names.append(name)
            return {"models": [{"id": name} for name in names]}
        except httpx.TimeoutException:
            raise DataError(504, "获取模型列表超时，请检查模型服务") from None
        except httpx.HTTPError:
            raise DataError(502, "无法连接模型服务，请检查服务地址") from None
        except DataError:
            raise
        except Exception:
            raise DataError(502, "模型列表格式无效，请手动填写模型名称") from None

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
