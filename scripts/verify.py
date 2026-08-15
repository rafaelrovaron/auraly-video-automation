from __future__ import annotations

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
