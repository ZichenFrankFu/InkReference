#!/usr/bin/env python3
"""Portable source-checkout installer/launcher; never changes global tools.

This module only uses the standard library so diagnostics also work before the
application's dependencies are installed. Packaged end users do not need it.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PYTHON = "Python 3.11–3.13"
PROBE = (
    "import importlib.util,json,sys;print(json.dumps({"
    "'executable':sys.executable,'version':list(sys.version_info[:3]),"
    "'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
    "'pip':importlib.util.find_spec('pip') is not None,"
    "'app':importlib.util.find_spec('inkreference') is not None,"
    "'desktop':importlib.util.find_spec('webview') is not None,"
    "'mineru':importlib.util.find_spec('mineru') is not None,"
    "'missing':[name for name in ('fastapi','uvicorn','multipart','httpx',"
    "'langgraph','markdownify','markdown_it','keyring') if importlib.util.find_spec(name) is None]}))"
)


class EnvironmentError(RuntimeError):
    """An actionable source-environment diagnostic."""


@dataclass(frozen=True)
class Runtime:
    executable: str
    prefix: tuple[str, ...] = ()
    label: str = ""
    conda_name: str | None = None

    def command(self, *arguments: str) -> list[str]:
        return [self.executable, *self.prefix, *map(str, arguments)]

    def environment(self) -> dict[str, str]:
        # Do not mutate the caller's process environment, activate a shell, or
        # route Python child processes through an unrelated PYTHONHOME.
        environment = os.environ.copy()
        environment.pop("PYTHONHOME", None)
        return environment

    def tool_command(self, executable: str, *arguments: str) -> list[str]:
        if self.conda_name:
            return [self.executable, "run", "--no-capture-output", "-n", self.conda_name, executable, *arguments]
        return [executable, *arguments]


def venv_python(root: Path, platform: str | None = None) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if (platform or sys.platform) == "win32" else "bin/python")


def capture(command: Sequence[str], *, cwd: Path | None = None, environment: dict | None = None) -> str:
    try:
        result = subprocess.run(
            list(command), cwd=cwd, env=environment, text=True,
            encoding="utf-8", errors="replace", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=45, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentError(f"Cannot run {command[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1600:]
        raise EnvironmentError(f"Command failed ({result.returncode}): {command[0]}\n{detail}")
    return result.stdout.strip()


def probe(runtime: Runtime) -> dict:
    output = capture(runtime.command("-c", PROBE), environment=runtime.environment())
    # Conda can prepend informational lines even with --no-capture-output.
    for line in reversed(output.splitlines()):
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict) and "version" in result:
            return result
    raise EnvironmentError(f"Could not inspect Python in {runtime.label or runtime.executable}.")


def supported(details: dict) -> bool:
    version = details.get("version", ())
    return len(version) >= 2 and version[0] == 3 and 11 <= version[1] <= 13


def checked_runtime(runtime: Runtime, *, needs_pip: bool = False) -> Runtime:
    details = probe(runtime)
    if not supported(details):
        found = ".".join(map(str, details.get("version", ())))
        raise EnvironmentError(f"{runtime.label}: found Python {found}; use {SUPPORTED_PYTHON}. The environment was not changed.")
    if needs_pip and not details.get("pip"):
        raise EnvironmentError(f"pip is missing in {runtime.label}. Install pip in this environment, then retry.")
    return runtime


def resolve_conda(name: str) -> Runtime:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise EnvironmentError("Use an existing Conda environment name containing letters, numbers, _, . or -.")
    explicit = os.environ.get("CONDA_EXE")
    executable = explicit if explicit and Path(explicit).is_file() else shutil.which("conda")
    if not executable:
        raise EnvironmentError("Conda was explicitly selected but is not on PATH. Activate your existing environment and use --current-environment, or use the default .venv.")
    output = capture([executable, "env", "list", "--json"])
    try:
        data = json.loads(output)
    except ValueError as exc:
        raise EnvironmentError("Conda did not return its environment list. No environment was created.") from exc
    paths = data.get("envs", [])
    exists = any(Path(path).name.casefold() == name.casefold() for path in paths)
    if name == "base":
        exists = bool(paths)
    if not exists:
        raise EnvironmentError(f"Conda environment {name!r} does not exist. Create/manage it yourself, or use the default project .venv.")
    return Runtime(executable, ("run", "--no-capture-output", "-n", name, "python"), f"Conda: {name}", name)


def explicit_python(value: str) -> Runtime:
    path = Path(value).expanduser()
    executable = str(path.resolve()) if path.is_file() else shutil.which(value)
    if not executable:
        raise EnvironmentError(f"Python executable was not found: {value}")
    return Runtime(executable, label=f"Python: {executable}")


def creation_python(value: str | None = None) -> Runtime:
    if value:
        return checked_runtime(explicit_python(value))
    candidates = [Runtime(sys.executable, label="current Python")]
    if sys.platform == "win32":
        launcher = shutil.which("py")
        if launcher:
            candidates.extend(Runtime(launcher, (f"-3.{minor}",), f"Python 3.{minor}") for minor in (12, 11, 13))
    for name in ("python3.12", "python3.11", "python3.13", "python3", "python"):
        executable = shutil.which(name)
        if executable:
            candidates.append(Runtime(executable, label=name))
    for candidate in candidates:
        try:
            if supported(probe(candidate)):
                return candidate
        except EnvironmentError:
            continue
    raise EnvironmentError(f"Install {SUPPORTED_PYTHON}, then retry; or choose its executable with --python PATH. System Python was not changed.")


def resolve_runtime(args: argparse.Namespace, root: Path = ROOT) -> Runtime:
    if getattr(args, "conda_environment", None):
        return checked_runtime(resolve_conda(args.conda_environment))
    if getattr(args, "current_environment", False):
        return checked_runtime(Runtime(sys.executable, label="current Python environment"))
    if getattr(args, "python", None) and getattr(args, "action", None) != "install":
        return checked_runtime(explicit_python(args.python))
    target = venv_python(root)
    if not target.is_file():
        raise EnvironmentError("The project .venv is missing or incomplete. Run the install command first, or explicitly use --current-environment.")
    return checked_runtime(Runtime(str(target), label="project .venv"))


def run_phase(command: Sequence[str], description: str, *, cwd: Path = ROOT, runtime: Runtime | None = None) -> None:
    print(f"\n[install] {description}", flush=True)
    environment = runtime.environment() if runtime else os.environ.copy()
    try:
        process = subprocess.Popen(list(command), cwd=cwd, env=environment)
    except OSError as exc:
        raise EnvironmentError(f"{description}: {exc}") from exc
    # Downloads/builds have no honest percentage. Show an indeterminate bar
    # while retaining the child tool's real download/build output.
    stop = threading.Event()

    def indicate() -> None:
        step = 0
        while not stop.wait(1.0):
            bar = " " * (step % 8) + "===="
            print(f"[{'%-11s' % bar}] {description} · {step + 1}s", flush=True)
            step += 1

    worker = threading.Thread(target=indicate, daemon=True)
    worker.start()
    try:
        result = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        stop.set()
        worker.join(timeout=1)
    if result:
        raise EnvironmentError(f"{description} failed (exit code {result}). Review the tool output above.")
    print(f"[===========] {description} complete", flush=True)


def frontend_tools(runtime: Runtime) -> tuple[list[str], list[str], str]:
    if runtime.conda_name:
        node = runtime.tool_command("node")
        npm = runtime.tool_command("npm.cmd" if sys.platform == "win32" else "npm")
    else:
        node_path = shutil.which("node")
        npm_path = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
        if not node_path or not npm_path:
            raise EnvironmentError("Frontend builds need Node.js 22 or 24 with npm on PATH. Install Node.js yourself, then retry. Packaged apps do not need Node.js.")
        node, npm = [node_path], [npm_path]
    try:
        version = capture([*node, "--version"], environment=runtime.environment()).splitlines()[-1]
        capture([*npm, "--version"], environment=runtime.environment())
    except EnvironmentError as exc:
        raise EnvironmentError("Node.js/npm could not run. Install Node.js 22 or 24 on PATH (or in the explicitly selected Conda environment). No system tools were installed.") from exc
    match = re.fullmatch(r"v?(\d+)\.\d+\.\d+(?:[-+].*)?", version)
    if not match or int(match.group(1)) not in (22, 24):
        raise EnvironmentError(f"Found Node.js {version}; frontend builds use Node.js 22 or 24. Select that version on PATH; no global tools were changed.")
    return node, npm, version


def install(args: argparse.Namespace, root: Path = ROOT) -> int:
    print("[1/5] Checking Python and frontend tools", flush=True)
    for path in ("pyproject.toml", "requirements-lock.txt", "frontend/package-lock.json"):
        if not (root / path).is_file():
            raise EnvironmentError(f"{path} is missing. Use a complete checkout of InkReference.")
    if args.current_environment or args.conda_environment:
        runtime = resolve_runtime(args, root)
    elif (root / ".venv").exists():
        runtime = resolve_runtime(args, root)
    else:
        base = creation_python(args.python)
        # Fail early before creating a partial environment when Node is absent.
        frontend_tools(base)
        print("[2/5] Creating project .venv", flush=True)
        run_phase(base.command("-m", "venv", str(root / ".venv")), "Create project .venv", cwd=root, runtime=base)
        runtime = resolve_runtime(args, root)
    checked_runtime(runtime, needs_pip=True)
    _, npm, node_version = frontend_tools(runtime)
    print(f"[2/5] Environment: {runtime.label}; frontend: Node.js {node_version}", flush=True)
    extras = [] if args.browser_only else ["desktop"]
    if args.dev:
        extras.insert(0, "dev")
    project = "." + ("[" + ",".join(extras) + "]" if extras else "")
    pip = runtime.command("-m", "pip", "install", "-c", str(root / "requirements-lock.txt"), "-e", project)
    print("[3/5] Python dependencies", flush=True)
    run_phase(pip, "Install Python dependencies", cwd=root, runtime=runtime)
    if args.mineru:
        run_phase(runtime.command("-m", "pip", "install", "-r", str(root / "requirements-mineru.txt")), "Install optional MinerU", cwd=root, runtime=runtime)
    print("[4/5] Frontend dependencies", flush=True)
    frontend = root / "frontend"
    if not (frontend / "package-lock.json").is_file():
        raise EnvironmentError("frontend/package-lock.json is missing. Use a complete checkout of InkReference.")
    run_phase([*npm, "ci"], "Install frontend dependencies", cwd=frontend, runtime=runtime)
    print("[5/5] Build interface", flush=True)
    run_phase([*npm, "run", "build"], "Build interface", cwd=frontend, runtime=runtime)
    print("\nReady. Use scripts/open.ps1 (Windows) or ./scripts/open.sh (macOS).", flush=True)
    if args.current_environment or args.conda_environment:
        print("Use the same explicit environment option when opening the app.", flush=True)
    return 0


def open_app(args: argparse.Namespace, root: Path = ROOT) -> int:
    print("[open] [====       ] Checking environment and interface", flush=True)
    runtime = resolve_runtime(args, root)
    details = probe(runtime)
    if not details.get("app"):
        raise EnvironmentError(f"InkReference is not installed in {runtime.label}. Run install with the same environment option.")
    if details.get("missing"):
        raise EnvironmentError(f"Application dependencies are missing: {', '.join(details['missing'])}. Run install with the same environment option.")
    if not (root / "frontend/dist/index.html").is_file():
        raise EnvironmentError("The interface build is missing. Run the install command first.")
    desktop = not args.browser and not args.no_open
    if desktop and (not details.get("desktop") or sys.platform not in ("win32", "darwin")):
        print("[open] Desktop runtime unavailable; opening the browser interface.", flush=True)
        desktop = False
    command = runtime.command("-m", "inkreference", "--host", "127.0.0.1", "--port", str(args.port))
    if desktop:
        command.append("--desktop")
    if args.no_open:
        command.append("--no-open")
    if args.data_dir:
        command.extend(("--data-dir", str(Path(args.data_dir).expanduser().resolve())))
    print(f"[open] [===========] Starting {'desktop' if desktop else 'browser'} interface · http://127.0.0.1:{args.port}", flush=True)
    try:
        return subprocess.call(command, cwd=root, env=runtime.environment())
    except KeyboardInterrupt:
        return 0


def doctor(args: argparse.Namespace, root: Path = ROOT) -> int:
    report: dict = {"platform": sys.platform, "project": str(root), "ok": True, "checks": {}}
    try:
        runtime = resolve_runtime(args, root)
        details = probe(runtime)
        dependencies_ok = bool(details.get("app")) and not details.get("missing")
        report["checks"]["python"] = {"ok": dependencies_ok, "environment": runtime.label, **details}
        if not dependencies_ok:
            report["checks"]["python"]["message"] = "Application dependencies are incomplete. Run install with the same environment option."
            report["ok"] = False
    except EnvironmentError as exc:
        report["checks"]["python"] = {"ok": False, "message": str(exc)}
        report["ok"] = False
        runtime = Runtime(sys.executable, label="diagnostic Python")
    try:
        _, _, version = frontend_tools(runtime)
        report["checks"]["frontend_tools"] = {"ok": True, "node": version}
    except EnvironmentError as exc:
        # Node is needed for source builds, never for opening an already-built UI.
        report["checks"]["frontend_tools"] = {"ok": False, "required_for": "source builds only", "message": str(exc)}
    report["checks"]["interface"] = {"ok": (root / "frontend/dist/index.html").is_file()}
    if not report["checks"]["interface"]["ok"]:
        report["checks"]["interface"]["message"] = "The interface build is missing. Run install."
        report["ok"] = False
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"InkReference · {sys.platform} · {root}")
        for name, check in report["checks"].items():
            status = "OK" if check["ok"] else "CHECK"
            detail = check.get("message") or check.get("environment") or check.get("node") or ""
            print(f"[{status}] {name}: {detail}")
    return 0 if report["ok"] else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="InkReference Windows/macOS source setup and launcher")
    subparsers = result.add_subparsers(dest="action", required=True)
    for action in ("install", "open", "doctor", "exec"):
        child = subparsers.add_parser(action)
        group = child.add_mutually_exclusive_group()
        group.add_argument("--current-environment", action="store_true", help="Use this Python environment explicitly; no .venv creation")
        group.add_argument("--conda-environment", metavar="NAME", help="Use an existing Conda environment; never creates or changes it")
        group.add_argument("--python", metavar="PATH", help="Install: Python used to create .venv. Other commands: explicit target Python")
        if action == "install":
            child.add_argument("--browser-only", action="store_true")
            child.add_argument("--mineru", action="store_true")
            child.add_argument("--dev", action="store_true", help="Also install test/development dependencies")
        elif action == "open":
            child.add_argument("--browser", action="store_true")
            child.add_argument("--no-open", action="store_true")
            child.add_argument("--port", type=int, default=8000)
            child.add_argument("--data-dir")
        elif action == "doctor":
            child.add_argument("--json", action="store_true")
        else:
            child.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments passed to the selected Python, after --")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.action == "open" and not 1 <= arguments.port <= 65535:
        parser().error("--port must be between 1 and 65535")
    try:
        if arguments.action == "install":
            return install(arguments)
        if arguments.action == "open":
            return open_app(arguments)
        if arguments.action == "doctor":
            return doctor(arguments)
        runtime = resolve_runtime(arguments)
        extra = arguments.arguments
        if extra[:1] == ["--"]:
            extra = extra[1:]
        if not extra:
            raise EnvironmentError("exec needs Python arguments after --, for example: exec -- scripts/package.py")
        return subprocess.call(runtime.command(*extra), cwd=ROOT, env=runtime.environment())
    except EnvironmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
