"""Bounded local diagnostics: code locations and correlation, never user payloads."""
from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

MAX_BYTES = 1_000_000
BACKUPS = 4
RETENTION_DAYS = 14
_sinks: dict[str, "DiagnosticLog"] = {}
_registry_lock = threading.Lock()
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("inkreference_diagnostics", default={})
_request_state: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("inkreference_request_state", default=None)
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_TOKEN = re.compile(r"[^a-zA-Z0-9_.:/{}\-]")


def _token(value: Any, limit: int = 100) -> str:
    return _TOKEN.sub("_", str(value or ""))[:limit]


def redact(value: str, limit: int = 1200) -> str:
    """For explicit diagnostic labels only; payloads must never be passed here."""
    value = re.sub(r"\bBearer\s+[\w.+/~=-]+", "Bearer [REDACTED]", value, flags=re.I)
    value = re.sub(r"\b(?:sk|sess)-[\w-]{8,}", "[REDACTED]", value, flags=re.I)
    value = re.sub(r"([\"']?(?:api[_-]?key|access[_-]?token|authorization|password|secret)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,\"';&}]+)", r"\1[REDACTED]", value, flags=re.I)
    value = re.sub(r"(https?://)[^/\s@]+@", r"\1[REDACTED]@", value, flags=re.I)
    value = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1", value, flags=re.I)
    value = re.sub(r"(?:[A-Z]:\\Users\\|/Users/|/home/)[^\\/\s]+", "[HOME]", value, flags=re.I)
    return value[:limit]


def _frame_file(filename: str) -> str:
    path = filename.replace("\\", "/")
    for prefix in ("/backend/inkreference/", "/tests/"):
        if prefix in path:
            return ("backend/inkreference/" if "backend" in prefix else "tests/") + path.split(prefix, 1)[1]
    # Third-party/stdlib locations are useful without the user's home directory.
    for prefix in ("/site-packages/", "/lib/python"):
        if prefix in path:
            return path.split(prefix, 1)[1][-240:]
    return Path(path).name[-160:]


def error_frames(error: BaseException) -> list[dict[str, Any]]:
    """Do not format exception arguments or source lines: both can contain text."""
    frames = traceback.extract_tb(error.__traceback__)
    app = [frame for frame in frames if "/inkreference/" in frame.filename.replace("\\", "/")]
    selected = app[-16:] or frames[-12:]
    if frames and app and frames[-1] not in selected:
        selected = selected[-15:] + [frames[-1]]
    if not selected:
        selected = [frame for frame in traceback.extract_stack(limit=8)[:-1] if not frame.filename.endswith("diagnostics.py")][-4:]
    return [{"file": _frame_file(frame.filename), "function": _token(frame.name), "line": frame.lineno} for frame in selected]


class DiagnosticLog:
    def __init__(self, data_dir: str | Path, *, max_bytes: int = MAX_BYTES,
                 backups: int = BACKUPS, retention_days: int = RETENTION_DAYS):
        self.directory = Path(data_dir).resolve() / "logs"
        self.path = self.directory / "events.jsonl"
        self.max_bytes = max(4096, min(int(max_bytes), 10_000_000))
        self.backups = max(1, min(int(backups), 10))
        self.retention_days = max(1, min(int(retention_days), 90))
        self.lock = threading.RLock()

    def _prune(self) -> None:
        cutoff = time.time() - self.retention_days * 86400
        for path in [self.path, *(self.directory / f"events.{i}.jsonl" for i in range(1, self.backups + 1))]:
            if path.exists() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def write(self, event: dict[str, Any]) -> None:
        # Diagnostics must never affect a completed import, save, or HTTP reply.
        try:
            raw = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(raw) > self.max_bytes:
                event = {**event, "frames": event.get("frames", [])[-3:], "traceback": "[bounded]"}
                raw = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            with self.lock:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._prune()
                if self.path.exists() and self.path.stat().st_size + len(raw) > self.max_bytes:
                    (self.directory / f"events.{self.backups}.jsonl").unlink(missing_ok=True)
                    for index in range(self.backups - 1, 0, -1):
                        old = self.directory / f"events.{index}.jsonl"
                        if old.exists():
                            old.replace(self.directory / f"events.{index + 1}.jsonl")
                    self.path.replace(self.directory / "events.1.jsonl")
                descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(descriptor, "ab") as stream:
                    stream.write(raw)
        except (OSError, ValueError, TypeError):
            pass

    def recent_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 80))
        result = []
        try:
            with self.lock:
                paths = [self.path, *(self.directory / f"events.{i}.jsonl" for i in range(1, self.backups + 1))]
                for path in paths:
                    if not path.exists() or path.stat().st_size > self.max_bytes + 4096:
                        continue
                    # At most five bounded files. Stop as soon as enough errors exist.
                    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and event.get("level") in {"ERROR", "WARNING"}:
                            result.append(event)
                            if len(result) >= limit:
                                return result
        except (OSError, UnicodeError):
            pass
        return result


def diagnostic_log(data_dir: str | Path) -> DiagnosticLog:
    key = str(Path(data_dir).resolve())
    with _registry_lock:
        if key not in _sinks:
            # No open handles are held; evicted test/workspace sinks remain usable.
            if len(_sinks) >= 128:
                _sinks.pop(next(iter(_sinks)))
            _sinks[key] = DiagnosticLog(key)
        return _sinks[key]


def recent_error_events(data_dir: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    return diagnostic_log(data_dir).recent_errors(limit)


@contextlib.contextmanager
def diagnostic_context(*, data_dir: str | Path | None = None, **fields: Any) -> Iterator[None]:
    current = {**_context.get(), **fields}
    if data_dir is not None:
        current["sink"] = diagnostic_log(data_dir)
    token = _context.set(current)
    try:
        yield
    finally:
        _context.reset(token)


def record_event(component: str, operation: str, *, data_dir: str | Path | None = None,
                 level: str = "INFO", status: str = "running", task_id: str | None = None,
                 stage: str | None = None, duration_ms: float | None = None,
                 diagnostic_id: str | None = None, http_status: int | None = None,
                 frames: list[dict] | None = None, error_type: str | None = None) -> str | None:
    context = _context.get()
    sink = diagnostic_log(data_dir) if data_dir is not None else context.get("sink")
    if sink is None:
        return diagnostic_id
    event = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": level if level in {"INFO", "WARNING", "ERROR"} else "INFO",
             "component": _token(component), "operation": _token(operation), "status": _token(status),
             "request_id": context.get("request_id"), "task_id": _token(task_id or context.get("task_id")) or None,
             "stage": _token(stage or context.get("stage")) or None, "diagnostic_id": diagnostic_id,
             "duration_ms": round(max(0.0, float(duration_ms)), 3) if duration_ms is not None else None}
    if context.get("route"):
        event.update(route=_token(context["route"], 240), method=_token(context.get("method"), 12))
    if http_status is not None:
        event["http_status"] = int(http_status)
    if error_type:
        event["error_type"] = _token(error_type)
    if frames:
        event["frames"] = frames[-16:]
        event["source"] = frames[-1]
        event["traceback"] = "\n".join(f"{item['file']}:{item['line']} in {item['function']}" for item in frames[-16:]) + (f"\n{_token(error_type)}" if error_type else "")
    sink.write(event)
    return diagnostic_id


def record_exception(component: str, operation: str, error: BaseException, *,
                     data_dir: str | Path | None = None, task_id: str | None = None,
                     stage: str | None = None, status: str = "failed", http_status: int | None = None,
                     level: str = "ERROR") -> str:
    identity = getattr(error, "_ink_diagnostic_id", None) or "err-" + uuid4().hex[:16]
    state = _request_state.get()
    if state is not None:
        state["diagnostic_id"] = identity
    if not getattr(error, "_ink_diagnostic_recorded", False):
        record_event(component, operation, data_dir=data_dir, level=level, status=status, task_id=task_id,
                     stage=stage, diagnostic_id=identity, frames=error_frames(error),
                     error_type=type(error).__name__, http_status=http_status)
        try:
            error._ink_diagnostic_id = identity
            error._ink_diagnostic_recorded = True
        except (AttributeError, TypeError):
            pass
    return identity


def background_target(function: Callable) -> Callable:
    """Capture request correlation before creating a worker thread."""
    context = contextvars.copy_context()
    return functools.wraps(function)(lambda *args, **kwargs: context.run(function, *args, **kwargs))


def logged_operation(component: str, operation: str, *, task_arg: str | None = None,
                     lifecycle: bool = True) -> Callable:
    """Context for service boundaries. Source/prompt/keyword values are never logged."""
    def decorate(function: Callable) -> Callable:
        signature = inspect.signature(function)
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs).arguments
            owner = bound.get("self")
            data_dir = getattr(owner, "data_dir", None) or getattr(getattr(owner, "models", None), "data_dir", None)
            if data_dir is None and getattr(owner, "path", None):
                data_dir = Path(owner.path).parent
            task_id = bound.get(task_arg) if task_arg else None
            started = time.monotonic()
            fields = {"component": component, "operation": operation}
            if task_id is not None:
                fields["task_id"] = task_id
            with diagnostic_context(data_dir=data_dir, **fields):
                if lifecycle:
                    record_event(component, operation, status="started")
                try:
                    result = function(*args, **kwargs)
                except BaseException as error:
                    record_exception(component, operation, error)
                    raise
                finally:
                    if lifecycle:
                        record_event(component, operation, status="returned", duration_ms=(time.monotonic() - started) * 1000)
                return result
        return wrapped
    return decorate


class DiagnosticMiddleware:
    """ASGI correlation without buffering bodies or recording URLs/query strings."""
    def __init__(self, app, *, data_dir: str | Path, slow_ms: float = 1500):
        self.app, self.data_dir, self.slow_ms = app, data_dir, slow_ms

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        supplied = [value.decode("latin1").replace("-", "").lower() for key, value in scope.get("headers", []) if key.lower() == b"x-request-id"]
        request_id = supplied[0] if len(supplied) == 1 and _REQUEST_ID.fullmatch(supplied[0]) else uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = _request_state.set(state)
        started, status = time.monotonic(), 500
        with diagnostic_context(data_dir=self.data_dir, request_id=request_id, method=scope.get("method")):
            async def correlated_send(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                    headers = [(key, value) for key, value in message.get("headers", []) if key.lower() not in {b"x-request-id", b"x-diagnostic-id"}]
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                    if status >= 400:
                        state.setdefault("diagnostic_id", "err-" + uuid4().hex[:16])
                        headers.append((b"x-diagnostic-id", state["diagnostic_id"].encode("ascii")))
                    message = {**message, "headers": headers}
                await send(message)
            try:
                await self.app(scope, receive, correlated_send)
            except BaseException as error:
                record_exception("http", "dispatch", error, http_status=500)
                raise
            finally:
                route = getattr(scope.get("route"), "path", None) or "/{unmatched}"
                elapsed = (time.monotonic() - started) * 1000
                if status >= 400 or elapsed >= self.slow_ms:
                    with diagnostic_context(route=route):
                        record_event("http", "request", level="ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO",
                                     status="failed" if status >= 400 else "slow", duration_ms=elapsed,
                                     http_status=status, diagnostic_id=state.get("diagnostic_id"))
                _request_state.reset(token)


def install_diagnostics(application, data_dir: str | Path) -> None:
    """Call after other middleware is declared, so correlation is outermost."""
    application.state.diagnostics = diagnostic_log(data_dir)
    application.add_middleware(DiagnosticMiddleware, data_dir=data_dir)
