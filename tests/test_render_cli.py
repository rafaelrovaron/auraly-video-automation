from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.render_service import RenderService
from tests.editing_helpers import file_sha
from tests.render_helpers import make_render_plan, publish_test_plan, refresh_plan
from tests.test_verify_harness import load_verify_workflow, workflow_commands


def args(plan: EditBatchPlan, root: Path) -> list[str]:
    return ["edit", "render", "--campaign-id", plan.campaign_id, "--video-id", plan.video_id,
            "--plan-hash", plan.plan_hash, "--project-root", str(root), "--work-root", str(root / "work")]


def test_cli_render_returns_json_and_exit_code(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    publish_test_plan(plan, tmp_path / "work")
    runner = CliRunner()
    first = runner.invoke(app, args(plan, tmp_path))
    assert first.exit_code == 0, first.output
    assert [o["status"] for o in json.loads(first.stdout)["outputs"]] == ["rendered"] * 3
    second = runner.invoke(app, args(plan, tmp_path))
    assert second.exit_code == 0
    assert [o["status"] for o in json.loads(second.stdout)["outputs"]] == ["reused"] * 3
    plan.outputs[0].manifest.captions.enabled = True
    plan.outputs[0].manifest.captions.font = plan.outputs[0].manifest.headline.font
    refresh_plan(plan)
    publish_test_plan(plan, tmp_path / "work")
    failed = runner.invoke(app, args(plan, tmp_path))
    assert failed.exit_code == 1
    assert [o["status"] for o in json.loads(failed.stdout)["outputs"]] == ["failed", "reused", "reused"]


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    service = RenderService(project_root=tmp_path, work_root=work)
    before = {p.relative_to(service.work_root): file_sha(p) for p in service.work_root.rglob("*") if p.is_file()}
    result = CliRunner().invoke(app, [*args(plan, tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    outputs = json.loads(result.stdout)["outputs"]
    assert all(o["status"] == "planned" and o["fitMeasured"] is False for o in outputs)
    after = {p.relative_to(service.work_root): file_sha(p) for p in service.work_root.rglob("*") if p.is_file()}
    assert before == after
    assert not list(service.work_root.glob(".render-*"))
    assert not (work / "campaigns" / plan.campaign_id / "editing" / "renders").exists()


def test_cli_global_failure_is_sanitized(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    publish_test_plan(plan, tmp_path / "work")
    (tmp_path / "source.mp4").write_bytes(b"private token-secret")
    result = CliRunner().invoke(app, args(plan, tmp_path))
    assert result.exit_code == 1 and result.stdout == ""
    assert "source" in result.output
    assert str(tmp_path) not in result.output and "token-secret" not in result.output
    assert "Traceback" not in result.output


def test_windows_ci_includes_render_tests() -> None:
    commands = workflow_commands(load_verify_workflow()["jobs"]["windows-focused"])
    focused = next(command for command in commands if "fast --pytest" in command).split()
    assert {f"tests/test_render_{name}.py" for name in ("domain", "text", "media", "service", "cli")} <= set(focused)


def test_render_help_describes_local_capability() -> None:
    result = CliRunner().invoke(app, ["edit", "render", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output
