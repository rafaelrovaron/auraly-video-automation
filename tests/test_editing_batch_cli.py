from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from tests.editing_batch_helpers import batch_data
from tests.test_editing_batch_service import setup_batch

pytest_plugins = ["tests.test_heygen_video_media"]


def test_plan_dry_run_and_get(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    path = tmp_path / "request.json"
    path.write_text(request.model_dump_json(by_alias=True), encoding="utf-8")
    roots = ["--project-root", str(tmp_path), "--work-root", str(work)]
    args = ["edit", "plan", "--request", str(path), "--database", str(db), *roots]
    runner = CliRunner()
    dry = runner.invoke(app, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert not list(work.rglob("plan.json"))
    real = runner.invoke(app, args)
    assert real.exit_code == 0, real.output
    assert real.stdout == dry.stdout
    plan = EditBatchPlan.model_validate_json(real.stdout)
    got = runner.invoke(app, ["edit", "plan-get", "--campaign-id", request.campaign_id,
        "--video-id", request.video_id, "--plan-hash", plan.plan_hash, *roots])
    assert got.exit_code == 0
    assert EditBatchPlan.model_validate_json(got.stdout) == plan


@pytest.mark.parametrize("case", ["limit", "secret", "database"])
def test_plan_errors_are_sanitized(tmp_path: Path, case: str) -> None:
    data = batch_data()
    if case == "limit":
        data["maxOutputs"] = 2
    elif case == "secret":
        data["https://private.example/?token=hidden"] = "private value"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = CliRunner().invoke(app, ["edit", "plan", "--request", str(path),
        "--database", str(tmp_path / "missing.db"), "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "hidden" not in result.output and "private value" not in result.output
    assert str(tmp_path) not in result.output
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.parametrize("scenario", ["default-work", "environment-project"])
def test_batch_cli_roots_reject_junction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    project = tmp_path / "project"
    (project / "pipeline").mkdir(parents=True)
    link = project / "pipeline" / "work" if scenario == "default-work" else tmp_path / "project-link"
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, check=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    roots = ["--project-root", str(project)] if scenario == "default-work" else []
    if scenario == "environment-project":
        monkeypatch.setenv("AURALY_PROJECT_ROOT", str(link))
    result = CliRunner().invoke(app, ["edit", "plan-get", "--campaign-id", "campaign-one",
        "--video-id", "video-one", "--plan-hash", "a" * 64, *roots])
    assert result.exit_code == 1 and "links" in result.output
