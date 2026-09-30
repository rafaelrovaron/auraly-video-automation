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
from tests.test_voice_external_import import setup_import


def test_plan_has_three_images_and_one_shared_voice(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path)
    service = HeyGenService.for_database(database, tmp_path, provider=FakeHeyGenProvider())

    plan = service.plan_assets("campaign-one")

    assert len(plan.sources) == 4
    assert [item.kind for item in plan.sources].count(RemoteAssetKind.AUDIO) == 1
    assert len({item.sha256 for item in plan.sources if item.kind is RemoteAssetKind.AUDIO}) == 1
    service.close()


def test_heygen_reuses_approved_imported_wav_only(tmp_path: Path) -> None:
    import hashlib
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from auraly_pipeline.campaigns.db_models import SceneVariantRow
    from auraly_pipeline.campaigns.persistence import create_sqlite_engine
    from auraly_pipeline.images.db_models import ImageCandidateRow

    imports, voices, source, request = setup_import(tmp_path)
    root = source.parent / "work"
    database = tmp_path / "test.db"
    submitted = imports.import_audio(request, source=source)
    imports.worker_once("integration", campaign_id=request.campaign_id)
    provider = FakeHeyGenProvider()
    service = HeyGenService.for_database(database, root, provider=provider)
    engine = create_sqlite_engine(database)
    try:
        with pytest.raises(HeyGenServiceError, match="approved Voice Master"):
            service.plan_assets(request.campaign_id)
        with Session(engine) as session:
            for index, scene in enumerate(session.scalars(select(SceneVariantRow))):
                relative = f"image-{index}.png"
                content = f"fixture-{index}".encode()
                (root / relative).write_bytes(content)
                now = datetime.now(UTC)
                session.add(
                    ImageCandidateRow(
                        id=str(uuid4()),
                        scene_variant_id=scene.id,
                        source_kind="manual_import",
                        import_manifest_sha256="a" * 64,
                        import_source_path=relative,
                        candidate_index=0,
                        source_path=relative,
                        sha256=hashlib.sha256(content).hexdigest(),
                        width=1080,
                        height=1920,
                        size_bytes=len(content),
                        format="png",
                        review_status="approved",
                        approved_at=now,
                        approved_by="tester",
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.commit()
        approved = voices.approve(submitted.voice_master.voice_master_id, approved_by="tester")
        plan = service.plan_assets(request.campaign_id)
        audio = [item for item in plan.sources if item.kind is RemoteAssetKind.AUDIO]
        assert len(audio) == 1
        assert (audio[0].sha256, audio[0].local_path, audio[0].mime_type) == (
            approved.processed_sha256,
            approved.processed_audio_path,
            "audio/wav",
        )
        upload = service.submit_assets(plan)
        assert upload.job is not None
        worked = service._jobs.worker_once("fake-upload", job_type="heygen.asset.upload")
        assert worked is not None and worked.status == "completed"
        assert not service.plan_assets(request.campaign_id).upload_sources
    finally:
        engine.dispose()
        service.close()
        imports.close()
        voices.close()


def test_submit_creates_one_reconcile_before_retry_batch_job(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    create_ready_campaign(database, tmp_path)
    service = HeyGenService.for_database(database, tmp_path, provider=FakeHeyGenProvider())

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
    service = HeyGenService.for_database(database, tmp_path, provider=FakeHeyGenProvider())

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
                statuses={slot.asset_id: ProviderAssetStatus.QUEUED for slot in allocation.slots},
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
