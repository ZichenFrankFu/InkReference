"""PyInstaller entry: desktop by default, --no-open for headless checks."""

from __future__ import annotations

import os
import sys


def _ensure_streams(argv: list[str]) -> None:
    # A windowed executable has no stdout/stderr. Give Uvicorn's formatters
    # real streams and preserve startup errors without displaying a console.
    if sys.stdout is not None and sys.stderr is not None:
        return
    configured = os.environ.get("INKREFERENCE_DATA_DIR")
    for index, argument in enumerate(argv):
        if argument.startswith("--data-dir="):
            configured = argument.split("=", 1)[1]
        elif argument == "--data-dir" and index + 1 < len(argv):
            configured = argv[index + 1]
    from inkreference.desktop import default_log_dir
    from inkreference.bootstrap_logging import BootstrapLogStream
    log_dir = default_log_dir(configured)
    stream = BootstrapLogStream(log_dir)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _ensure_streams(args)
    if "--desktop" not in args and "--no-open" not in args:
        args.append("--desktop")
    from inkreference.__main__ import main as run
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
