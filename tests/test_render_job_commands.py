from __future__ import annotations

import json
import sqlite3
from typing import Any, cast
from uuid import UUID

import pytest

from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.jobs.domain import JobSubmit, RetrySafety
from tests.render_job_helpers import RenderApiCase, render_case as provide_render_case, run_render  # noqa: F401


def test_submission_is_idempotent_and_does_not_encode(render_case: RenderApiCase, monkeypatch: pytest.MonkeyPatch) -> None:
    case = render_case
    def forbidden(*a: object, **k: object) -> None:
        pytest.fail("submission must not perform media work")
    monkeypatch.setattr(case.commands.renderer, "render", forbidden)
    first = case.commands.editorial_renders.submit(case.request)
    assert case.commands.editorial_renders.submit(case.request).job_id == first.job_id
    job = case.commands.jobs.get_job(first.job_id)
    assert job.status == "queued" and job.max_attempts == 1 and job.retry_safety == "manual_only"
    view = case.commands.editorial_renders.get(case.plan.campaign_id, first.job_id)
    assert view.result is None and view.render_status is None
    assert not list(case.settings.work_root.glob("campaigns/*/editing/renders/*/*/*.mp4"))


def test_explicit_new_execution_gets_new_job_and_reuses_outputs(render_case: RenderApiCase) -> None:
    case = render_case
    first = run_render(case)
    view = case.commands.editorial_renders.get(case.plan.campaign_id, first)
    assert view.result is not None and view.render_status == "succeeded"
    assert [o.status for o in view.result.outputs] == ["rendered"] * 3
    receipts = {p: p.read_bytes() for p in case.commands.renderer.work_root.rglob("render.json")}
    request = case.request.model_copy(update={"execution_id": UUID("22222222-2222-4222-8222-222222222222")})
    second = run_render(case, request)
    assert first != second
    again = case.commands.editorial_renders.get(case.plan.campaign_id, second)
    assert again.result is not None and [o.status for o in again.result.outputs] == ["reused"] * 3
    assert receipts == {p: p.read_bytes() for p in receipts}
    jobs = case.commands.editorial_renders.list(case.plan.campaign_id, video_id=case.plan.video_id, plan_hash=case.plan.plan_hash)
    assert {j.job_id for j in jobs} == {first, second}
    assert case.commands.editorial_renders.list(case.plan.campaign_id, video_id="other") == []


@pytest.mark.parametrize("mutation", ["plan", "missing", "extra", "order", "variant", "request"])
def test_views_validate_job_plan_and_output_identity(render_case: RenderApiCase, mutation: str) -> None:
    case = render_case
    job_id = run_render(case)
    job = case.commands.jobs.get_job(job_id)
    assert job.output is not None
    output = cast(dict[str, Any], job.output)
    payload = job.input
    if mutation == "plan":
        output["planHash"] = "c" * 64
    elif mutation == "missing":
        output["outputs"] = output["outputs"][:-1]
    elif mutation == "extra":
        output["outputs"] = output["outputs"] * 2
    elif mutation == "order":
        output["outputs"] = output["outputs"][::-1]
    elif mutation == "variant":
        output["outputs"][0]["outputVariantId"] = "foreign"
    else:
        payload["campaignId"] = "other"
    with sqlite3.connect(case.settings.database) as conn:
        conn.execute("UPDATE jobs SET output_json=?,input_json=? WHERE id=?", (json.dumps(output), json.dumps(payload), job_id))
    with pytest.raises(QueryError) as error:
        case.commands.editorial_renders.get(case.plan.campaign_id, job_id)
    assert error.value.code == "artifact_invalid"
    with pytest.raises(QueryError) as listing:
        case.commands.editorial_renders.list(case.plan.campaign_id)
    assert listing.value.code == "artifact_invalid"


def test_foreign_job_type_is_not_exposed(render_case: RenderApiCase) -> None:
    case = render_case
    other = case.commands.jobs.submit_job(JobSubmit(campaign_id=case.plan.campaign_id,
        job_type="voice.import", input={"voiceMasterId": "never-run"}, idempotency_key="other-type",
        retry_safety=RetrySafety.MANUAL_ONLY))
    with pytest.raises(QueryError) as error:
        case.commands.editorial_renders.get(case.plan.campaign_id, other.job_id)
    assert error.value.code == "not_found"


def test_missing_plan_does_not_enqueue(render_case: RenderApiCase) -> None:
    case = render_case
    with pytest.raises(QueryError):
        case.commands.editorial_renders.submit(case.request.model_copy(update={"plan_hash": "c" * 64}))
    assert not case.commands.jobs.list_jobs(campaign_id=case.plan.campaign_id)
