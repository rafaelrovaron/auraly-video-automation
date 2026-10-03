from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from auraly_pipeline.cli import app


def test_api_serve_defaults_and_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("uvicorn.run", lambda application, **kwargs: calls.append({"app": application, **kwargs}))
    monkeypatch.setenv("AURALY_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("AURALY_DATABASE_PATH", str(tmp_path / "private.db"))
    result = CliRunner().invoke(app, ["api", "serve"])
    assert result.exit_code == 0, result.output
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8000
    assert calls[0]["workers"] == 1
    assert calls[0]["access_log"] is False
    calls.clear()
    result = CliRunner().invoke(app, ["api", "serve", "--port", "9000", "--project-root", str(tmp_path),
                                    "--work-root", str(tmp_path / "work"), "--database", str(tmp_path / "other.db")])
    assert result.exit_code == 0, result.output
    assert calls[0]["port"] == 9000
    help_result = CliRunner().invoke(app, ["--help"])
    assert all(command in help_result.output for command in ["campaign", "voice", "image", "heygen", "editing", "api"])


@pytest.mark.parametrize("options", [["--port", "0"], ["--port", "65536"], ["--host", "0.0.0.0"],
                                     ["--reload"], ["--workers", "2"]])
def test_api_serve_rejects_invalid_port_and_host_override(options: list[str]) -> None:
    result = CliRunner().invoke(app, ["api", "serve", *options])
    assert result.exit_code != 0


def test_api_serve_startup_error_has_no_private_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(str(tmp_path / "private.db"))

    monkeypatch.setattr("uvicorn.run", fail)
    result = CliRunner().invoke(app, ["api", "serve", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Local API could not start" in result.output
    assert str(tmp_path) not in result.output


def test_api_serve_disables_access_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("uvicorn.run", lambda application, **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(app, ["api", "serve", "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls[0]["access_log"] is False
