"""Local, reviewable source repairs. No provider calls or LLM supplied commands."""
from __future__ import annotations

import copy
import difflib
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import __version__
from .database import DataError

PROTOCOL = "inkreference.repair.v1"
MAX_REPLY = 2_000_000
MAX_FILES = 12
MAX_FILE = 1_000_000
TTL = 2 * 60 * 60
TOPICS = {
    "chapters": ("正文与清洗", ("frontend/src/ChapterWorkbench.tsx", "backend/inkreference/documents.py", "frontend/src/chapter.css", "frontend/src/ChapterTextEditor.tsx", "backend/inkreference/chapter_preprocessing.py")),
    "upload": ("文件上传", ("frontend/src/DocumentUpload.tsx", "frontend/src/Documents.tsx", "backend/inkreference/ingestions.py")),
    "models": ("模型连接", ("frontend/src/Models.tsx", "backend/inkreference/models.py")),
    "library": ("素材库", ("frontend/src/Library.tsx", "backend/inkreference/library_exchange.py")),
    "workflow": ("分析与审核", ("frontend/src/ReferenceAnalysis.tsx", "backend/inkreference/workflows.py")),
    "app": ("界面与启动", ("frontend/src/App.tsx", "frontend/src/styles.css", "backend/inkreference/api.py")),
}
_SELF = {"backend/inkreference/quick_repair.py", "backend/inkreference/repair_routes.py"}
_WINDOWS = {
    "backend/inkreference/documents.py": ("def get_chapter(", "def edit_chapter_slice(", "def edit_chapter(", "def apply_chapter_cleaning(", "def _incremental_chapter_edit(", "def rename_chapter("),
    "frontend/src/ChapterWorkbench.tsx": ("const changePage", "const mutate", "const save", "ChapterTextEditor", "ChapterCleanupDialog", "chapter-navigation", "return ("),
    "frontend/src/ChapterTextEditor.tsx": ("export function", "highlight", "textarea", "return ("),
    "backend/inkreference/models.py": ("def save(", "def test(", "def discover(", "def invoke("),
    "frontend/src/Models.tsx": ("async function", "return ("),
}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fail(message: str, status: int = 422):
    raise DataError(status, message)


class QuickRepair:
    def __init__(self, data_dir: Path, source_root: Path | None = None, *, frozen: bool | None = None,
                 cache_rebuilder: Callable[[str, str | None], dict] | None = None):
        self.root = (source_root or Path(__file__).resolve().parents[2]).resolve()
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self.source = not self.frozen and (self.root / "pyproject.toml").is_file() and (self.root / "backend" / "inkreference").is_dir()
        self.backups = Path(data_dir) / "quick-repairs"
        self.cache_rebuilder = cache_rebuilder
        self.lock = threading.RLock()
        self.events: deque[dict] = deque(maxlen=80)
        self.sessions: dict[str, dict] = {}
        self.previews: dict[str, dict] = {}
        self.operations: dict[str, dict] = {}
        self.threads: list[threading.Thread] = []
        self.processes: set[subprocess.Popen] = set()
        self.closed = threading.Event()
        self._load_events()
        self._load_operations()

    def _load_events(self):
        try:
            target = self.backups / "recent-errors.json"
            if target.stat().st_size > 80_000:
                return
            values = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(values, list):
                self.events.extend(item for item in values[-40:] if isinstance(item, dict))
        except (OSError, ValueError, TypeError):
            pass

    def _persist_events(self):
        try:
            self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = self.backups / "recent-errors.json"
            temporary = self.backups / "recent-errors.tmp"
            # Successful requests remain in memory; persist only on an error/prompt.
            values = list(self.events)[-40:]
            temporary.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, target)
        except OSError:
            pass

    def _load_operations(self):
        if not self.backups.is_dir():
            return
        # Only local repair manifests, never application databases or user logs.
        manifests = sorted(self.backups.glob("*/operation.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:24]
        for manifest in reversed(manifests):
            try:
                saved = json.loads(manifest.read_text(encoding="utf-8"))
                if saved.pop("root_fingerprint", None) != _sha(str(self.root).encode()):
                    continue
                if not re.fullmatch(r"[0-9a-f]{32}", saved.get("operation_id", "")) or saved["operation_id"] != manifest.parent.name:
                    continue
                for item in saved.get("files", []):
                    self._path(item["path"], must_exist=False)
                if saved.get("status") == "running":
                    saved.update(status="failed", stage="上次修复被中断", progress=100,
                                 error="请检查修复文件；未自动覆盖后续修改",
                                 rollback_available=bool(saved.get("files")) and all((manifest.parent / f"{index}.original").is_file() for index, _ in enumerate(saved.get("files", []))))
                self.operations[saved["operation_id"]] = saved
            except (OSError, ValueError, TypeError, KeyError, DataError):
                continue

    def _persist(self, operation):
        directory = self.backups / operation["operation_id"]
        if not directory.is_dir():
            return
        saved = copy.deepcopy(operation)
        saved["root_fingerprint"] = _sha(str(self.root).encode())
        saved["files"] = [{key: value for key, value in item.items() if key not in {"content", "diff"}} for item in saved.get("files", [])]
        target = directory / "operation.json"
        temporary = directory / "operation.tmp"
        temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, target)

    def redact(self, value: Any, limit: int = 16_000) -> str:
        text = str(value)[:limit]
        text = text.replace(str(self.root), "<project>").replace(str(Path.home()), "<home>")
        text = re.sub(r"(?i)\b(?:bearer\s+)[\w.\-+/=]+", "Bearer <redacted>", text)
        text = re.sub(r"\b(?:sk-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{15,})", "<redacted>", text)
        text = re.sub(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''', r"\1<redacted>", text)
        text = re.sub(r"(https?://[^\s?'\"<>]+)\?[^\s'\"<>]*", r"\1?<redacted>", text)
        text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1<redacted>@", text)
        text = re.sub(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s]+", "<home>", text)
        text = re.sub(r"/(?:Users|home)/[^/\s]+", "<home>", text)
        return text

    def record_request(self, method: str, path: str, status: int, duration_ms: float):
        # Use route templates when provided; never retain query strings or bodies.
        path = path.split("?", 1)[0]
        path = re.sub(r"(?<=/)[a-f0-9]{16,}(?=/|$)", "{id}", path)
        with self.lock:
            self.events.append({"kind": "request", "method": method[:12], "path": self.redact(path, 240),
                                "status": int(status), "duration_ms": round(float(duration_ms), 1), "at": time.time()})

    def record_exception(self, exception: BaseException, *, method: str = "", path: str = ""):
        frames = []
        for frame in traceback.extract_tb(exception.__traceback__)[-10:]:
            try:
                name = Path(frame.filename).resolve().relative_to(self.root).as_posix()
            except (ValueError, OSError):
                name = Path(frame.filename).name
            frames.append({"file": name, "line": frame.lineno, "function": frame.name})
        # Exception messages and source lines can embed full texts, keys or form input.
        with self.lock:
            self.events.append({"kind": "exception", "type": type(exception).__name__, "frames": frames,
                                "method": method[:12], "path": self.redact(path.split("?", 1)[0], 240), "at": time.time()})
            self._persist_events()

    def capabilities(self):
        return {"mode": "source" if self.source else "packaged", "can_apply_source": self.source,
                "can_rebuild_cache": self.cache_rebuilder is not None, "source_root_label": "InkReference",
                "protocol": PROTOCOL, "topics": [{"id": key, "label": value[0]} for key, value in TOPICS.items()],
                "limits": {"files": MAX_FILES, "reply_characters": MAX_REPLY, "session_seconds": TTL}}

    def _expire(self):
        now = time.time()
        for items in (self.sessions, self.previews):
            for key in list(items):
                if items[key]["expires_at"] <= now:
                    del items[key]
            while len(items) > 24:
                del items[next(iter(items))]

    def _git_info(self):
        result = {"commit": None, "branch": None, "dirty": None, "changed_files": []}
        if not self.source or not (self.root / ".git").exists():
            return result
        for key, args in (("commit", ["rev-parse", "HEAD"]), ("branch", ["branch", "--show-current"]),
                          ("status", ["status", "--porcelain", "--untracked-files=no"])):
            try:
                output = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, timeout=2, check=False)
                if output.returncode == 0:
                    text = output.stdout.decode("utf-8", "replace")
                    if key == "status":
                        result["dirty"] = bool(text.strip())
                        result["changed_files"] = [self.redact(line[3:], 180) for line in text.splitlines()[:20]]
                    else:
                        result[key] = self.redact(text.strip(), 100)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return result

    def prompt(self, description: str, topic: str = "app", frontend_events: list[dict] | None = None, context: str = ""):
        if topic not in TOPICS:
            _fail("请选择有效的问题页面")
        if not description.strip() or len(description) > 6000 or len(context) > 12_000:
            _fail("请填写问题描述；描述最多 6000 字，可选上下文最多 12000 字")
        with self.lock:
            self._expire()
            events = copy.deepcopy(list(self.events)[-40:])
            self._persist_events()
        front = []
        for event in (frontend_events or [])[-40:]:
            if not isinstance(event, dict):
                continue
            # These are frontend error metadata, never fetch payloads/headers.
            front.append({key: self.redact(event[key], 400) for key in ("kind", "message", "path", "at") if key in event})
        dependencies = {}
        for package in ("fastapi", "uvicorn", "pydantic", "langgraph", "defusedxml"):
            try:
                dependencies[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                dependencies[package] = "not-installed"
        diagnostics = {"app_version": __version__, "mode": "source" if self.source else "packaged",
                       "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
                       "python": platform.python_version(), "dependencies": dependencies, "git": self._git_info(),
                       "requests_and_errors": events, "frontend_events": front}
        source_files = []
        remaining = 44_000
        truncated = False
        for name in TOPICS[topic][1]:
            if not self.source:
                continue
            try:
                path = self._path(name, must_exist=True)
                raw = path.read_bytes()
            except (DataError, OSError):
                continue
            if len(raw) > MAX_FILE:
                continue
            source = raw.decode("utf-8", "strict")
            lines = source.splitlines(keepends=True)
            # Full small files; first section and relevant traceback windows for larger files.
            selected = set(range(min(len(lines), 35)))
            for anchor in _WINDOWS.get(name, ()):
                matches = [i for i, line in enumerate(lines) if anchor in line]
                for index in matches[:2]:
                    selected.update(range(max(0, index - 4), min(len(lines), index + 27)))
            for event in events:
                for frame in event.get("frames", []):
                    if frame["file"] == name:
                        selected.update(range(max(0, frame["line"] - 22), min(len(lines), frame["line"] + 22)))
            if len(source) <= 11_000:
                excerpt = source
                complete = True
            elif name.endswith(".css"):
                blocks = source.split("}")
                needles = ("chapter-navigation-item", "chapter-input-mark", "chapter-cleanup-pagination", "chapter-title", "chapter-reader", ":root", "data-scheme")
                matching = [block + "}" for block in blocks if any(needle in block for needle in needles)]
                excerpt = "\n[CSS 规则摘录]\n" + "\n".join(matching[:55])
                complete = False
                truncated = True
            else:
                sections = []
                previous = -2
                for index in sorted(selected):
                    if index != previous + 1:
                        sections.append("\n[源码摘录省略]\n")
                    sections.append(f"{index + 1:04d} {lines[index]}")
                    previous = index
                excerpt = "".join(sections)
                complete = False
                truncated = True
            clean = self.redact(excerpt, min(remaining, 15_000))
            if not remaining or not clean:
                truncated = True
                break
            remaining -= len(clean)
            source_files.append({"path": name, "sha256": _sha(raw), "content": clean,
                                 "complete": complete and len(clean) == len(source)})
        session_id = uuid.uuid4().hex
        payload = {"session_id": session_id, "description": self.redact(description, 6000),
                   "context": self.redact(context, 12_000), "diagnostics": diagnostics, "source_files": source_files}
        instructions = (
            "你正在修复 InkReference。分析下面的诊断与源码。用户会把你的回复粘贴回应用。\n"
            "仅返回一个 JSON 对象，禁止附加 shell 命令、安装命令、Markdown 或自然语言前后缀。\n"
            f"协议为 {PROTOCOL}；session_id 必须原样返回。files 最多 {MAX_FILES} 个，只能修改现有项目业务源码。\n"
            "files 每项含 path（项目相对路径）、sha256（原文件完整字节的 SHA256），以及 edits:[{before,after}]。"
            "每个 before 必须在当前文件中恰好出现一次，且不可为空；也可用 content 完整新文件内容替代 edits。"
            "禁止修改依赖/构建清单、修复引擎、node_modules、用户数据、凭据、数据库或执行命令。\n"
            "请保留原件、不可变版本和版本冲突校验，修复后应用会执行固定静态检查及前端构建。\n"
            "源码摘录可能截断或带行号；before 必须使用真实源码，不含摘录行号。缺少信息时请先要求用户提供对应源码，"
            "不要虚构 hash 或补丁。打包版不能直接修改 exe；需把修复方案导出后交由源码开发环境应用。\n"
            "回复格式示例：{\"protocol\":\"inkreference.repair.v1\",\"session_id\":\"原session_id\","
            "\"summary\":\"修复内容\",\"files\":[{\"path\":\"frontend/src/App.tsx\",\"sha256\":\"原sha256\","
            "\"edits\":[{\"before\":\"唯一的原代码\",\"after\":\"替换代码\"}]}]}\n\n"
        )
        with self.lock:
            self.sessions[session_id] = {"expires_at": time.time() + TTL, "topic": topic}
        return {"session_id": session_id, "prompt": instructions + json.dumps(payload, ensure_ascii=False, indent=2),
                "diagnostics": diagnostics, "source_files": [{"path": f["path"], "sha256": f["sha256"]} for f in source_files],
                "truncated": truncated, "capabilities": self.capabilities()}

    def _path(self, name: Any, *, must_exist: bool):
        if not isinstance(name, str) or "\\" in name or ":" in name or any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in name):
            _fail("补丁路径必须是项目相对路径")
        parts = PurePosixPath(name).parts
        if not parts or name != PurePosixPath(name).as_posix() or ".." in parts or PurePosixPath(name).is_absolute():
            _fail("补丁路径必须是规范的项目相对路径")
        allowed = ((name.startswith("backend/inkreference/") and name.endswith(".py")) or
                   (name.startswith("frontend/src/") and Path(name).suffix in {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}) or
                   (name.startswith("scripts/") and Path(name).suffix in {".py", ".ps1", ".sh"}))
        if not allowed or name in _SELF or any(part.startswith(".") or part in {"node_modules", "__pycache__", "dist", "data", "artifacts"} for part in parts):
            _fail(f"不支持修改此文件：{name}")
        path = self.root.joinpath(*parts)
        cursor = self.root
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                _fail("不能修改符号链接或其目录")
        if not path.resolve().is_relative_to(self.root):
            _fail("补丁路径超出项目目录")
        if must_exist and not path.is_file():
            _fail(f"项目中未找到文件：{name}")
        if path.is_file() and path.stat().st_size > MAX_FILE:
            _fail(f"文件超过 1 MB：{name}")
        return path

    @staticmethod
    def _parse(reply: str):
        if not isinstance(reply, str) or len(reply) > MAX_REPLY:
            _fail("修复回复不能超过 2 MB")
        # One fenced JSON block is accepted for common chat clients; no extraction from prose.
        candidate = reply.strip()
        if candidate.startswith("```json\n") and candidate.endswith("\n```"):
            candidate = candidate[8:-4]
        try:
            def pairs(values):
                obj = {}
                for key, value in values:
                    if key in obj:
                        raise ValueError("重复字段")
                    obj[key] = value
                return obj
            result = json.loads(candidate, object_pairs_hook=pairs)
        except (ValueError, TypeError, RecursionError):
            _fail("回复需为完整的 JSON 修复对象")
        if not isinstance(result, dict) or set(result) != {"protocol", "session_id", "summary", "files"} or result["protocol"] != PROTOCOL:
            _fail("修复协议或字段不符合 inkreference.repair.v1")
        if not isinstance(result["session_id"], str) or not isinstance(result["summary"], str) or not 1 <= len(result["summary"]) <= 2000:
            _fail("修复会话或摘要无效")
        if not isinstance(result["files"], list) or not 1 <= len(result["files"]) <= MAX_FILES:
            _fail(f"修复方案需包含 1 至 {MAX_FILES} 个文件")
        return result

    def preview(self, reply: str, session_id: str | None = None):
        plan = self._parse(reply)
        with self.lock:
            self._expire()
            if session_id is not None:
                # Explicitly import an exported plan into a fresh local diagnosis.
                # All source hashes and exact replacements are still verified below.
                if session_id not in self.sessions:
                    _fail("本机诊断会话已过期，请重新生成诊断", 409)
                plan["session_id"] = session_id
            if plan["session_id"] not in self.sessions:
                _fail("诊断会话已过期，请重新生成诊断", 409)
        prepared, seen = [], set()
        for item in plan["files"]:
            if not isinstance(item, dict) or set(item) not in ({"path", "sha256", "edits"}, {"path", "sha256", "content"}):
                _fail("每个文件需含 path、sha256 和 edits 或 content")
            path = self._path(item["path"], must_exist=self.source)
            if item["path"] in seen:
                _fail("补丁不能重复指定同一文件")
            seen.add(item["path"])
            if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                _fail("原文件 sha256 无效")
            old_raw = path.read_bytes() if self.source else None
            if old_raw is not None and _sha(old_raw) != item["sha256"]:
                _fail(f"文件已更新：{item['path']}；请重新生成诊断后修复", 409)
            try:
                old = old_raw.decode("utf-8") if old_raw is not None else ""
            except UnicodeDecodeError:
                _fail("只支持 UTF-8 源码文件")
            new = old
            if "content" in item:
                if not isinstance(item["content"], str):
                    _fail("content 必须是字符串")
                new = item["content"]
            else:
                edits = item["edits"]
                if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
                    _fail("edits 需包含 1 至 100 处替换")
                for edit in edits:
                    if not isinstance(edit, dict) or set(edit) != {"before", "after"} or not isinstance(edit["before"], str) or not isinstance(edit["after"], str) or not edit["before"]:
                        _fail("每处替换需含非空 before 和字符串 after")
                    if old_raw is not None:
                        if new.count(edit["before"]) != 1:
                            _fail(f"原代码在 {item['path']} 中不唯一或不存在，请重新生成修复", 409)
                        new = new.replace(edit["before"], edit["after"], 1)
                if old_raw is None:
                    old = "\n".join(edit["before"] for edit in edits)
                    new = "\n".join(edit["after"] for edit in edits)
            try:
                raw = new.encode("utf-8", "strict")
            except UnicodeError:
                _fail("修复代码包含不完整的 Unicode 字符，请重新生成")
            if len(raw) > MAX_FILE:
                _fail("单个修复文件不能超过 1 MB")
            if old_raw is not None and raw == old_raw:
                _fail(f"文件没有变化：{item['path']}")
            diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/" + item["path"], tofile="b/" + item["path"]))
            prepared.append({"path": item["path"], "old_sha256": item["sha256"], "new_sha256": _sha(raw), "content": new,
                             "diff": diff, "unverified": old_raw is None,
                             "additions": sum(line.startswith("+") and not line.startswith("+++") for line in diff.splitlines()),
                             "deletions": sum(line.startswith("-") and not line.startswith("---") for line in diff.splitlines())})
        result = {"preview_id": uuid.uuid4().hex, "session_id": plan["session_id"], "summary": self.redact(plan["summary"], 2000),
                  "files": prepared, "checks": self._check_names(prepared), "can_apply": self.source,
                  "restart_required": any(not f["path"].startswith("frontend/") for f in prepared), "expires_at": time.time() + TTL,
                  "plan": plan}
        with self.lock:
            self.previews[result["preview_id"]] = result
        return self._public_preview(result)

    @staticmethod
    def _check_names(files):
        names = []
        if any(f["path"].endswith(".py") for f in files):
            names.append("Python 语法检查")
        if any(f["path"].endswith(".json") for f in files):
            names.append("JSON 格式检查")
        if any(f["path"].startswith("frontend/") for f in files):
            names += ["TypeScript 类型检查", "前端构建"]
        if any(f["path"].endswith((".ps1", ".sh")) for f in files):
            names.append("脚本仅保存，不自动执行")
        return names

    @staticmethod
    def _public_preview(result):
        return {key: ([{k: v for k, v in item.items() if k != "content"} for item in value] if key == "files" else value)
                for key, value in result.items() if key != "plan"}

    def export(self, preview_id: str) -> bytes:
        return json.dumps(self._preview(preview_id)["plan"], ensure_ascii=False, indent=2).encode("utf-8")

    def _preview(self, identifier):
        with self.lock:
            self._expire()
            if identifier not in self.previews:
                _fail("修复预览已过期，请重新解析", 409)
            return self.previews[identifier]

    def _check_current(self, files, *, rollback=False):
        for item in files:
            path = self._path(item["path"], must_exist=True)
            expected = {item["new_sha256"], item["old_sha256"]} if rollback else {item["old_sha256"]}
            if _sha(path.read_bytes()) not in expected:
                _fail(f"文件已更新：{item['path']}；为保留后续修改，操作未执行", 409)

    def _new_operation(self, kind, restart=False):
        with self.lock:
            self._check_open()
            if any(item["status"] == "running" for item in self.operations.values()):
                _fail("已有修复操作正在执行", 409)
            operation = {"operation_id": uuid.uuid4().hex, "kind": kind, "status": "running", "stage": "准备中", "progress": 0,
                         "checks": [], "rollback_available": False, "restart_required": restart, "frontend_rebuilt": False}
            self.operations[operation["operation_id"]] = operation
            (self.backups / operation["operation_id"]).mkdir(parents=True, mode=0o700)
            self._persist(operation)
            return operation

    def _start(self, callback, *args):
        thread = threading.Thread(target=callback, args=args, daemon=False, name="inkreference-repair")
        self.threads.append(thread)
        thread.start()

    def _check_open(self):
        if self.closed.is_set():
            _fail("应用正在关闭，修复操作已取消", 409)

    def apply(self, preview_id):
        if not self.source:
            _fail("打包版不能直接修改程序文件，请导出修复方案并在源码环境应用", 409)
        preview = self._preview(preview_id)
        self._check_current(preview["files"])
        operation = self._new_operation("source", preview["restart_required"])
        operation["files"] = copy.deepcopy(preview["files"])
        self._start(self._apply_owned, operation)
        return self.operation(operation["operation_id"])

    def operation(self, operation_id):
        with self.lock:
            operation = self.operations.get(operation_id)
            if operation is None:
                _fail("未找到修复操作", 404)
            return copy.deepcopy({key: value for key, value in operation.items() if key != "files"})

    def recent_operations(self):
        with self.lock:
            return {"operations": [self.operation(identifier) for identifier in list(self.operations)[-12:][::-1]]}

    def _update(self, operation, **values):
        with self.lock:
            operation.update(values)
            self._persist(operation)

    def _write(self, path, raw):
        temporary = path.with_name(path.name + ".inkrepair-" + uuid.uuid4().hex)
        try:
            temporary.write_bytes(raw)
            temporary.chmod(path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _backup(self, operation):
        directory = self.backups / operation["operation_id"]
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for number, item in enumerate(operation["files"]):
            target = directory / f"{number}.original"
            target.write_bytes(self._path(item["path"], must_exist=True).read_bytes())
            target.chmod(0o600)
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps([{key: item[key] for key in ("path", "old_sha256", "new_sha256")} for item in operation["files"]]), encoding="utf-8")
        manifest.chmod(0o600)
        self._persist(operation)

    def _restore(self, operation, *, verify=True):
        if verify:
            self._check_current(operation["files"], rollback=True)
        directory = self.backups / operation["operation_id"]
        # Validate every backup before touching any source file.
        originals = []
        for number, item in enumerate(operation["files"]):
            raw = (directory / f"{number}.original").read_bytes()
            if _sha(raw) != item["old_sha256"]:
                _fail("修复备份校验失败，未恢复文件", 409)
            originals.append(raw)
        for item, raw in zip(operation["files"], originals):
            self._write(self._path(item["path"], must_exist=True), raw)

    def _static_checks(self, operation):
        for item in operation["files"]:
            name = item["path"]
            raw = self._path(name, must_exist=True).read_bytes()
            if name.endswith(".py"):
                compile(raw, name, "exec", dont_inherit=True)
            elif name.endswith(".json"):
                json.loads(raw)
        for name in self._check_names(operation["files"]):
            if name.startswith(("Python", "JSON", "脚本")):
                operation["checks"].append({"name": name, "ok": True, "detail": "完成"})

    def _frontend_check(self, operation, *, restore=False):
        if not any(item["path"].startswith("frontend/") for item in operation["files"]):
            return
        node = shutil.which("node")
        frontend = self.root / "frontend"
        tsc = frontend / "node_modules" / "typescript" / "bin" / "tsc"
        vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
        if not node or not tsc.is_file() or not vite.is_file():
            _fail("缺少前端构建工具；请先运行项目 install，再重新应用修复")
        output_dir = frontend / (".repair-build-" + operation["operation_id"])
        try:
            commands = [("TypeScript 类型检查", [node, str(tsc), "--noEmit"]),
                        ("前端构建", [node, str(vite), "build", "--outDir", str(output_dir)])]
            for index, (name, command) in enumerate(commands):
                self._update(operation, stage=("恢复界面" if restore else name), progress=60 + index * 15)
                self._check_open()
                with self.lock:
                    self._check_open()
                    process = subprocess.Popen(command, cwd=frontend, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               env={**os.environ, "NO_COLOR": "1"},
                                               **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}))
                    self.processes.add(process)
                try:
                    try:
                        stdout, stderr = process.communicate(timeout=180)
                    except subprocess.TimeoutExpired:
                        from .conversions import ConversionStore
                        ConversionStore._terminate_tree(process)
                        stdout, stderr = process.communicate(timeout=2)
                        _fail("前端检查超时，修复已取消")
                finally:
                    with self.lock:
                        self.processes.discard(process)
                self._check_open()
                if process.returncode:
                    # Build diagnostics are source diagnostics, not user request data.
                    detail = self.redact((stdout + stderr).decode("utf-8", "replace"), 6000)
                    operation["checks"].append({"name": name, "ok": False, "detail": detail})
                    _fail(f"{name}未通过")
                if not restore:
                    operation["checks"].append({"name": name, "ok": True, "detail": "完成"})
            if not (output_dir / "index.html").is_file():
                _fail("前端构建未生成入口")
            # Merge immutable hashed assets, then atomically publish index.html.
            # Existing clients keep their old assets until they reload.
            dist = frontend / "dist"
            dist.mkdir(exist_ok=True)
            for path in output_dir.rglob("*"):
                self._check_open()
                if path.is_file() and path.name != "index.html":
                    target = dist / path.relative_to(output_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
            index = dist / "index.html"
            if not index.exists():
                index.write_bytes(b"")
            self._check_open()
            self._write(index, (output_dir / "index.html").read_bytes())
            self._update(operation, frontend_rebuilt=True)
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def _apply_owned(self, operation):
        written = []
        try:
            self._update(operation, stage="校验当前源码", progress=10)
            self._check_open()
            self._check_current(operation["files"])
            self._backup(operation)
            self._update(operation, stage="写入修复", progress=30)
            for item in operation["files"]:
                self._check_open()
                path = self._path(item["path"], must_exist=True)
                if _sha(path.read_bytes()) != item["old_sha256"]:
                    _fail(f"文件已更新：{item['path']}", 409)
                self._write(path, item["content"].encode("utf-8"))
                written.append(item)
            self._update(operation, stage="检查修复", progress=50)
            self._static_checks(operation)
            self._frontend_check(operation)
            self._check_open()
            self._update(operation, status="succeeded", stage="修复已应用", progress=100, rollback_available=True)
        except Exception as error:
            restore_error = None
            try:
                for number, item in enumerate(operation["files"]):
                    if item in written:
                        path = self._path(item["path"], must_exist=True)
                        if _sha(path.read_bytes()) != item["new_sha256"]:
                            raise DataError(409, f"{item['path']} 有后续修改，未强制覆盖")
                        self._write(path, (self.backups / operation["operation_id"] / f"{number}.original").read_bytes())
            except Exception as exc:
                restore_error = self.redact(str(exc), 600)
            message = self.redact(str(error), 1200)
            self._update(operation, status="failed", stage="修复未应用" if not restore_error else "需要手动恢复", progress=100,
                         error=message, rollback_available=bool(restore_error), auto_rolled_back=bool(written) and not restore_error,
                         **({"restore_error": restore_error} if restore_error else {}))

    def rollback(self, operation_id):
        if not self.source:
            _fail("打包版不能恢复源码修复", 409)
        with self.lock:
            operation = self.operations.get(operation_id)
            if not operation or operation["kind"] != "source" or not operation.get("rollback_available") or operation["status"] not in {"succeeded", "failed"}:
                _fail("此操作没有可恢复的源码备份", 409)
            if any(item["status"] == "running" for item in self.operations.values()):
                _fail("已有修复操作正在执行", 409)
            self._check_current(operation["files"], rollback=True)
            self._update(operation, status="running", stage="恢复修复前版本", progress=10)
        self._start(self._rollback_owned, operation)
        return self.operation(operation_id)

    def _rollback_owned(self, operation):
        try:
            self._restore(operation)
            self._frontend_check(operation, restore=True)
            self._update(operation, status="rolled_back", progress=100, stage="已恢复修复前版本", rollback_available=False)
        except Exception as error:
            self._update(operation, status="failed", progress=100, stage="恢复未完成", error=self.redact(str(error), 1200))

    def rebuild_cache(self, document_id: str, version_id: str | None = None):
        if self.cache_rebuilder is None:
            _fail("当前版本没有可重建的章节缓存", 409)
        if not document_id or len(document_id) > 200 or (version_id is not None and (not version_id or len(version_id) > 200)):
            _fail("作品或版本标识无效")
        operation = self._new_operation("chapter_cache")
        def run():
            try:
                self._check_open()
                self._update(operation, progress=15, stage="重建章节索引")
                result = self.cache_rebuilder(document_id, version_id)
                operation["checks"].append({"name": "章节缓存重建", "ok": True, "detail": "原文与版本保留"})
                self._update(operation, status="succeeded", progress=100, stage="章节缓存已重建", result=result)
            except Exception as error:
                self._update(operation, status="failed", progress=100, stage="缓存重建未完成", error=self.redact(str(error), 1000))
        self._start(run)
        return self.operation(operation["operation_id"])

    def active_threads(self):
        with self.lock:
            return [thread for thread in self.threads if thread.is_alive()]

    def shutdown(self, timeout: float = 2.5):
        deadline = time.monotonic() + min(2.5, max(0.1, timeout))
        self.closed.set()
        with self.lock:
            processes = list(self.processes)
        if processes:
            from .conversions import ConversionStore
            for process in processes:
                ConversionStore._terminate_tree(process)
        for thread in self.active_threads():
            if thread.is_alive():
                thread.join(timeout=max(0, deadline - time.monotonic()))
