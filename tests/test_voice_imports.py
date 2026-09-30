import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("force_color", [None, "1"])
def test_heygen_cli_help_does_not_require_whisper(force_color: str | None) -> None:
    env = {key: value for key, value in os.environ.items() if key != "FORCE_COLOR"}
    env["TERM"] = "dumb" if force_color is None else "xterm-256color"
    if force_color is not None:
        env["FORCE_COLOR"] = force_color
    program = """
import builtins
original_import = builtins.__import__
def blocked_import(name, *args, **kwargs):
    if name == 'faster_whisper' or name.startswith('faster_whisper.'):
        raise ImportError('Whisper native runtime is unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked_import
from click import unstyle
from typer.testing import CliRunner
from auraly_pipeline.cli import app
result = CliRunner().invoke(app, ['heygen', 'plan-videos', '--help'])
assert result.exit_code == 0, result.output
assert '--max-paid-renders' in unstyle(result.output), result.output
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
