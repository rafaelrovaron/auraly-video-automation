from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "verify.py"


def load_verify_module() -> ModuleType:
    assert VERIFY_SCRIPT.is_file(), "scripts/verify.py must provide the verification harness"
    spec = importlib.util.spec_from_file_location("auraly_verify", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_verification_step_uses_explicit_argv_and_repository_root() -> None:
    verify = load_verify_module()

    step = verify.VerificationStep(name="example", argv=("uv", "run", "pytest"))

    assert step.argv == ("uv", "run", "pytest")
    assert verify.REPOSITORY_ROOT == REPOSITORY_ROOT


def test_runner_uses_list_argv_repository_root_and_no_shell(tmp_path: Path) -> None:
    verify = load_verify_module()
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    result = verify.run_steps(
        (verify.VerificationStep("example", ("uv", "run", "pytest")),),
        repository_root=tmp_path,
        run_command=fake_run,
        output=lambda _: None,
    )

    assert result == 0
    assert calls[0][0] == ["uv", "run", "pytest"]
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["check"] is False
    assert calls[0][1]["shell"] is False


def test_runner_returns_first_failure_and_stops_subsequent_steps(tmp_path: Path) -> None:
    verify = load_verify_module()
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 7)

    result = verify.run_steps(
        (
            verify.VerificationStep("first", ("uv", "run", "pytest")),
            verify.VerificationStep("second", ("git", "diff", "--check")),
        ),
        repository_root=tmp_path,
        run_command=fake_run,
        output=lambda _: None,
    )

    assert result == 7
    assert calls == [["uv", "run", "pytest"]]


def test_fast_without_pytest_selects_only_low_cost_checks() -> None:
    verify = load_verify_module()

    steps = verify.build_fast_steps(())

    assert [step.argv for step in steps] == [
        ("uv", "run", "ruff", "check", "src", "tests", "scripts"),
        ("uv", "run", "python", "-m", "mypy", "src"),
    ]


def test_fast_forwards_focused_pytest_targets_exactly() -> None:
    verify = load_verify_module()
    targets = (
        "tests/test_job_service.py::test_job_submission_is_idempotent",
        "tests/test_voice_service.py",
    )

    steps = verify.build_fast_steps(targets)

    assert steps[-1].argv == ("uv", "run", "pytest", *targets)
    assert sum("pytest" in step.argv for step in steps) == 1


def test_fast_cli_selects_requested_focused_target() -> None:
    verify = load_verify_module()
    selected: list[Any] = []

    def fake_run_steps(steps: Any) -> int:
        selected.extend(steps)
        return 0

    setattr(verify, "run_steps", fake_run_steps)

    result = verify.main(["fast", "--pytest", "tests/test_verify_harness.py"])

    assert result == 0
    assert selected[-1].argv == (
        "uv",
        "run",
        "pytest",
        "tests/test_verify_harness.py",
    )
