from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_domain import RenderBatchResult
from auraly_pipeline.editing.render_service import RenderService
from auraly_pipeline.jobs.domain import JobExecutionOutcome
from auraly_pipeline.jobs.handlers import JobExecutionContext
from tests.test_render_job_domain import request_data, result_data


def handler(renderer: RenderService) -> Any:
    name = "auraly_pipeline.editing.render_handler"
    assert importlib.util.find_spec(name), "render handler not implemented"
    return importlib.import_module(name).RenderJobHandler(renderer)


def context(campaign: str = "campaign-one") -> JobExecutionContext:
    return JobExecutionContext(job_id="render-job", job_type="editing.render", campaign_id=campaign,
                               input=request_data(), attempt_number=1)


def test_handler_checks_campaign_and_calls_saved_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = RenderService(project_root=tmp_path, work_root=tmp_path / "work")
    def render(campaign_id: str, video_id: str, plan_hash: str, *, dry_run: bool = False) -> RenderBatchResult:
        assert (campaign_id, video_id, plan_hash, dry_run) == ("campaign-one", "video-one", "a" * 64, False)
        return RenderBatchResult.model_validate(result_data())
    monkeypatch.setattr(renderer, "render", render)
    unit = handler(renderer)
    assert unit.execute(context("campaign-two")).outcome == JobExecutionOutcome.TERMINAL_FAILURE
    assert unit.execute(context()).result["planHash"] == "a" * 64


def test_partial_result_completes_with_all_variant_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = RenderService(project_root=tmp_path, work_root=tmp_path / "work")
    monkeypatch.setattr(renderer, "render", lambda *a, **k: RenderBatchResult.model_validate(result_data(("rendered", "failed"))))
    outcome = handler(renderer).execute(context())
    assert outcome.outcome == JobExecutionOutcome.SUCCESS
    assert outcome.result["outputs"] == result_data(("rendered", "failed"))["outputs"]
    assert "hasFailures" not in outcome.result


def test_handler_rejects_result_for_different_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = RenderService(project_root=tmp_path, work_root=tmp_path / "work")
    monkeypatch.setattr(renderer, "render", lambda *a, **k: RenderBatchResult.model_validate({**result_data(), "planHash": "c" * 64}))
    assert handler(renderer).execute(context()).outcome == JobExecutionOutcome.TERMINAL_FAILURE


@pytest.mark.parametrize("error", [EditingError("source", "C:/private/token-secret"), RuntimeError("token-secret")])
def test_global_failure_is_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    renderer = RenderService(project_root=tmp_path, work_root=tmp_path / "work")
    def fail(*a: Any, **k: Any) -> RenderBatchResult:
        raise error
    monkeypatch.setattr(renderer, "render", fail)
    outcome = handler(renderer).execute(context())
    assert outcome.outcome == JobExecutionOutcome.TERMINAL_FAILURE
    assert "private" not in outcome.model_dump_json() and "token" not in outcome.model_dump_json()
