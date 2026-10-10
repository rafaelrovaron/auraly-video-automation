from __future__ import annotations

from pathlib import Path

import pytest

from auraly_pipeline.editing.qc_domain import QcReport, QcRequest, QcTarget
from auraly_pipeline.editing.qc_service import QcService
from auraly_pipeline.editing.render_service import RenderService
from auraly_pipeline.editing.review_domain import HumanReviewRequest
from auraly_pipeline.editing.review_service import HumanReviewService
from tests.render_helpers import make_render_plan, publish_test_plan, refresh_plan


@pytest.fixture
def review_case(tmp_path: Path) -> tuple[HumanReviewService, HumanReviewRequest, QcReport]:
    project = tmp_path / "project"
    plan = make_render_plan(project)
    plan.outputs = plan.outputs[:1]
    plan.output_count = 1
    refresh_plan(plan)
    renderer = RenderService(project_root=project, work_root=project / "work")
    publish_test_plan(plan, renderer.work_root)
    rendered = renderer.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert not rendered.has_failures
    target = rendered.outputs[0]
    qc = QcService(project_root=project, work_root=renderer.work_root)
    result = qc.run(QcRequest(campaign_id=plan.campaign_id, video_id=plan.video_id,
        plan_hash=plan.plan_hash, outputs=[QcTarget(output_variant_id=target.output_variant_id,
                                                 render_key=target.render_key)]))
    item = result.outputs[0]
    assert item.status == "passed" and item.qc_key is not None
    request = HumanReviewRequest(campaign_id=plan.campaign_id, video_id=plan.video_id,
        plan_hash=plan.plan_hash, output_variant_id=target.output_variant_id,
        render_key=target.render_key, qc_key=item.qc_key, decision="approved",
        reviewed_by="Local reviewer", watched_and_listened=True)
    report = qc.get_report(request.campaign_id, request.video_id, request.plan_hash,
                          request.output_variant_id, request.render_key, request.qc_key)
    return HumanReviewService(project_root=project, work_root=renderer.work_root), request, report


def qc_path(service: HumanReviewService, request: HumanReviewRequest) -> Path:
    return (service.work_root / "campaigns" / request.campaign_id / "editing" / "qc"
            / request.output_variant_id / request.render_key / request.qc_key / "report.json")


def review_path(service: HumanReviewService, request: HumanReviewRequest, key: str) -> Path:
    return (service.work_root / "campaigns" / request.campaign_id / "editing" / "reviews"
            / request.output_variant_id / request.render_key / key / "review.json")


def lookup(service: HumanReviewService, request: HumanReviewRequest, key: str):  # type: ignore[no-untyped-def]
    return service.get_review(request.campaign_id, request.video_id, request.plan_hash,
                              request.output_variant_id, request.render_key, key)
