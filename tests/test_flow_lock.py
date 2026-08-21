from __future__ import annotations

import errno
from pathlib import Path
import subprocess
import sys
import time
from typing import BinaryIO, cast

import pytest

from auraly_pipeline.flow import BrowserRuntimeLock, FlowRuntimeBusyError
from auraly_pipeline.flow import lock as lock_module


LOCK_HOLDER = Path(__file__).with_name("flow_lock_holder.py")


def start_lock_holder(lock_path: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [sys.executable, str(LOCK_HOLDER), str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "locked"
    return holder


def test_second_process_fails_immediately_while_first_owns_lock(tmp_path: Path) -> None:
    holder = start_lock_holder(tmp_path / "flow.lock")
    try:
        with pytest.raises(FlowRuntimeBusyError):
            BrowserRuntimeLock(tmp_path / "flow.lock").acquire()
        assert holder.stdin is not None
        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.wait(timeout=5) == 0
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)


def test_process_exit_releases_kernel_lock_even_when_file_remains(tmp_path: Path) -> None:
    lock_path = tmp_path / "flow.lock"
    holder = start_lock_holder(lock_path)
    holder.kill()
    assert holder.wait(timeout=5) != 0

    deadline = time.monotonic() + 5
    while True:
        try:
            with BrowserRuntimeLock(lock_path):
                assert lock_path.is_file()
                return
        except FlowRuntimeBusyError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def test_normal_release_allows_reacquisition_and_keeps_lock_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "flow.lock"
    first = BrowserRuntimeLock(lock_path)
    first.acquire()
    first.release()
    assert lock_path.is_file()

    with BrowserRuntimeLock(lock_path):
        assert lock_path.is_file()


def test_release_is_idempotent_after_finally_style_cleanup(tmp_path: Path) -> None:
    lock = BrowserRuntimeLock(tmp_path / "flow.lock")
    lock.acquire()
    lock.release()
    lock.release()


def test_retained_lock_releases_after_with_body_error_and_can_be_reacquired(tmp_path: Path) -> None:
    lock = BrowserRuntimeLock(tmp_path / "flow.lock")

    with pytest.raises(ValueError, match="body failed"):
        with lock:
            raise ValueError("body failed")

    with lock:
        assert (tmp_path / "flow.lock").is_file()


def test_failed_native_acquire_leaves_no_owned_handle_before_reacquisition(tmp_path: Path) -> None:
    lock_path = tmp_path / "flow.lock"
    holder = start_lock_holder(lock_path)
    attempted = BrowserRuntimeLock(lock_path)
    try:
        with pytest.raises(FlowRuntimeBusyError):
            attempted.acquire()
        assert attempted._handle is None

        assert holder.stdin is not None
        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.wait(timeout=5) == 0

        with attempted:
            assert attempted._handle is not None
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)


def test_windows_native_lock_calls_resolve_module_before_seeking_lock_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeHandle:
        def seek(self, _: int) -> None:
            events.append("seek")

        def fileno(self) -> int:
            return 1

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, _: int, mode: int, __: int) -> None:
            events.append(f"locking:{mode}")

    def import_msvcrt(name: str, package: str | None = None) -> FakeMsvcrt:
        assert name == "msvcrt"
        assert package is None
        events.append("import")
        return FakeMsvcrt()

    monkeypatch.setattr(lock_module.os, "name", "nt")
    monkeypatch.setattr(lock_module.importlib, "import_module", import_msvcrt)
    handle = cast(BinaryIO, FakeHandle())

    lock_module._acquire_handle_lock(handle)
    lock_module._release_handle_lock(handle)

    assert events == ["import", "seek", "locking:1", "import", "seek", "locking:2"]


def test_windows_lock_contention_tolerates_oserror_without_winerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OSErrorWithoutWinerror(OSError):
        def __getattribute__(self, name: str) -> object:
            if name == "winerror":
                raise AttributeError(name)
            return super().__getattribute__(name)

    monkeypatch.setattr(lock_module.os, "name", "nt")

    assert lock_module._is_lock_contention(OSErrorWithoutWinerror(errno.EIO, "unexpected")) is False


def test_unexpected_lock_setup_error_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_lock_setup(_: object) -> None:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(lock_module, "_ensure_lock_byte", deny_lock_setup)

    with pytest.raises(PermissionError):
        BrowserRuntimeLock(tmp_path / "flow.lock").acquire()
