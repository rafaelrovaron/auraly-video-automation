from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class VerificationStep:
    name: str
    argv: tuple[str, ...]
    extra_env: Mapping[str, str] = field(default_factory=dict)
    generated_files: tuple[Path, ...] = ()


def build_fast_steps(pytest_targets: Sequence[str]) -> tuple[VerificationStep, ...]:
    steps = [
        VerificationStep(
            name="Ruff source, tests, and harness",
            argv=("uv", "run", "ruff", "check", "src", "tests", "scripts"),
        ),
        VerificationStep(
            name="mypy source",
            argv=("uv", "run", "python", "-m", "mypy", "src"),
        ),
    ]
    if pytest_targets:
        steps.append(
            VerificationStep(
                name="focused pytest",
                argv=("uv", "run", "pytest", *pytest_targets),
            )
        )
    return tuple(steps)


RunCommand = Callable[..., subprocess.CompletedProcess[Any]]
Output = Callable[[str], None]


def run_steps(
    steps: Sequence[VerificationStep],
    *,
    repository_root: Path = REPOSITORY_ROOT,
    run_command: RunCommand = subprocess.run,
    output: Output = print,
) -> int:
    total = len(steps)
    for index, step in enumerate(steps, start=1):
        output(f"[{index}/{total}] {step.name}")
        output(f"$ {shlex.join(step.argv)}")
        environment = os.environ.copy()
        environment.update(step.extra_env)
        result = run_command(
            list(step.argv),
            cwd=repository_root,
            env=environment,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            output(f"[FAIL] {step.name} exited with status {result.returncode}")
            output(f"[SUMMARY] stopped after {index}/{total} steps")
            return result.returncode
        output(f"[PASS] {step.name}")

    output(f"[SUMMARY] {total}/{total} steps passed")
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic Auraly verification checks.")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    fast_parser = subparsers.add_parser("fast", help="Run low-cost task-level checks.")
    fast_parser.add_argument(
        "--pytest",
        dest="pytest_targets",
        nargs="+",
        default=(),
        metavar="TARGET",
        help="Run exactly these focused pytest targets after Ruff and mypy.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    return run_steps(build_fast_steps(arguments.pytest_targets))


if __name__ == "__main__":
    raise SystemExit(main())
