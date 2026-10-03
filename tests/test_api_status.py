from __future__ import annotations

from datetime import timedelta
import importlib
from pathlib import Path
from typing import Any

import pytest

from auraly_pipeline.api.queries import ApiQueries
from auraly_pipeline.campaigns.persistence import create_readonly_sqlite_engine
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.heygen.video_domain import HeyGenRenderStatus
from auraly_pipeline.jobs.state_machine import JobStatus
from auraly_pipeline.voices.domain import VoiceMasterStatus
from tests.api_helpers import create_api_fixture, create_ready_api_fixture

pytest_plugins = ["tests.test_heygen_video_media"]


def compute(campaign: Any, **observed: Any) -> Any:
    assert importlib.util.find_spec("auraly_pipeline.api.status"), "campaign status missing"
    return importlib.import_module("auraly_pipeline.api.status").compute_campaign_status(campaign, **observed)


@pytest.fixture
def observed(tmp_path: Path, mp4: bytes) -> tuple[Any, dict[str, Any], Any, Any]:
    settings, request = create_ready_api_fixture(tmp_path, mp4)
    queries = ApiQueries(settings, create_readonly_sqlite_engine(settings.database))
    campaign = queries.campaign("campaign-one")
    render = next(r for r in queries.renders.list_campaign("campaign-one") if r.render_id == request.render_id)
    scene = next(s for s in campaign.scene_variants if s.scene_variant_id == render.item.scene_variant_id)
    campaign = campaign.model_copy(update={"scene_variants": [scene]})
    facts = {
        "images": queries.images.list_candidates_for_scene(scene.scene_variant_id),
        "voices": queries.voices.list(campaign_id="campaign-one"),
        "renders": [render], "jobs": queries.jobs.list_jobs(campaign_id="campaign-one"), "plans": [],
    }
    return campaign, facts, settings, request


def test_missing_voice_status(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    queries = ApiQueries(settings, create_readonly_sqlite_engine(settings.database))
    assert callable(getattr(queries, "get_status", None)), "status query missing"
    status = queries.get_status("campaign-one")
    assert status.operational_status == "needs_input"
    assert status.next_pending is not None
    assert status.next_pending.code == "voice_missing"


@pytest.mark.parametrize("render_status, expected, code", [
    (HeyGenRenderStatus.READY, "ready_for_editing", "editing_plan_missing"),
    (HeyGenRenderStatus.PROCESSING, "in_progress", "wait_for_job"),
    (HeyGenRenderStatus.FAILED, "needs_attention", "attention_required"),
    (HeyGenRenderStatus.RECONCILIATION_REQUIRED, "needs_attention", "attention_required"),
])
def test_status_priority_and_pending_match(observed: Any, render_status: Any, expected: str, code: str) -> None:
    campaign, facts, _, _ = observed
    facts["renders"] = [facts["renders"][0].model_copy(update={"status": render_status})]
    status = compute(campaign, **facts)
    assert status.operational_status == expected
    assert status.next_pending.code == code
    assert status.scenes[0].pending[0].code == code


def test_voice_job_reports_voice_stage(observed: Any) -> None:
    campaign, facts, _, _ = observed
    facts["renders"] = []
    facts["voices"] = [facts["voices"][0].model_copy(update={"status": VoiceMasterStatus.GENERATING})]
    facts["jobs"] = [next(j for j in facts["jobs"] if j.job_type == "voice.generate")]
    assert compute(campaign, **facts).next_pending.stage == "voice"


@pytest.mark.parametrize("job_status, expected, code", [
    (JobStatus.QUEUED, "in_progress", "wait_for_job"),
    (JobStatus.RUNNING, "in_progress", "wait_for_job"),
    (JobStatus.FAILED, "needs_attention", "attention_required"),
])
def test_current_imported_voice_job_is_visible(observed: Any, job_status: JobStatus, expected: str, code: str) -> None:
    campaign, facts, _, _ = observed
    facts["renders"] = []
    facts["voices"] = [facts["voices"][0].model_copy(update={"status": VoiceMasterStatus.GENERATING})]
    job = next(j for j in facts["jobs"] if j.job_type == "voice.generate")
    facts["jobs"] = [job.model_copy(update={"job_type": "voice.import", "status": job_status})]
    status = compute(campaign, **facts)
    assert status.operational_status == expected
    assert status.next_pending.code == code
    assert status.next_pending.stage == "voice"


def test_ready_render_retains_pinned_history(observed: Any) -> None:
    campaign, facts, _, _ = observed
    draft = campaign.copy_masters[0].model_copy(update={"version": 2, "approval_state": "draft"})
    campaign = campaign.model_copy(update={"copy_masters": [*campaign.copy_masters, draft]})
    facts["images"] = [facts["images"][0].model_copy(update={"review_status": "superseded"})]
    facts["voices"] = []
    status = compute(campaign, **facts)
    assert status.operational_status == "ready_for_editing"
    assert status.ready_render_count == 1


def test_historical_failure_and_paused_flow_do_not_block(observed: Any) -> None:
    campaign, facts, _, _ = observed
    ready = facts["renders"][0]
    failed = ready.model_copy(update={
        "render_id": "99999999-9999-4999-8999-999999999999", "status": HeyGenRenderStatus.FAILED,
        "created_at": ready.created_at - timedelta(seconds=10),
    })
    facts["renders"].append(failed)
    failed_job = facts["jobs"][0].model_copy(update={"status": JobStatus.FAILED})
    facts["jobs"].append(failed_job)
    facts["jobs"].append(failed_job.model_copy(update={"job_type": "image.generate"}))
    assert compute(campaign, **facts).operational_status == "ready_for_editing"


def test_multiple_renders_and_plans_remain_visible(observed: Any) -> None:
    campaign, facts, settings, request = observed
    batch = EditBatchService(project_root=settings.project_root, work_root=settings.work_root)
    first = batch.plan(request, database_path=settings.database, persist=False)
    second = batch.plan(request.model_copy(update={"video_id": "video-two"}),
                        database_path=settings.database, persist=False)
    facts["plans"] = [first, second]
    status = compute(campaign, **facts)
    assert status.operational_status == "editing_planned"
    assert status.plan_count == 2
    assert status.scenes[0].plan_hashes == sorted([first.plan_hash, second.plan_hash])
    facts["renders"].append(facts["renders"][0].model_copy(update={
        "render_id": "99999999-9999-4999-8999-999999999999",
    }))
    assert compute(campaign, **facts).operational_status == "ready_for_editing"


def test_disabled_captions_need_no_timing(observed: Any) -> None:
    campaign, facts, settings, request = observed
    facts["plans"] = [EditBatchService(project_root=settings.project_root, work_root=settings.work_root).plan(
        request, database_path=settings.database, persist=False)]
    status = compute(campaign, **facts)
    assert status.operational_status == "editing_planned"
    assert status.next_pending.code == "renderer_not_implemented"


def test_missing_timing_needs_input(observed: Any) -> None:
    campaign, facts, settings, request = observed
    from auraly_pipeline.editing.batch_domain import BatchInputs, EditBatchRequest
    from auraly_pipeline.editing.batch_planner import build_batch_plan, variant_requests
    from auraly_pipeline.editing.resolver import resolve_manifest

    service = EditBatchService(project_root=settings.project_root, work_root=settings.work_root)
    base = service.plan(request, database_path=settings.database, persist=False)
    inputs = BatchInputs(source=base.source, copy=campaign.copy_masters[0],
                         voice_ref=base.voice_ref, image_ref=base.image_ref)
    data = request.model_dump(by_alias=True)
    data["variants"][0]["overrides"]["captions"] = {
        "enabled": True, "font": {"path": "font.ttf", "sha256": "a" * 64},
    }
    enabled = EditBatchRequest.model_validate(data)
    profile = service.editing.get_profile(request.profile_ref.profile_id, request.profile_ref.version)
    manifests = [resolve_manifest(profile, item) for _, item in variant_requests(enabled, inputs)]
    plan = build_batch_plan(enabled, inputs, manifests)
    facts["plans"] = [plan]
    status = compute(campaign, **facts)
    assert status.operational_status == "needs_input"
    assert status.next_pending.code == "caption_timing_missing"


def test_mixed_scenes_and_tie_order(observed: Any) -> None:
    campaign, facts, _, _ = observed
    scene = campaign.scene_variants[0].model_copy(update={
        "scene_variant_id": "99999999-9999-4999-8999-999999999999", "variant_id": "aaa",
    })
    campaign = campaign.model_copy(update={"scene_variants": [*campaign.scene_variants, scene]})
    status = compute(campaign, **facts)
    assert status.operational_status == "needs_input"
    assert status.next_pending.code == "image_missing"
    assert status.ready_render_count == 1
    reverse = campaign.model_copy(update={"scene_variants": list(reversed(campaign.scene_variants))})
    assert compute(reverse, **{k: list(reversed(v)) for k, v in facts.items()}) == status


def test_current_voice_pins_copy(observed: Any) -> None:
    campaign, facts, _, _ = observed
    facts["renders"] = []
    draft = campaign.copy_masters[0].model_copy(update={"version": 2, "approval_state": "draft"})
    campaign = campaign.model_copy(update={"copy_masters": [*campaign.copy_masters, draft]})
    assert compute(campaign, **facts).next_pending.code == "heygen_render_missing"
    facts["voices"] = [facts["voices"][0].model_copy(update={"status": VoiceMasterStatus.REVIEW_REQUIRED})]
    facts["jobs"] = []
    status = compute(campaign, **facts)
    assert status.operational_status == "needs_review"
    assert status.next_pending.code == "voice_review_required"


@pytest.mark.parametrize("job_status, expected", [
    (JobStatus.QUEUED, "in_progress"), (JobStatus.FAILED, "needs_attention"),
])
def test_current_asset_upload_is_relevant(observed: Any, job_status: JobStatus, expected: str) -> None:
    campaign, facts, _, _ = observed
    facts["renders"] = []
    upload = next(job for job in facts["jobs"] if job.job_type == "heygen.asset.upload")
    facts["jobs"] = [upload.model_copy(update={"status": job_status})]
    assert compute(campaign, **facts).operational_status == expected


@pytest.mark.parametrize("case, expected, code", [
    ("copy", "needs_input", "copy_approval_missing"),
    ("voice", "needs_input", "voice_missing"),
    ("voice_review", "needs_review", "voice_review_required"),
    ("voice_active", "in_progress", "wait_for_job"),
    ("voice_failed", "needs_attention", "attention_required"),
    ("image", "needs_input", "image_missing"),
    ("image_review", "needs_review", "image_review_required"),
    ("heygen", "needs_input", "heygen_render_missing"),
])
def test_unrendered_prerequisite_states(observed: Any, case: str, expected: str, code: str) -> None:
    campaign, facts, _, _ = observed
    facts["renders"] = []
    jobs, facts["jobs"] = facts["jobs"], []
    if case == "copy":
        campaign = campaign.model_copy(update={"copy_masters": []})
    elif case == "voice":
        facts["voices"] = []
    elif case.startswith("voice_"):
        states = {"voice_review": VoiceMasterStatus.REVIEW_REQUIRED,
                  "voice_active": VoiceMasterStatus.GENERATING, "voice_failed": VoiceMasterStatus.FAILED}
        facts["voices"] = [facts["voices"][0].model_copy(update={"status": states[case]})]
        if case in {"voice_active", "voice_failed"}:
            job = next(j for j in jobs if j.job_type == "voice.generate")
            facts["jobs"] = [job.model_copy(update={"status": JobStatus.FAILED if case == "voice_failed" else JobStatus.QUEUED})]
    elif case == "image":
        facts["images"] = []
    elif case == "image_review":
        facts["images"] = [facts["images"][0].model_copy(update={"review_status": "pending_review"})]
    status = compute(campaign, **facts)
    assert status.operational_status == expected
    assert status.next_pending.code == code


def test_ready_without_source_fails_safely(observed: Any) -> None:
    from auraly_pipeline.api.contracts import QueryError

    campaign, facts, _, _ = observed
    facts["renders"] = [facts["renders"][0].model_copy(update={"source": None})]
    with pytest.raises(QueryError) as error:
        compute(campaign, **facts)
    assert error.value.code == "artifact_invalid"
