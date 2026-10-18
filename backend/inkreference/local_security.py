"""Browser isolation for a single-user application bound to loopback only."""
from __future__ import annotations

import ipaddress
import hashlib
import json
import os
from urllib.parse import urlsplit


def loopback_host(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def origin_parts(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not loopback_host(parsed.hostname):
            return None
        if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
            return None
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return None
        return parsed.scheme, parsed.hostname, port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def trusted_host(value: str) -> bool:
    return origin_parts("http://" + value) is not None


def allowed_origin(origin: str, own_origin: str) -> bool:
    candidate = origin_parts(origin)
    if candidate is None:
        return False
    if candidate == origin_parts(own_origin):
        return True
    # A development exception is opt-in, exact, and still limited to loopback.
    configured = os.environ.get("INKREFERENCE_DEV_ORIGIN", "")
    return bool(configured and candidate == origin_parts(configured))


def remote_model(config: dict) -> bool:
    base = config.get("base_url", "")
    return config.get("connection_mode") == "remote" or bool(base and not loopback_host(urlsplit(base).hostname))


def target_fingerprint(config: dict) -> str:
    target = {key: config.get(key, "") for key in ("provider", "base_url", "model")}
    target["connection_mode"] = "remote" if remote_model(config) else "local"
    return hashlib.sha256(json.dumps(target, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def require_remote_consent(config: dict, consent: bool) -> None:
    if remote_model(config) and consent is not True:
        from .database import DataError
        raise DataError(428, "请先确认远程服务地址和模型，并同意将当前任务的全文分块发送给该服务。")


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), display-capture=()",
}
