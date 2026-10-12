from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from jsonschema import Draft202012Validator

from auraly_pipeline.cli import app
from auraly_pipeline.editing.render_artifacts import render_work_root
from tests.review_helpers import review_case as review_case
from tests.test_delivery_service import delivery_case


def test_cli_delivery_get_replay_and_root_override(review_case: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    service, request = delivery_case(review_case)
    project = service.reviews.project_root
    root = project / "approved-exports"
    monkeypatch.setenv("AURALY_DELIVERY_ROOT", str(root))
    path = project / "delivery-request.json"
    path.write_text(request.model_dump_json(by_alias=True), encoding="utf-8")
    options = ["--request", str(path), "--project-root", str(project), "--work-root", str(service.work_root)]
    runner = CliRunner()
    absent = runner.invoke(app, ["edit", "delivery-get", *options])
    assert absent.exit_code == 0 and json.loads(absent.stdout)["record"] is None
    first = runner.invoke(app, ["edit", "deliver", *options])
    assert first.exit_code == 0, first.output
    record = json.loads(first.stdout)
    assert (render_work_root(root) / record["path"]).exists()
    assert runner.invoke(app, ["edit", "deliver", *options]).stdout == first.stdout
    found = runner.invoke(app, ["edit", "delivery-get", *options])
    assert found.exit_code == 0 and json.loads(found.stdout)["record"] == record
    other_root = project / "other-exports"
    explicit = runner.invoke(app, ["edit", "deliver", *options, "--delivery-root", str(other_root)])
    assert explicit.exit_code == 0 and (render_work_root(other_root) / record["path"]).exists()
    for name, payload in (("request", request.model_dump(mode="json", by_alias=True)), ("record", record),
                          ("state", json.loads(found.stdout))):
        schema = json.loads(Path(f"schemas/delivery-{name}.schema.json").read_bytes())
        Draft202012Validator(schema).validate(payload)


def test_cli_delivery_invalid_request_is_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text('{"campaignId":"private-marker","destination":"private-path"}', encoding="utf-8")
    response = CliRunner().invoke(app, ["edit", "deliver", "--request", str(path),
        "--project-root", str(tmp_path), "--work-root", str(tmp_path / "work")])
    assert response.exit_code == 1 and response.stdout == ""
    assert "private-" not in response.output and str(tmp_path) not in response.output
