#!/usr/bin/env python3
"""Build a self-contained desktop application on its target OS.

Run through package.ps1/package.sh to select a project or developer environment.
The installed application contains Python and its frontend; end users do not
need Python, Node or Conda. MinerU remains an optional external runtime.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_IDENTIFIER = "com.inkreference.desktop"


@contextmanager
def progress(description: str):
    """Give subprocesses, ZIP compression and DMG staging the same feedback."""
    print(f"[package] {description}", flush=True)
    stopped = threading.Event()
    started = time.monotonic()

    def indicate() -> None:
        step = 0
        while not stopped.wait(1):
            bar = (" " * (step % 8) + "====").ljust(11)
            print(f"[{bar}] {description} · {int(time.monotonic() - started)}s", flush=True)
            step += 1

    indicator = threading.Thread(target=indicate, daemon=True)
    indicator.start()
    try:
        yield
    finally:
        stopped.set()
        indicator.join(timeout=1)
    print(f"[===========] {description} complete", flush=True)


def run(command: list[str], description: str, *, cwd: Path = ROOT, environment: dict[str, str] | None = None) -> None:
    with progress(description):
        process = subprocess.Popen(command, cwd=cwd, env=environment)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)


def architecture(system: str, machine: str) -> str:
    normalized = machine.lower()
    if system == "darwin" and normalized in {"arm64", "aarch64"}:
        return "arm64"
    if normalized in {"amd64", "x86_64", "x64"}:
        return "x86_64" if system == "darwin" else "x64"
    raise ValueError(f"Unsupported build architecture: {system}/{machine}. Use native arm64 or x86_64 Python on macOS, or x64 Python on Windows.")


def pyinstaller_command(root: Path, output: Path, system: str, arch: str) -> list[str]:
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--windowed", "--noupx", "--name", "InkReference",
        "--distpath", str(output / "app"),
        "--workpath", str(root / "build" / f"pyinstaller-{system}-{arch}"),
        "--specpath", str(root / "build" / "pyinstaller-spec"),
        "--paths", str(root / "backend"),
        "--add-data", str(root / "frontend" / "dist") + ":frontend/dist",
    ]
    collect = ["inkreference", "webview", "keyring", "langgraph", "langgraph.checkpoint.sqlite"]
    hidden = [
        "inkreference.api", "uvicorn.logging", "uvicorn.loops.asyncio",
        "uvicorn.lifespan.on", "uvicorn.protocols.http.h11_impl",
    ]
    if system == "win32":
        collect += ["pythonnet", "clr_loader"]
        hidden += ["keyring.backends.Windows"]
    else:
        args += ["--osx-bundle-identifier", BUNDLE_IDENTIFIER, "--target-architecture", arch]
        hidden += ["webview.platforms.cocoa", "keyring.backends.macOS", "Security", "WebKit", "Cocoa", "objc"]
    for name in collect:
        args += ["--collect-all", name]
    for name in hidden:
        args += ["--hidden-import", name]
    for name in ("inkreference", "langgraph-checkpoint-sqlite"):
        args += ["--recursive-copy-metadata", name]
    args += ["--copy-metadata", "pywebview"]
    for name in ("PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3", "mineru"):
        args += ["--exclude-module", name]
    args.append(str(root / "packaging" / "desktop_entry.py"))
    return args


def copy_notices(root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "THIRD_PARTY_NOTICES.md", "LICENSE"):
        shutil.copy2(root / name, destination / name)
    shutil.copytree(root / "third_party", destination / "third_party", dirs_exist_ok=True)


def update_macos_plist(bundle: Path, version: str) -> None:
    path = bundle / "Contents" / "Info.plist"
    with path.open("rb") as stream:
        info = plistlib.load(stream)
    info.update({
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleName": "InkReference",
        "CFBundleDisplayName": "InkReference",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": False,
        "NSHumanReadableCopyright": "InkReference contributors. See bundled LICENSE and THIRD_PARTY_NOTICES.md.",
        # Only the embedded loopback web UI needs HTTP. External model requests
        # use Python's verified TLS transport, independently of WebKit ATS.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    })
    with path.open("wb") as stream:
        plistlib.dump(info, stream, sort_keys=False)


def resolve_inno() -> str:
    found = shutil.which("ISCC.exe")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if not base:
            continue
        for version in ("7", "6"):
            candidate = Path(base) / ("Inno Setup " + version) / "ISCC.exe"
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError("Inno Setup 6.3+ was not found. Install it to build Setup, or omit --installer for ZIP only.")


def package_windows(root: Path, output: Path, version: str, arch: str, installer: bool) -> list[Path]:
    app = output / "app" / "InkReference"
    if not (app / "InkReference.exe").is_file():
        raise RuntimeError("PyInstaller did not produce InkReference.exe.")
    if not (app / "_internal" / "frontend" / "dist" / "index.html").is_file():
        raise RuntimeError("The bundled frontend is missing.")
    copy_notices(root, app)
    archive = output / f"InkReference-windows-{arch}.zip"
    with progress("Creating the portable ZIP..."):
        shutil.make_archive(str(archive.with_suffix("")), "zip", app.parent, app.name)
    artifacts = [archive]
    if installer:
        installer_dir = output / "installer"
        installer_dir.mkdir(parents=True, exist_ok=True)
        run([resolve_inno(), "/Qp", f"/DAppSource={app}", f"/DAppVersion={version}", f"/O{installer_dir}", str(root / "packaging" / "InkReference.iss")], "Compiling the Windows Setup installer...")
        setup = installer_dir / f"InkReference-Setup-{version}.exe"
        if not setup.is_file():
            raise RuntimeError("Inno Setup did not produce the expected installer.")
        artifacts.append(setup)
    return artifacts


def package_macos(root: Path, output: Path, version: str, arch: str, dmg: bool) -> list[Path]:
    bundle = output / "app" / "InkReference.app"
    if not (bundle / "Contents" / "MacOS" / "InkReference").is_file():
        raise RuntimeError("PyInstaller did not produce InkReference.app.")
    resources = bundle / "Contents" / "Resources"
    if not (resources / "frontend" / "dist" / "index.html").is_file():
        raise RuntimeError("The bundled frontend is missing.")
    copy_notices(root, resources)
    update_macos_plist(bundle, version)
    # Changes to Resources/Info.plist happen before the final bundle signature.
    # This is local ad-hoc signing, never Developer ID signing or notarization.
    run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)], "Applying local ad-hoc bundle signature...")
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], "Verifying the local bundle signature...")
    archive = output / f"InkReference-macos-{arch}.zip"
    # ditto preserves the executable mode and framework symlinks used by a
    # PyInstaller .app. Python/PowerShell ZIP helpers do not preserve both.
    run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(archive)], "Creating the macOS application ZIP...")
    artifacts = [archive]
    if dmg:
        stage = root / "build" / f"dmg-{arch}"
        with progress("Preparing the disk image..."):
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(parents=True)
            shutil.copytree(bundle, stage / bundle.name, symlinks=True)
            (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
        disk = output / f"InkReference-macos-{arch}.dmg"
        run(["/usr/bin/hdiutil", "create", "-volname", "InkReference", "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(disk)], "Creating the macOS disk image...")
        artifacts.append(disk)
    print("[package] macOS bundle is ad-hoc signed for local testing; it is not Developer ID signed or notarized.", flush=True)
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build InkReference on Windows or macOS")
    parser.add_argument("--output-directory", type=Path, default=ROOT / "release")
    parser.add_argument("--installer", action="store_true", help="Build Windows Setup with installed Inno Setup")
    parser.add_argument("--dmg", action="store_true", help="Build an additional macOS disk image")
    parser.add_argument("--skip-install", action="store_true", help="Use dependencies/frontend already prepared in this Python environment")
    parser.add_argument("--skip-prepare", action="store_true", help="Application/frontend are prepared; install only desktop build dependencies")
    args = parser.parse_args(argv)
    if sys.platform not in {"win32", "darwin"}:
        parser.error("Build Windows packages on Windows and macOS packages on macOS.")
    if args.installer and sys.platform != "win32":
        parser.error("--installer requires Windows; use --dmg on macOS.")
    if args.dmg and sys.platform != "darwin":
        parser.error("--dmg requires macOS.")
    arch = architecture(sys.platform, platform.machine())
    output = args.output_directory.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        if not args.skip_install:
            if not args.skip_prepare:
                run([sys.executable, str(ROOT / "scripts" / "manage.py"), "install", "--current-environment"], "Preparing the application and frontend in the selected Python environment...")
            run([sys.executable, "-m", "pip", "install", "-c", str(ROOT / "requirements-lock.txt"), "-e", ".[desktop,build]"], "Installing desktop build dependencies...")
        if not (ROOT / "frontend" / "dist" / "index.html").is_file():
            raise RuntimeError("Frontend build is missing. Run the install script before --skip-install.")
        version = importlib.metadata.version("inkreference")
        environment = os.environ.copy()
        if sys.platform == "darwin":
            environment.setdefault("MACOSX_DEPLOYMENT_TARGET", "13.0")
        run(pyinstaller_command(ROOT, output, sys.platform, arch), "Collecting the application, frontend and native desktop runtime...", environment=environment)
        artifacts = package_windows(ROOT, output, version, arch, args.installer) if sys.platform == "win32" else package_macos(ROOT, output, version, arch, args.dmg)
        manifest = {"version": version, "platform": sys.platform, "architecture": arch, "signed": "ad-hoc" if sys.platform == "darwin" else False, "notarized": False, "artifacts": [path.name for path in artifacts]}
        (output / "package-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for artifact in artifacts:
            print(f"[package] {artifact}", flush=True)
        print("[package] Packaging completed. No release was published.", flush=True)
        return 0
    except (subprocess.CalledProcessError, RuntimeError, ValueError, OSError) as exc:
        print(f"[package] ERROR: {exc}", file=sys.stderr, flush=True)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
