"""A synthetic checkout verifies real edits, isolation, checks and recovery."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from inkreference.database import DataError
from inkreference.quick_repair import PROTOCOL, QuickRepair
from inkreference.repair_routes import install_repair_routes


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "checkout"
    (root / "backend/inkreference").mkdir(parents=True)
    (root / "frontend/src").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="repair-fixture"\nversion="0.0.0"\n')
    (root / "backend/inkreference/documents.py").write_bytes(b"def value():\r\n    return 1\r\n")
    (root / "frontend/src/App.tsx").write_text("export default function App() { return <main>Fixture</main> }\n")
    return root


@pytest.fixture
def repair(source, tmp_path):
    service = QuickRepair(tmp_path / "data", source_root=source)
    yield service
    service.shutdown()


def plan(service, path="backend/inkreference/documents.py", before="return 1", after="return 2", **overrides):
    session = service.prompt("点击保存之后界面没有更新", "chapters")
    raw = (service.root / path).read_bytes()
    result = {"protocol": PROTOCOL, "session_id": session["session_id"], "summary": "修复合成函数返回值", "files": [
        {"path": path, "sha256": sha(raw), "edits": [{"before": before, "after": after}]}]}
    result.update(overrides)
    return result


def wait(service, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        operation = service.operation(identifier)
        assert 0 <= operation["progress"] <= 100
        if operation["status"] != "running":
            return operation
        time.sleep(0.005)
    pytest.fail("repair worker did not complete")


def test_actual_edit_diff_checks_exact_bytes_and_restart_recovery(repair):
    target = repair.root / "backend/inkreference/documents.py"
    original = target.read_bytes()
    preview = repair.preview(json.dumps(plan(repair)))
    assert preview["can_apply"] and preview["restart_required"]
    assert "+    return 2" in preview["files"][0]["diff"]
    assert "content" not in preview["files"][0]
    assert target.read_bytes() == original
    operation = repair.apply(preview["preview_id"])
    final = wait(repair, operation["operation_id"])
    assert final["status"] == "succeeded", final
    assert final["checks"] == [{"name": "Python 语法检查", "ok": True, "detail": "完成"}]
    assert target.read_bytes() == original.replace(b"return 1", b"return 2")
    assert not list(target.parent.rglob("*.pyc"))
    assert final["rollback_available"]
    restarted = QuickRepair(repair.backups.parent, source_root=repair.root)
    try:
        assert restarted.operation(final["operation_id"])["status"] == "succeeded"
        result = restarted.rollback(final["operation_id"])
        restored = wait(restarted, result["operation_id"])
        assert restored["status"] == "rolled_back", restored
        assert target.read_bytes() == original
    finally:
        restarted.shutdown()


def test_compile_failure_rolls_back_every_file_without_running_code(repair):
    sentinel = repair.root / "never-created"
    patch = plan(repair, after=f"__import__('pathlib').Path({str(sentinel)!r}).write_text('bad')\nthis is invalid syntax !")
    target = repair.root / patch["files"][0]["path"]
    original = target.read_bytes()
    preview = repair.preview(json.dumps(patch))
    result = wait(repair, repair.apply(preview["preview_id"])["operation_id"])
    assert result["status"] == "failed" and result["auto_rolled_back"]
    assert target.read_bytes() == original
    assert not sentinel.exists()


def test_whole_file_json_check_and_failed_multifile_rollback(repair):
    target = repair.root / "frontend/src/settings.json"
    target.write_text('{"scheme":"blue"}\n')
    patch = plan(repair)
    patch["files"].append({"path": "frontend/src/settings.json", "sha256": sha(target.read_bytes()), "content": "{invalid}"})
    original = (repair.root / "backend/inkreference/documents.py").read_bytes()
    preview = repair.preview(json.dumps(patch))
    result = wait(repair, repair.apply(preview["preview_id"])["operation_id"])
    assert result["status"] == "failed" and result["auto_rolled_back"]
    assert target.read_text() == '{"scheme":"blue"}\n'
    assert (repair.root / "backend/inkreference/documents.py").read_bytes() == original


@pytest.mark.parametrize("name", ["../outside.py", "/etc/passwd", "backend/inkreference/../../secret.py", "C:/Users/Test/file.py", "backend\\inkreference\\documents.py", "backend/inkreference//documents.py", "backend/inkreference/quick_repair.py", "backend/inkreference/repair_routes.py", "frontend/package.json", "frontend/vite.config.ts", "scripts/.env.py", "frontend/src/node_modules/a.ts", "data/inkreference.sqlite3", "frontend/src/.secrets.json"])
def test_paths_and_checkrunner_are_rejected(repair, name):
    patch = plan(repair)
    patch["files"][0]["path"] = name
    with pytest.raises(DataError) as error:
        repair.preview(json.dumps(patch))
    assert error.value.status_code == 422


def test_symlink_target_and_parent_are_rejected(repair, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n")
    linked = repair.root / "backend/inkreference/link.py"
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    patch = plan(repair)
    patch["files"][0].update(path="backend/inkreference/link.py", sha256=sha(outside.read_bytes()))
    with pytest.raises(DataError):
        repair.preview(json.dumps(patch))
    linked.unlink()
    linked.parent.joinpath("nested").symlink_to(tmp_path, target_is_directory=True)
    patch["files"][0]["path"] = "backend/inkreference/nested/outside.py"
    with pytest.raises(DataError):
        repair.preview(json.dumps(patch))


def test_stale_before_preview_apply_and_rollback_never_overwrite(repair):
    target = repair.root / "backend/inkreference/documents.py"
    patch = plan(repair)
    target.write_text("def value():\n    return 3\n")
    with pytest.raises(DataError) as error:
        repair.preview(json.dumps(patch))
    assert error.value.status_code == 409
    patch = plan(repair, before="return 3", after="return 4")
    preview = repair.preview(json.dumps(patch))
    target.write_text("def value():\n    return 5\n")
    with pytest.raises(DataError) as error:
        repair.apply(preview["preview_id"])
    assert error.value.status_code == 409
    preview = repair.preview(json.dumps(plan(repair, before="return 5", after="return 6")))
    final = wait(repair, repair.apply(preview["preview_id"])["operation_id"])
    target.write_text("def value():\n    return 7\n")
    with pytest.raises(DataError) as error:
        repair.rollback(final["operation_id"])
    assert error.value.status_code == 409
    assert "return 7" in target.read_text()


def test_ambiguous_before_duplicate_json_unknown_commands_and_expiry(repair):
    patch = plan(repair, before="r", after="x")
    with pytest.raises(DataError) as error:
        repair.preview(json.dumps(patch))
    assert error.value.status_code == 409
    patch = plan(repair, commands=["rm -rf data"])
    with pytest.raises(DataError):
        repair.preview(json.dumps(patch))
    patch = plan(repair)
    reply = json.dumps(patch).replace('"summary":', '"summary":"duplicate","summary":')
    with pytest.raises(DataError):
        repair.preview(reply)
    repair.sessions[patch["session_id"]]["expires_at"] = 0
    with pytest.raises(DataError) as error:
        repair.preview(json.dumps(patch))
    assert error.value.status_code == 409


def test_redaction_diagnostics_and_error_metadata_never_capture_input(repair):
    secret = "sk-fake1234567890abcdef"
    user_body = "PRIVATE_FULL_TEXT_SHOULD_NEVER_APPEAR"
    repair.record_request("POST", "/api/studio/models?api_key=" + secret, 500, 23.4)
    try:
        raise ValueError(user_body + secret)
    except ValueError as error:
        repair.record_exception(error, path="/api/studio/documents?text=" + user_body)
    result = repair.prompt("保存失败 api_key='example-secret' " + secret + " https://user:pass@api.example.test/x?token=hidden", frontend_events=[{
        "kind": "error", "message": 'Authorization="Bearer aaaaaaaaaa"', "path": "C:\\Users\\Alice\\private.txt"}],
        context="/Users/Bob/project/password.txt password=abcdef /home/Carol/work")
    prompt = result["prompt"]
    for private in (secret, user_body, "example-secret", "user:pass", "hidden", "aaaaaaaaaa", "Alice", "Bob", "Carol", "abcdef"):
        assert private not in prompt, private
    assert "ValueError" in prompt and "frames" in prompt and "duration_ms" in prompt
    assert "source_files" in prompt and "sha256" in prompt
    assert "executable" not in result["diagnostics"]
    restarted = QuickRepair(repair.backups.parent, source_root=repair.root)
    assert any(event.get("type") == "ValueError" for event in restarted.events)


def test_successful_requests_are_memory_only(repair):
    repair.record_request("GET", "/api/studio/documents", 200, 1)
    assert not repair.backups.exists()


def test_frozen_preview_export_and_actual_cache_repair(source, tmp_path):
    calls = []
    service = QuickRepair(tmp_path / "frozen-data", source_root=source, frozen=True,
                          cache_rebuilder=lambda document, version: calls.append((document, version)) or {"id": document})
    try:
        assert not service.capabilities()["can_apply_source"]
        patch = plan(service)
        preview = service.preview(json.dumps(patch))
        assert not preview["can_apply"] and preview["files"][0]["unverified"]
        assert json.loads(service.export(preview["preview_id"])) == patch
        with pytest.raises(DataError) as error:
            service.apply(preview["preview_id"])
        assert error.value.status_code == 409
        result = wait(service, service.rebuild_cache("fixture-document", "fixture-version")["operation_id"])
        assert result["status"] == "succeeded" and not result["rollback_available"]
        assert calls == [("fixture-document", "fixture-version")]
    finally:
        service.shutdown()


def test_fixed_frontend_commands_do_not_run_package_scripts(repair, monkeypatch):
    target = repair.root / "frontend/src/App.tsx"
    sentinel = repair.root / "must-not-run"
    (repair.root / "frontend/package.json").write_text(json.dumps({"scripts": {"build": "touch " + str(sentinel)}}))
    for path in ("typescript/bin/tsc", "vite/bin/vite.js"):
        entry = repair.root / "frontend/node_modules" / path
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("fixture")
    commands = []
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            assert "shell" not in kwargs
            commands.append(command)
            if "--outDir" in command:
                out = Path(command[command.index("--outDir") + 1])
                (out / "assets").mkdir(parents=True)
                (out / "assets/new.js").write_text("fixture")
                (out / "index.html").write_text("<main>rebuilt</main>")
        def communicate(self, timeout):
            return b"", b""
    monkeypatch.setattr(repair, "_git_info", lambda: {})
    monkeypatch.setattr("inkreference.quick_repair.shutil.which", lambda name: "/fixture/node")
    monkeypatch.setattr("inkreference.quick_repair.subprocess.Popen", Process)
    preview = repair.preview(json.dumps(plan(repair, path="frontend/src/App.tsx", before="Fixture", after="Repaired")))
    final = wait(repair, repair.apply(preview["preview_id"])["operation_id"])
    assert final["status"] == "succeeded" and final["frontend_rebuilt"]
    assert len(commands) == 2
    assert Path(commands[0][1]).parts[-3:] == ("typescript", "bin", "tsc")
    assert Path(commands[1][1]).parts[-3:] == ("vite", "bin", "vite.js")
    assert all("shell" not in command for command in commands)
    assert not sentinel.exists()
    assert (repair.root / "frontend/dist/index.html").read_text() == "<main>rebuilt</main>"
    assert not list((repair.root / "frontend").glob(".repair-build-*"))


def test_interrupted_partial_write_rolls_back_only_verified_old_or_new_bytes(repair):
    second = repair.root / "backend/inkreference/second.py"
    second.write_text("value = 1\n")
    patch = plan(repair)
    patch["files"].append({"path": "backend/inkreference/second.py", "sha256": sha(second.read_bytes()),
                           "edits": [{"before": "value = 1", "after": "value = 2"}]})
    preview = repair.preview(json.dumps(patch))
    operation = repair._new_operation("source", True)
    operation["files"] = repair.previews[preview["preview_id"]]["files"]
    repair._backup(operation)
    first = operation["files"][0]
    (repair.root / first["path"]).write_bytes(first["content"].encode("utf-8"))
    restarted = QuickRepair(repair.backups.parent, source_root=repair.root)
    try:
        assert restarted.operation(operation["operation_id"])["rollback_available"]
        result = wait(restarted, restarted.rollback(operation["operation_id"])["operation_id"])
        assert result["status"] == "rolled_back", result
        assert b"return 1\r\n" in (repair.root / first["path"]).read_bytes()
        assert second.read_text() == "value = 1\n"
    finally:
        restarted.shutdown()


def test_exported_plan_can_be_explicitly_retargeted_to_local_session(repair, source, tmp_path):
    packaged = QuickRepair(tmp_path / "packaged", source_root=source, frozen=True)
    exported = plan(packaged)
    with pytest.raises(DataError) as error:
        repair.preview(json.dumps(exported))
    assert error.value.status_code == 409
    local = repair.prompt("导入导出的修复方案")
    preview = repair.preview(json.dumps(exported), session_id=local["session_id"])
    assert preview["can_apply"]
    assert preview["session_id"] == local["session_id"]


def test_shutdown_is_bounded_and_preserves_lease_for_unfinished_worker(repair, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    original = repair._static_checks
    def block(operation):
        entered.set()
        release.wait(timeout=5)
        original(operation)
    monkeypatch.setattr(repair, "_static_checks", block)
    target = repair.root / "backend/inkreference/documents.py"
    original_bytes = target.read_bytes()
    preview = repair.preview(json.dumps(plan(repair)))
    identifier = repair.apply(preview["preview_id"])["operation_id"]
    assert entered.wait(timeout=2)
    started = time.monotonic()
    repair.shutdown(timeout=0.1)
    assert time.monotonic() - started < 0.5
    assert repair.active_threads()
    with pytest.raises(DataError):
        repair.apply(preview["preview_id"])
    release.set()
    result = wait(repair, identifier)
    assert result["status"] == "failed" and result["auto_rolled_back"]
    assert target.read_bytes() == original_bytes


def test_shutdown_cancels_real_build_process_and_does_not_publish(repair, monkeypatch):
    import sys
    entered = repair.root / "build-entered"
    for name, body in (("typescript/bin/tsc", f"import time\nfrom pathlib import Path\nPath({str(entered)!r}).write_text('entered')\ntime.sleep(180)\n"),
                       ("vite/bin/vite.js", "raise RuntimeError('must not start next build')\n")):
        target = repair.root / "frontend/node_modules" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    old_dist = repair.root / "frontend/dist"
    old_dist.mkdir()
    (old_dist / "index.html").write_text("original interface")
    target = repair.root / "frontend/src/App.tsx"
    original = target.read_bytes()
    monkeypatch.setattr("inkreference.quick_repair.shutil.which", lambda name: sys.executable)
    preview = repair.preview(json.dumps(plan(repair, path="frontend/src/App.tsx", before="Fixture", after="Repaired")))
    identifier = repair.apply(preview["preview_id"])["operation_id"]
    deadline = time.monotonic() + 5
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered.exists()
    repair.shutdown()
    assert not repair.active_threads() and not repair.processes
    final = wait(repair, identifier)
    assert final["status"] == "failed" and final["auto_rolled_back"]
    assert not final["frontend_rebuilt"]
    assert target.read_bytes() == original
    assert (old_dist / "index.html").read_text() == "original interface"


def test_real_frontend_build_and_restore_in_temporary_checkout(repair):
    import shutil
    dependencies = Path(__file__).resolve().parents[1] / "frontend/node_modules"
    if not shutil.which("node") or not (dependencies / "vite/bin/vite.js").is_file():
        pytest.skip("real frontend dependencies are not installed in this test job")
    frontend = repair.root / "frontend"
    try:
        (frontend / "node_modules").symlink_to(dependencies, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    (frontend / "package.json").write_text('{"name":"repair-fixture","type":"module"}')
    (frontend / "tsconfig.json").write_text('{"compilerOptions":{"target":"ES2022","module":"ESNext","moduleResolution":"Bundler","jsx":"preserve","noEmit":true},"include":["src"]}')
    (frontend / "index.html").write_text('<main id="root"></main><script type="module" src="/src/main.ts"></script>')
    (frontend / "src/main.ts").write_text("import App from './App'; document.querySelector('#root')!.textContent = App();\n")
    target = frontend / "src/App.tsx"
    target.write_text("export default function App() { return 'Fixture'; }\n")
    preview = repair.preview(json.dumps(plan(repair, path="frontend/src/App.tsx", before="Fixture", after="Repaired")))
    operation = repair.apply(preview["preview_id"])
    # Real compiler/build checks can take more than the small unit worker deadline.
    deadline = time.monotonic() + 30
    while repair.operation(operation["operation_id"])["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    final = repair.operation(operation["operation_id"])
    assert final["status"] == "succeeded" and final["frontend_rebuilt"], final
    assert "Repaired" in "".join(path.read_text() for path in (frontend / "dist/assets").glob("*.js"))
    restored = repair.rollback(operation["operation_id"])
    deadline = time.monotonic() + 30
    while repair.operation(restored["operation_id"])["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    final = repair.operation(restored["operation_id"])
    assert final["status"] == "rolled_back", final
    assert "return 'Fixture'" in target.read_text()


def test_http_contract_has_async_operation_and_validation(source, tmp_path, monkeypatch):
    service = QuickRepair(tmp_path / "http-data", source_root=source)
    monkeypatch.setattr("inkreference.repair_routes.QuickRepair", lambda *args, **kwargs: service)
    app = FastAPI()
    @app.exception_handler(DataError)
    async def handle(_, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    class Runtime:
        data_dir = tmp_path / "http-data"
    install_repair_routes(app, Runtime())
    try:
        with TestClient(app) as client:
            prefix = "/api/studio/repair"
            assert client.get(prefix + "/capabilities").json()["mode"] == "source"
            assert client.post(prefix + "/prompt", json={"description": "save", "extra": "no"}).status_code == 422
            prompt = client.post(prefix + "/prompt", json={"description": "save", "topic": "chapters"}).json()
            patch = plan(service, session_id=prompt["session_id"])
            preview = client.post(prefix + "/preview", json={"reply": json.dumps(patch)})
            assert preview.status_code == 200, preview.text
            exported = client.post(prefix + "/export", json={"preview_id": preview.json()["preview_id"]})
            assert exported.status_code == 200 and json.loads(exported.content) == patch
            applied = client.post(prefix + "/apply", json={"preview_id": preview.json()["preview_id"]})
            assert applied.status_code == 200
            identifier = applied.json()["operation_id"]
            assert wait(service, identifier)["status"] == "succeeded"
            assert client.get(prefix + "/operations/" + identifier).json()["progress"] == 100
    finally:
        service.shutdown()
