"""Lightweight OS workspace paths shared by API, CLI, and desktop bootstrap."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def default_data_dir() -> Path:
    """Return the OS application-data location, without creating it."""
    configured = os.environ.get("INKREFERENCE_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "InkReference"
    if sys.platform == "darwin":
        legacy = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "InkReference"
        try:
            with (legacy / "inkreference.sqlite3").open("rb") as database:
                if database.read(16) == b"SQLite format 3\x00":
                    return legacy
        except OSError:
            pass
        return Path.home() / "Library" / "Application Support" / "InkReference"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "InkReference"
