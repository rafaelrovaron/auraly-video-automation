from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "verify.py"
VERIFY_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "verify.yml"


def load_verify_module() -> ModuleType:
    assert VERIFY_SCRIPT.is_file(), "scripts/verify.py must provide the verification harness"
    spec = importlib.util.spec_from_file_location("auraly_verify", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_verify_workflow() -> dict[Any, Any]:
    assert VERIFY_WORKFLOW.is_file(), ".github/workflows/verify.yml must define CI verification"
    loaded = yaml.safe_load(VERIFY_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


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


def test_full_contains_agents_deterministic_baseline_in_order() -> None:
    verify = load_verify_module()
    required = [
        ("uv", "sync", "--locked", "--all-groups"),
        ("uv", "run", "pytest"),
        ("uv", "run", "ruff", "check", "src", "tests"),
        ("uv", "run", "python", "-m", "mypy", "src"),
        ("uv", "run", "python", "-m", "mypy", "tests"),
        ("uv", "run", "python", "-m", "auraly_pipeline.schema"),
        (
            "uv",
            "run",
            "python",
            "-m",
            "auraly_pipeline.cli",
            "export-image-generation-schema",
            "--output",
            "schemas/image-generation.schema.json",
        ),
        ("uv", "pip", "check"),
        ("npm", "ci"),
        ("npm", "run", "hf:doctor"),
        ("npm", "audit", "--omit=dev", "--audit-level=high"),
        ("git", "diff", "--check"),
    ]

    actual = [step.argv for step in verify.build_full_steps(os_name="posix")]

    assert [argv for argv in actual if argv in required] == required


def test_full_test_mypy_uses_cross_platform_mypy_path_environment() -> None:
    verify = load_verify_module()

    steps = verify.build_full_steps(os_name="posix")
    test_mypy = next(step for step in steps if step.argv[-1] == "tests" and "mypy" in step.argv)

    assert test_mypy.extra_env == {"MYPYPATH": "src"}


def test_full_uses_platform_appropriate_npm_executable() -> None:
    verify = load_verify_module()

    windows = verify.build_full_steps(os_name="nt")
    posix = verify.build_full_steps(os_name="posix")

    assert {step.argv[0] for step in windows if "npm" in step.argv[0]} == {"npm.cmd"}
    assert {step.argv[0] for step in posix if "npm" in step.argv[0]} == {"npm"}


def test_full_cli_selects_the_full_registry() -> None:
    verify = load_verify_module()
    selected: list[Any] = []

    def fake_run_steps(steps: Any) -> int:
        selected.extend(steps)
        return 0

    setattr(verify, "run_steps", fake_run_steps)

    result = verify.main(["full"])

    assert result == 0
    assert selected[0].argv == ("uv", "sync", "--locked", "--all-groups")
    assert selected[-1].argv == ("git", "diff", "--check")


def test_schema_drift_fails_without_reverting_generated_file(tmp_path: Path) -> None:
    verify = load_verify_module()
    schema = tmp_path / "schemas" / "generated.json"
    schema.parent.mkdir()
    schema.write_text("before\n", encoding="utf-8")
    calls: list[list[str]] = []
    output: list[str] = []

    def changing_generator(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        schema.write_text("after\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    result = verify.run_steps(
        (
            verify.VerificationStep(
                "generate schema",
                ("uv", "run", "generator"),
                generated_files=(Path("schemas/generated.json"),),
            ),
            verify.VerificationStep("must not run", ("git", "diff", "--check")),
        ),
        repository_root=tmp_path,
        run_command=changing_generator,
        output=output.append,
    )

    assert result == 1
    assert calls == [["uv", "run", "generator"]]
    assert schema.read_text(encoding="utf-8") == "after\n"
    assert "generated schema drift" in "\n".join(output).lower()


def test_unrelated_dirty_file_does_not_trigger_schema_drift(tmp_path: Path) -> None:
    verify = load_verify_module()
    schema = tmp_path / "schemas" / "generated.json"
    schema.parent.mkdir()
    schema.write_text("stable\n", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("pre-existing work\n", encoding="utf-8")

    def stable_generator(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0)

    result = verify.run_steps(
        (
            verify.VerificationStep(
                "generate schema",
                ("uv", "run", "generator"),
                generated_files=(Path("schemas/generated.json"),),
            ),
        ),
        repository_root=tmp_path,
        run_command=stable_generator,
        output=lambda _: None,
    )

    assert result == 0


def test_full_schema_generators_declare_tracked_outputs() -> None:
    verify = load_verify_module()
    steps = verify.build_full_steps(os_name="posix")
    generated = {
        step.name: step.generated_files for step in steps if step.generated_files
    }

    assert generated == {
        "edit schema": (Path("schemas/edit.schema.json"),),
        "image generation schema": (Path("schemas/image-generation.schema.json"),),
    }


def test_environment_secret_is_never_printed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    verify = load_verify_module()
    secret = "do-not-print-this-api-key"
    monkeypatch.setenv("ELEVENLABS_API_KEY", secret)

    def successful_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0)

    result = verify.run_steps(
        (verify.VerificationStep("safe", ("uv", "run", "pytest")),),
        repository_root=tmp_path,
        run_command=successful_run,
    )

    assert result == 0
    assert secret not in capsys.readouterr().out


def test_runner_applies_extra_environment_without_shell_syntax(tmp_path: Path) -> None:
    verify = load_verify_module()
    received_environment: dict[str, str] = {}

    def capture_environment(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        received_environment.update(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0)

    result = verify.run_steps(
        (
            verify.VerificationStep(
                "mypy tests",
                ("uv", "run", "python", "-m", "mypy", "tests"),
                extra_env={"MYPYPATH": "src"},
            ),
        ),
        repository_root=tmp_path,
        run_command=capture_environment,
        output=lambda _: None,
    )

    assert result == 0
    assert received_environment["MYPYPATH"] == "src"


def test_runner_defaults_to_repository_root_cwd() -> None:
    verify = load_verify_module()
    received_cwd: list[Path] = []

    def capture_cwd(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        received_cwd.append(kwargs["cwd"])
        return subprocess.CompletedProcess(argv, 0)

    result = verify.run_steps(
        (verify.VerificationStep("root", ("git", "diff", "--check")),),
        run_command=capture_cwd,
        output=lambda _: None,
    )

    assert result == 0
    assert received_cwd == [REPOSITORY_ROOT]


@pytest.mark.parametrize("argv", [("invalid",), ("fast", "--pytest")])
def test_invalid_cli_input_fails_clearly(
    argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    verify = load_verify_module()

    with pytest.raises(SystemExit) as error:
        verify.main(argv)

    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_runner_prints_progress_and_final_summary(tmp_path: Path) -> None:
    verify = load_verify_module()
    output: list[str] = []

    def successful_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0)

    result = verify.run_steps(
        (verify.VerificationStep("example", ("uv", "run", "pytest")),),
        repository_root=tmp_path,
        run_command=successful_run,
        output=output.append,
    )

    assert result == 0
    assert output == [
        "[1/1] example",
        "$ uv run pytest",
        "[PASS] example",
        "[SUMMARY] 1/1 steps passed",
    ]


def test_linux_ci_runs_full_harness_with_locked_toolchain() -> None:
    workflow = load_verify_workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"push": {"branches": ["main"]}, "pull_request": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is True

    job = workflow["jobs"]["linux-full"]
    assert job["runs-on"] == "ubuntu-latest"
    uses = {step.get("uses") for step in job["steps"]}
    assert "actions/checkout@v6" in uses
    assert "actions/setup-python@v6" in uses
    assert "actions/setup-node@v6" in uses
    assert any(str(action).startswith("astral-sh/setup-uv@") for action in uses)

    python_step = next(step for step in job["steps"] if step.get("uses") == "actions/setup-python@v6")
    node_step = next(step for step in job["steps"] if step.get("uses") == "actions/setup-node@v6")
    assert python_step["with"]["python-version"] == "3.11"
    assert node_step["with"]["node-version"] == "22"

    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert any("install" in command and "ffmpeg" in command for command in commands)
    assert "uv run python scripts/verify.py full" in commands
    assert "secrets" not in str(workflow).lower()


def test_windows_ci_runs_meaningful_cross_platform_fast_verification() -> None:
    workflow = load_verify_workflow()

    job = workflow["jobs"]["windows-focused"]
    assert job["runs-on"] == "windows-latest"
    uses = {step.get("uses") for step in job["steps"]}
    assert "actions/checkout@v6" in uses
    assert "actions/setup-python@v6" in uses
    assert any(str(action).startswith("astral-sh/setup-uv@") for action in uses)

    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert "uv sync --locked --all-groups" in commands
    focused = next(command for command in commands if "scripts/verify.py fast" in command)
    assert focused.split() == [
        "uv",
        "run",
        "python",
        "scripts/verify.py",
        "fast",
        "--pytest",
        "tests/test_verify_harness.py",
        "tests/test_config_paths.py",
        "tests/test_models.py",
        "tests/test_image_generation.py",
        "tests/test_job_migrations.py",
        "tests/test_migration_lock.py",
        "tests/test_job_concurrency.py",
    ]
    assert "secrets" not in str(job).lower()
