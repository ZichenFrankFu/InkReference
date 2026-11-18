"""Resource and lifecycle contracts. These are not native desktop rendering tests."""

from __future__ import annotations

import base64
import ctypes
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import pytest

from inkreference import app_icon, desktop


ROOT = Path(__file__).resolve().parents[1]


def png_size(content: bytes) -> tuple[int, int]:
    assert content[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    dimensions = None
    while position < len(content):
        length = struct.unpack_from(">I", content, position)[0]
        kind = content[position + 4:position + 8]
        data = content[position + 8:position + 8 + length]
        crc = struct.unpack_from(">I", content, position + 8 + length)[0]
        assert zlib.crc32(kind + data) == crc
        if kind == b"IHDR":
            dimensions = struct.unpack_from(">II", data)
            assert data[8:10] == b"\x08\x06"  # RGBA, 8-bit channels.
        position += length + 12
        if kind == b"IEND":
            assert position == len(content)
            break
    assert dimensions is not None
    return dimensions


def test_windows_icon_contains_valid_small_and_high_dpi_images():
    content = app_icon.native_icon("win32").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", content)
    assert (reserved, kind) == (0, 1)
    dimensions = []
    previous_end = 6 + 16 * count
    for index in range(count):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", content, 6 + 16 * index)
        assert planes == 1 and bits == 32
        assert offset == previous_end
        actual = png_size(content[offset:offset + length])
        assert actual == (width or 256, height or 256)
        dimensions.append(actual[0])
        previous_end = offset + length
    assert previous_end == len(content)
    assert dimensions == [16, 20, 24, 32, 40, 48, 64, 128, 256]


def test_macos_icon_has_valid_retina_and_standard_representations():
    content = (app_icon.ASSETS / "icon.icns").read_bytes()
    assert content[:4] == b"icns"
    assert struct.unpack_from(">I", content, 4)[0] == len(content)
    expected = {b"icp4": 16, b"icp5": 32, b"icp6": 64, b"ic07": 128,
                b"ic08": 256, b"ic09": 512, b"ic10": 1024}
    position = 8
    found = {}
    while position < len(content):
        kind, length = struct.unpack_from(">4sI", content, position)
        found[kind] = png_size(content[position + 8:position + length])[0]
        position += length
    assert position == len(content)
    assert found == expected


def test_browser_sidebar_and_splash_share_the_font_independent_svg():
    svg = (ROOT / "packaging/icon.svg").read_bytes()
    assert (ROOT / "frontend/public/app-icon.svg").read_bytes() == svg
    assert (app_icon.ASSETS / "icon.svg").read_bytes() == svg
    assert b"<path" in svg and b"<text" not in svg
    assert app_icon.icon_data_url() in desktop.LOADING_HTML
    assert base64.b64decode(app_icon.icon_data_url().split(",", 1)[1]) == svg
    assert 'src="/app-icon.svg"' in (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert 'href="/app-icon.svg"' in (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert png_size((ROOT / "frontend/public/app-icon.png").read_bytes()) == (192, 192)
    assert png_size((ROOT / "frontend/public/apple-touch-icon.png").read_bytes()) == (180, 180)
    assert png_size(app_icon.native_icon("darwin").read_bytes()) == (512, 512)


def test_windows_grouping_uses_pointer_safe_prototype_and_matches_shortcut(monkeypatch):
    calls = []

    class Function:
        def __call__(self, value):
            calls.append(value)
            return 0

    function = Function()
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs: SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=function), raising=False)
    app_icon.set_windows_app_id()
    assert calls == ["com.inkreference.desktop"]
    assert function.restype is ctypes.c_long
    assert len(function.argtypes) == 1
    setup = (ROOT / "packaging/InkReference.iss").read_text(encoding="utf-8")
    assert 'AppUserModelID: "' + calls[0] + '"' in setup
    assert "SetupIconFile=" in setup


def test_windows_grouping_failed_hresult_is_reported(monkeypatch):
    class Function:
        def __call__(self, value):
            return -2147467259

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=Function()), raising=False)
    with pytest.raises(OSError, match="HRESULT 0x80004005"):
        app_icon.set_windows_app_id()
