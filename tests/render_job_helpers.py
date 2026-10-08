from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from auraly_pipeline.api.commands import ApiCommands
from auraly_pipeline.api.contracts import ApiSettings
from auraly_pipeline.campaigns.persistence import create_existing_sqlite_engine
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.render_job_domain import RenderJobRequest
from tests.api_helpers import create_api_fixture
from tests.render_helpers import make_render_plan, publish_test_plan


@dataclass
class RenderApiCase:
    settings: ApiSettings
    engine: Engine
    commands: ApiCommands
    plan: EditBatchPlan
    request: RenderJobRequest


def render_api_case(tmp_path: Path) -> RenderApiCase:
    settings = create_api_fixture(tmp_path)
    plan = make_render_plan(tmp_path)
    publish_test_plan(plan, settings.work_root)
    engine = create_existing_sqlite_engine(settings.database)
    return RenderApiCase(settings, engine, ApiCommands(settings, engine), plan,
        RenderJobRequest(campaign_id=plan.campaign_id, video_id=plan.video_id, plan_hash=plan.plan_hash,
                         execution_id=UUID("11111111-1111-4111-8111-111111111111")))


@pytest.fixture(name="render_case")
def render_case(tmp_path: Path) -> Iterator[RenderApiCase]:
    case = render_api_case(tmp_path)
    try:
        yield case
    finally:
        case.engine.dispose()


def run_render(case: RenderApiCase, request: RenderJobRequest | None = None) -> str:
    sent = case.commands.editorial_renders.submit(request or case.request)
    case.commands.jobs.worker_once("render-test-worker", campaign_id=case.plan.campaign_id, job_type="editing.render")
    return sent.job_id
