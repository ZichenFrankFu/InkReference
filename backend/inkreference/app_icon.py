"""One packaged icon for native windows, taskbar/Dock and startup HTML."""

from __future__ import annotations

import base64
from pathlib import Path
import sys


WINDOWS_APP_ID = "com.inkreference.desktop"
ASSETS = Path(__file__).resolve().with_name("assets")


def native_icon(platform: str | None = None) -> Path:
    """PyInstaller collects these package resources in its normal package tree."""
    platform = platform or sys.platform
    return ASSETS / ("icon.ico" if platform == "win32" else "icon.png")


def icon_data_url() -> str:
    content = (ASSETS / "icon.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(content).decode("ascii")


def set_windows_app_id() -> None:
    """Set grouping identity before any HWND, including source Python launches.

    The native Form.Icon itself is owned by pywebview's WinForms backend;
    start(icon=...) sets it before Form.Show and releases it with the Form.
    """
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    set_id = shell32.SetCurrentProcessExplicitAppUserModelID
    set_id.argtypes = [wintypes.LPCWSTR]
    set_id.restype = ctypes.c_long
    result = set_id(WINDOWS_APP_ID)
    if result < 0:
        raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed: HRESULT 0x{result & 0xffffffff:08x}")
