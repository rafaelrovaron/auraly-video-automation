from __future__ import annotations

import errno
from pathlib import Path
import subprocess
import sys

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

    with BrowserRuntimeLock(lock_path):
        assert lock_path.is_file()


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


def test_unexpected_lock_setup_error_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_lock_setup(_: object) -> None:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(lock_module, "_ensure_lock_byte", deny_lock_setup)

    with pytest.raises(PermissionError):
        BrowserRuntimeLock(tmp_path / "flow.lock").acquire()
