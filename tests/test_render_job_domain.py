from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auraly_pipeline.editing.render_domain import RenderBatchResult
from tests.test_verify_harness import load_verify_module


def domain() -> Any:
    name = "auraly_pipeline.editing.render_job_domain"
    assert importlib.util.find_spec(name), "render job contracts not implemented"
    return importlib.import_module(name)


def request_data() -> dict[str, Any]:
    return {"schemaVersion": "1.0", "campaignId": "campaign-one", "videoId": "video-one",
            "planHash": "a" * 64, "executionId": "11111111-1111-4111-8111-111111111111"}


def result_data(statuses: tuple[str, ...] = ("rendered", "reused")) -> dict[str, Any]:
    return {"schemaVersion": "1.0", "planHash": "a" * 64, "dryRun": False, "outputs": [
        {"key": key, "outputVariantId": "variant-" + key, "status": status, "renderKey": "b" * 64,
         "fitMeasured": status in {"rendered", "reused"},
         "path": "masters/" + key + ".mp4" if status != "failed" else None,
         "error": {"field": "font", "message": "Local font missing."} if status == "failed" else None}
        for key, status in zip(("a", "b", "c"), statuses, strict=False)]}


@pytest.mark.parametrize("change", [{"sourcePath": "../private.mp4"}, {"overrides": {}},
    {"executionId": "bad"}, {"videoId": "../escape"}, {"videoId": "con"}, {"planHash": "bad"}])
def test_request_rejects_paths_overrides_unknown_fields_and_invalid_uuid(change: dict[str, Any]) -> None:
    contract = domain().RenderJobRequest
    assert contract.model_validate(request_data()).campaign_id == "campaign-one"
    with pytest.raises(ValidationError):
        contract.model_validate({**request_data(), **change})


@pytest.mark.parametrize("statuses,want", [(("rendered", "reused"), "succeeded"),
    (("rendered", "failed"), "partial_failure"), (("failed",), "failed")])
def test_aggregate_requires_real_nonempty_results(statuses: tuple[str, ...], want: str) -> None:
    assert domain().render_status(RenderBatchResult.model_validate(result_data(statuses))) == want


@pytest.mark.parametrize("change", [{"outputs": []}, {"dryRun": True},
    {"outputs": result_data(("planned",))["outputs"]},
    {"outputs": result_data(("rendered",))["outputs"] * 2}])
def test_invalid_batch_is_not_a_completed_job_result(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        domain().validate_job_result(RenderBatchResult.model_validate({**result_data(), **change}))


def test_view_requires_result_only_on_completed() -> None:
    domain()
    contract = importlib.import_module("auraly_pipeline.api.render_contracts").RenderJobView
    body = {**request_data(), "jobId": "job-one", "status": "queued"}
    assert contract.model_validate(body).result is None
    result = result_data(("rendered", "failed"))
    valid = {**body, "status": "completed", "result": result, "renderStatus": "partial_failure"}
    assert contract.model_validate(valid).render_status == "partial_failure"
    for change in ({"status": "running"}, {"result": None}, {"renderStatus": "succeeded"},
                   {"errorCode": "internal_error"}, {"planHash": "c" * 64}):
        with pytest.raises(ValidationError):
            contract.model_validate({**valid, **change})


def test_new_schema_drift_fails_in_full_harness(tmp_path: Path) -> None:
    verify = load_verify_module()
    step = next(s for s in verify.build_full_steps() if s.name == "editing schemas")
    paths = [Path("schemas/render-job-" + kind + ".schema.json") for kind in ("request", "submission", "view")]
    assert set(paths) <= set(step.generated_files)
    for path in paths:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("{}", encoding="utf-8")
    import subprocess
    def change(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        (tmp_path / paths[0]).write_text('{"changed":true}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)
    assert verify.run_steps((step,), repository_root=tmp_path, run_command=change,
                                   output=lambda _: None) == 1
