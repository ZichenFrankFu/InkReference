"""Small bounded stderr/stdout replacement for a windowed executable."""
from __future__ import annotations

import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import threading
import time

from .diagnostics import redact


class _LocalFileHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


class BootstrapLogStream(io.TextIOBase):
    def __init__(self, directory: str | Path, *, max_bytes: int = 500_000, backups: int = 2):
        super().__init__()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / "desktop.log"
        cutoff = time.time() - 14 * 86400
        for path in [target, *(directory / f"desktop.log.{index}" for index in range(1, backups + 1))]:
            if path.exists() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        self.handler = _LocalFileHandler(target, maxBytes=max(4096, max_bytes), backupCount=max(1, backups), encoding="utf-8", delay=True)
        # Logging's default handleError writes to sys.stderr, which can be this
        # same stream in a windowed executable. Never recurse on a full disk.
        self.handler.handleError = lambda _record: None
        formatter = logging.Formatter("%(asctime)sZ %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        self.handler.setFormatter(formatter)
        self.buffered = ""
        self.lock = threading.RLock()

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def isatty(self):
        return False

    def _line(self, value):
        # ANSI sequences are not useful in a text log. Keys/URL queries are removed.
        safe = redact(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value), 2000)
        if safe.strip():
            self.handler.handle(logging.LogRecord("inkreference.bootstrap", logging.INFO, __file__, 0, safe, (), None))

    def write(self, value):
        if self.closed:
            raise ValueError("I/O operation on closed log stream")
        if not isinstance(value, str):
            raise TypeError("log stream accepts text")
        with self.lock:
            self.buffered += value[:8192]
            lines = self.buffered.split("\n")
            self.buffered = lines.pop()
            for line in lines:
                self._line(line)
            if len(self.buffered) >= 4096:
                self._line(self.buffered[:2000] + " [truncated]")
                self.buffered = ""
        return len(value)

    def flush(self):
        if self.closed:
            return
        with self.lock:
            if self.buffered:
                self._line(self.buffered)
                self.buffered = ""
            self.handler.flush()

    def close(self):
        if not self.closed:
            self.flush()
            self.handler.close()
        super().close()
