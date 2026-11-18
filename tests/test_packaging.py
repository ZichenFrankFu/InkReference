"""Packaging contracts and resource integrity without a native renderer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import plistlib
import shutil
import zipfile

import pytest


@pytest.fixture
def package():
    path = Path(__file__).resolve().parents[1] / "scripts" / "package.py"
    spec = importlib.util.spec_from_file_location("inkreference_package", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_root(path: Path) -> Path:
    path.mkdir(parents=True)
    for name in ("README.md", "THIRD_PARTY_NOTICES.md", "LICENSE"):
        (path / name).write_text(name, encoding="utf-8")
    (path / "third_party").mkdir()
    (path / "third_party" / "frontend-LICENSES.txt").write_text("Synthetic dependency notice", encoding="utf-8")
    shutil.copytree(Path(__file__).resolve().parents[1] / "backend/inkreference/assets",
                    path / "backend/inkreference/assets")
    return path


def test_native_architecture_contract_and_cross_build_rejection(package):
    assert package.architecture("win32", "AMD64") == "x64"
    assert package.architecture("darwin", "arm64") == "arm64"
    assert package.architecture("darwin", "x86_64") == "x86_64"
    with pytest.raises(ValueError, match="Unsupported build architecture"):
        package.architecture("win32", "arm64")


def test_platform_collects_native_vault_and_frontend_without_mineru(package, tmp_path):
    windows = package.pyinstaller_command(tmp_path, tmp_path / "release", "win32", "x64")
    mac = package.pyinstaller_command(tmp_path, tmp_path / "release", "darwin", "arm64")
    assert "keyring.backends.Windows" in windows and "pythonnet" in windows
    assert "keyring.backends.macOS" in mac and "webview.platforms.cocoa" in mac
    assert "Security" in mac and "WebKit" in mac and "objc" in mac
    assert "pythonnet" not in mac and "keyring.backends.Windows" not in mac
    assert mac[mac.index("--target-architecture") + 1] == "arm64"
    assert "--argv-emulation" not in mac
    assert "--onedir" in mac and "--windowed" in mac
    assert "--add-data" in mac and ":frontend/dist" in mac[mac.index("--add-data") + 1]
    assert Path(windows[windows.index("--icon") + 1]).name == "icon.ico"
    assert Path(mac[mac.index("--icon") + 1]).name == "icon.icns"
    assert any(value.endswith(":inkreference/assets") for value in windows)
    assert mac[mac.index("mineru") - 1] == "--exclude-module"


def test_macos_resources_plist_signature_and_symlink_safe_archive(package, tmp_path, monkeypatch):
    root = fixture_root(tmp_path / "source")
    output = tmp_path / "release"
    bundle = output / "app" / "InkReference.app"
    executable = bundle / "Contents" / "MacOS" / "InkReference"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic executable")
    resources = bundle / "Contents" / "Resources"
    frontend = resources / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("Synthetic UI", encoding="utf-8")
    shutil.copytree(root / "backend/inkreference/assets", resources / "inkreference/assets")
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "InkReference"}))
    calls = []
    monkeypatch.setattr(package, "run", lambda command, description: calls.append(command))
    artifacts = package.package_macos(root, output, "0.2.0", "arm64", False)
    assert artifacts == [output / "InkReference-macos-arm64.zip"]
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "InkReference"
    assert info["CFBundleIdentifier"] == "com.inkreference.desktop"
    assert info["CFBundleIconFile"] == "icon.icns"
    assert (resources / info["CFBundleIconFile"]).read_bytes() == (root / "backend/inkreference/assets/icon.icns").read_bytes()
    assert info["CFBundleShortVersionString"] == "0.2.0"
    assert info["LSMinimumSystemVersion"] == "13.0"
    assert info["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}
    assert (resources / "third_party" / "frontend-LICENSES.txt").is_file()
    assert calls[0][:5] == ["/usr/bin/codesign", "--force", "--deep", "--sign", "-"]
    assert calls[1][:4] == ["/usr/bin/codesign", "--verify", "--deep", "--strict"]
    assert calls[2][:6] == ["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle)]


def test_windows_portable_zip_includes_runtime_and_licenses(package, tmp_path):
    root = fixture_root(tmp_path / "source")
    output = tmp_path / "release"
    app = output / "app" / "InkReference"
    frontend = app / "_internal" / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (app / "InkReference.exe").write_bytes(b"synthetic executable")
    (frontend / "index.html").write_text("Synthetic UI", encoding="utf-8")
    shutil.copytree(root / "backend/inkreference/assets", app / "_internal/inkreference/assets")
    [archive] = package.package_windows(root, output, "0.2.0", "x64", False)
    with zipfile.ZipFile(archive) as stream:
        assert "InkReference/InkReference.exe" in stream.namelist()
        assert "InkReference/_internal/frontend/dist/index.html" in stream.namelist()
        assert "InkReference/_internal/inkreference/assets/icon.ico" in stream.namelist()
        assert "InkReference/third_party/frontend-LICENSES.txt" in stream.namelist()


def test_packaging_does_not_claim_cross_os_builds(package, monkeypatch):
    monkeypatch.setattr(package.sys, "platform", "linux")
    with pytest.raises(SystemExit) as caught:
        package.main(["--skip-install"])
    assert caught.value.code == 2


@pytest.mark.parametrize("system", ["win32", "darwin"])
def test_missing_native_icon_stops_packaging_before_archive(package, tmp_path, system):
    root = fixture_root(tmp_path / "source")
    output = tmp_path / "release"
    if system == "win32":
        app = output / "app/InkReference"
        frontend = app / "_internal/frontend/dist"
        frontend.mkdir(parents=True)
        (app / "InkReference.exe").write_bytes(b"synthetic executable")
        (frontend / "index.html").write_text("Synthetic UI", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Windows window icon is missing"):
            package.package_windows(root, output, "0.2.0", "x64", False)
    else:
        app = output / "app/InkReference.app/Contents"
        (app / "MacOS").mkdir(parents=True)
        (app / "MacOS/InkReference").write_bytes(b"synthetic executable")
        frontend = app / "Resources/frontend/dist"
        frontend.mkdir(parents=True)
        (frontend / "index.html").write_text("Synthetic UI", encoding="utf-8")
        with pytest.raises(RuntimeError, match="macOS application icon is missing"):
            package.package_macos(root, output, "0.2.0", "arm64", False)
    assert not list(output.glob("*.zip"))
