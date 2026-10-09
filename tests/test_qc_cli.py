from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.qc_domain import QcRequest
from auraly_pipeline.editing.qc_service import QcService
pytest_plugins = ["tests.test_qc_service"]


def roots(service: QcService) -> list[str]:
    return ["--project-root", str(service.project_root), "--work-root", str(service.work_root)]


@pytest.mark.parametrize("state", ["passed", "blocked", "error"])
def test_qc_emits_complete_batch_and_exits_one_for_blocked_or_error(
        qc_case: tuple[QcService, QcRequest, EditBatchPlan], state: str) -> None:
    service, request, _ = qc_case
    request.outputs = request.outputs[:1]
    if state != "passed":
        root = service.work_root / "campaigns" / request.campaign_id / "editing" / "renders"
        master = next((root / request.outputs[0].output_variant_id).glob("*/*.mp4"))
        master.write_bytes(b"synthetic corrupted master")
        if state == "blocked":
            receipt = master.with_name("render.json")
            data = json.loads(receipt.read_bytes())
            data.update(sha256=hashlib.sha256(master.read_bytes()).hexdigest(), sizeBytes=master.stat().st_size)
            receipt.write_text(json.dumps(data))
    file = service.project_root / "qc-request.json"
    file.write_text(request.model_dump_json(by_alias=True))
    result = CliRunner().invoke(app, ["edit", "qc", "--request", str(file), *roots(service)])
    assert result.exit_code == (0 if state == "passed" else 1), result.output
    payload = json.loads(result.stdout)
    assert [o["status"] for o in payload["outputs"]] == [state]
    assert payload["hasFailures"] is (state != "passed")


def test_qc_get_returns_blocked_report_with_exit_zero(
        qc_case: tuple[QcService, QcRequest, EditBatchPlan], monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, _ = qc_case
    root = service.work_root / "campaigns" / request.campaign_id / "editing" / "renders"
    master = next((root / request.outputs[0].output_variant_id).glob("*/*.mp4"))
    master.write_bytes(b"synthetic corrupted master")
    receipt = master.with_name("render.json")
    data = json.loads(receipt.read_bytes())
    data.update(sha256=hashlib.sha256(master.read_bytes()).hexdigest(), sizeBytes=master.stat().st_size)
    receipt.write_text(json.dumps(data))
    batch = service.run(request)
    assert batch.outputs[0].qc_key is not None
    monkeypatch.setattr(qc_service, "detect_runtime", lambda: pytest.fail("GET invoked FFmpeg"))
    args = ["edit", "qc-get", "--campaign-id", request.campaign_id, "--video-id", request.video_id,
            "--plan-hash", request.plan_hash, "--output-variant-id", request.outputs[0].output_variant_id,
            "--render-key", request.outputs[0].render_key, "--qc-key", batch.outputs[0].qc_key, *roots(service)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "blocked"
    master.write_bytes(b"changed-again")
    stale = CliRunner().invoke(app, args)
    assert stale.exit_code == 1 and stale.stdout == ""


def test_cli_rejects_invalid_request_and_sanitizes_diagnostics(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "request.json"
    file.write_text(json.dumps({"campaignId": "private-token-secret"}))
    monkeypatch.setenv("AURALY_PROJECT_ROOT", str(tmp_path))
    result = CliRunner().invoke(app, ["edit", "qc", "--request", str(file)])
    assert result.exit_code == 1 and result.stdout == ""
    assert "Traceback" not in result.output and "private-token-secret" not in result.output
    assert str(tmp_path) not in result.output


def test_qc_example_validates_against_request_schema() -> None:
    schema = json.loads(Path("schemas/qc-request.schema.json").read_bytes())
    Draft202012Validator(schema).validate(json.loads(Path("examples/qc-request.json").read_bytes()))
