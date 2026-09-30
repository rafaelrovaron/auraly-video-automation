import subprocess
import sys


def test_heygen_cli_help_does_not_require_whisper() -> None:
    program = """
import builtins
original_import = builtins.__import__
def blocked_import(name, *args, **kwargs):
    if name == 'faster_whisper' or name.startswith('faster_whisper.'):
        raise ImportError('Whisper native runtime is unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked_import
from typer.testing import CliRunner
from auraly_pipeline.cli import app
result = CliRunner().invoke(app, ['heygen', 'plan-videos', '--help'])
assert result.exit_code == 0, result.output
assert '--max-paid-renders' in result.output
"""
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
