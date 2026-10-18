"""One live application process per data directory, including different ports.

The OS releases the advisory/byte-range lock when its file handle closes or the
process dies. The file itself remains, contains no owner data or credentials,
and must not be deleted on release (unlinking would create an inode race).
"""

from __future__ import annotations

import contextlib
import errno
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from .database import DataError

LOCK_FILENAME = "workspace.lock"
_retained_handles: list[Any] = []


def _release(handle: Any) -> None:
    try:
        with contextlib.suppress(OSError):
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _release_after_threads(handle: Any, threads: list[threading.Thread]) -> None:
    # This guard is a daemon: a real process exit remains immediate, and the
    # kernel releases its lock. An embedded server retains ownership while its
    # last paid request is still returning and persisting local audit records.
    try:
        for worker in threads:
            worker.join()
    except RuntimeError:
        # During interpreter finalization, joining a daemon may be forbidden.
        # Retain ownership until the OS closes the process's handles.
        _retained_handles.append(handle)
        return
    _release(handle)


@contextlib.contextmanager
def workspace_lock(data_dir: str | Path, *, pending_threads: Callable[[], list[threading.Thread]] | None = None) -> Iterator[None]:
    """Hold a workspace lock through shutdown, including unfinished workers.

    If shutdown returns before an existing model request, a daemon guard keeps
    the same file handle locked until the supplied worker threads exit. The
    caller must stop accepting new work before leaving this context.
    """
    directory = Path(data_dir).expanduser().resolve()
    descriptor = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(directory / LOCK_FILENAME, flags, 0o600)
        os.set_inheritable(descriptor, False)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
    except OSError as exc:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise DataError(503, "无法打开工作区锁文件，请检查数据目录是否存在且允许读写。") from exc

    acquired = False
    try:
        # Windows _locking supports byte ranges beyond EOF. Acquire first,
        # then initialize, so concurrent creators never write a locked byte.
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            busy = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
            if exc.errno in busy:
                raise DataError(409, "此数据目录已在另一个 InkReference 实例中打开。请先关闭该实例，或选择不同的数据目录。") from None
            raise DataError(503, "当前数据目录无法使用进程锁，请选择本机可读写目录。") from exc
        if os.fstat(handle.fileno()).st_size == 0:
            handle.seek(0)
            handle.write(b"1")
        yield
    finally:
        if acquired:
            try:
                pending = [worker for worker in pending_threads() if worker.is_alive()] if pending_threads is not None else []
            except Exception:
                # A broken lifecycle callback must not release ownership of a
                # directory whose worker state cannot be established.
                _retained_handles.append(handle)
                raise
            if pending:
                guard = threading.Thread(target=_release_after_threads, args=(handle, pending), name="inkreference-workspace-release", daemon=True)
                try:
                    guard.start()
                except RuntimeError:
                    _retained_handles.append(handle)
            else:
                _release(handle)
        else:
            handle.close()
