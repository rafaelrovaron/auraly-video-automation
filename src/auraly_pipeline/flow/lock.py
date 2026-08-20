"""Native exclusive lock for the single Google Flow browser runtime."""

from __future__ import annotations

import errno
import importlib
import os
from pathlib import Path
from typing import Any, BinaryIO, Self

from .domain import FlowRuntimeBusyError


class BrowserRuntimeLock:
    """Own a non-blocking, process-exclusive lock without deleting its file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        """Acquire one byte non-blockingly or raise FlowRuntimeBusyError."""
        handle = self._path.open("a+b")
        try:
            _ensure_lock_byte(handle)
        except BaseException:
            handle.close()
            raise
        try:
            _acquire_handle_lock(handle)
        except OSError as error:
            handle.close()
            if _is_lock_contention(error):
                raise FlowRuntimeBusyError() from None
            raise
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        """Unlock and close the owned handle without deleting the lock file."""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _release_handle_lock(handle)
        finally:
            handle.close()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()


def _acquire_handle_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt: Any = importlib.import_module("msvcrt")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl: Any = importlib.import_module("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_handle_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt: Any = importlib.import_module("msvcrt")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl: Any = importlib.import_module("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_lock_contention(error: OSError) -> bool:
    if error.errno in {errno.EACCES, errno.EAGAIN}:
        return True
    return os.name == "nt" and getattr(error, "winerror", None) in {32, 33}
