from __future__ import annotations

from pathlib import Path

import pytest

from auraly_pipeline.heygen.domain import (
    AssetBatchState,
    AssetUploadJobInput,
    ProviderAssetStatus,
    RemoteAssetKind,
)
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.service import HeyGenService, HeyGenServiceError
from auraly_pipeline.jobs.domain import RetrySafety
from auraly_pipeline.jobs.state_machine import JobStatus
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


def test_plan_deduplicates_same_kind_and_hash(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path, duplicate_first_two_images=True)
    service = HeyGenService.for_database(database, tmp_path, provider=FakeHeyGenProvider())

    plan = service.plan_assets("campaign-one")

    assert len(plan.sources) == 3
    assert len(plan.upload_sources) == 3
    service.close()


def test_reconcile_rejects_connected_account_change(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path)
    provider = FakeHeyGenProvider()
    service = HeyGenService.for_database(database, tmp_path, provider=provider)
    submission = service.submit_assets(service.plan_assets("campaign-one"))
    assert submission.job is not None
    provider.account_ref = "account-other"
    blocked = service._jobs.worker_once("worker-1", job_type="heygen.asset.upload")
    assert blocked is not None and blocked.status is JobStatus.BLOCKED

    with pytest.raises(HeyGenServiceError, match="account"):
        service.reconcile_upload(blocked.job_id)

    assert provider.events[-1] == "preflight"
    service.close()


def test_reconcile_resumes_queued_checkpoint_with_same_allocation(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path)

    class QueuedProvider(FakeHeyGenProvider):
        def get_asset_batch(self, batch_id: str) -> AssetBatchState:
            self.events.append("poll")
            allocation = next(
                item for item in self._allocations.values() if item.batch_id == batch_id
            )
            return AssetBatchState(
                batch_id=batch_id,
                statuses={
                    slot.asset_id: ProviderAssetStatus.QUEUED for slot in allocation.slots
                },
            )

    provider = QueuedProvider()
    service = HeyGenService.for_database(database, tmp_path, provider=provider)
    submission = service.submit_assets(service.plan_assets("campaign-one"))
    assert submission.job is not None
    provider.account_ref = "account-other"
    blocked = service._jobs.worker_once("worker-1", job_type="heygen.asset.upload")
    assert blocked is not None and blocked.status is JobStatus.BLOCKED
    provider.account_ref = "account-fake"
    request = AssetUploadJobInput.model_validate(blocked.input)
    allocation = provider.allocate_asset_batch(request.sources, request.idempotency_key)
    service._assets.record_allocation(
        request.account_ref,
        allocation.batch_id,
        request.sources,
        allocation.slots,
        blocked.updated_at,
    )

    reconciled = service.reconcile_upload(blocked.job_id)

    assert reconciled.status is JobStatus.QUEUED
    assert provider.events[-3:] == ["preflight", "poll", "allocate"]
    service.close()
