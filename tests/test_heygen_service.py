from __future__ import annotations

from pathlib import Path

from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.domain import RemoteAssetKind
from auraly_pipeline.heygen.service import HeyGenService
from auraly_pipeline.jobs.domain import RetrySafety
from tests.heygen_support import create_ready_campaign


def test_plan_has_three_images_and_one_shared_voice(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path)
    service = HeyGenService.for_database(
        database, tmp_path, provider=FakeHeyGenProvider()
    )

    plan = service.plan_assets("campaign-one")

    assert len(plan.sources) == 4
    assert [item.kind for item in plan.sources].count(RemoteAssetKind.AUDIO) == 1
    assert len({item.sha256 for item in plan.sources if item.kind is RemoteAssetKind.AUDIO}) == 1
    service.close()


def test_submit_creates_one_reconcile_before_retry_batch_job(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path)
    service = HeyGenService.for_database(
        database, tmp_path, provider=FakeHeyGenProvider()
    )

    submitted = service.submit_assets(service.plan_assets("campaign-one"))

    assert submitted.job is not None
    assert submitted.job.job_type == "heygen.asset.upload"
    assert submitted.job.retry_safety is RetrySafety.RECONCILE_BEFORE_RETRY
    sources = submitted.job.input["sources"]
    assert isinstance(sources, list) and len(sources) == 4
    service.close()


def test_replay_reuses_same_job(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path)
    service = HeyGenService.for_database(
        database, tmp_path, provider=FakeHeyGenProvider()
    )

    first = service.submit_assets(service.plan_assets("campaign-one"))
    second = service.submit_assets(service.plan_assets("campaign-one"))

    assert first.job is not None and second.job is not None
    assert second.job.job_id == first.job.job_id
    service.close()
