from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.heygen.domain import (
    AssetBatchState,
    AssetSource,
    AssetUploadSlot,
    ProviderAssetStatus,
    RemoteAssetKind,
    RemoteAssetStatus,
)
from auraly_pipeline.heygen.repository import (
    RemoteAssetPersistenceError,
    RemoteAssetRepository,
)


NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
IMAGE = AssetSource(
    source_id="00000000-0000-4000-8000-000000000001",
    kind=RemoteAssetKind.IMAGE,
    local_path="campaigns/one/image.png",
    sha256="1" * 64,
    mime_type="image/png",
    size_bytes=10,
)
AUDIO = AssetSource(
    source_id="00000000-0000-4000-8000-000000000002",
    kind=RemoteAssetKind.AUDIO,
    local_path="campaigns/one/voice.wav",
    sha256="2" * 64,
    mime_type="audio/wav",
    size_bytes=20,
)
SLOTS = [
    AssetUploadSlot(
        source_id=IMAGE.source_id,
        asset_id="asset-image",
        upload_url="https://storage.example/image",
        upload_headers={},
        size_bytes=10,
        expires_in_seconds=300,
        max_bytes=10,
    ),
    AssetUploadSlot(
        source_id=AUDIO.source_id,
        asset_id="asset-audio",
        upload_url="https://storage.example/audio",
        upload_headers={},
        size_bytes=20,
        expires_in_seconds=300,
        max_bytes=20,
    ),
]


@pytest.fixture
def repository(tmp_path: Path) -> RemoteAssetRepository:
    database = tmp_path / "auraly.db"
    migrate_database(database)
    engine = create_sqlite_engine(database)
    return RemoteAssetRepository(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))


def test_record_allocation_persists_ids_in_submitted_order(
    repository: RemoteAssetRepository,
) -> None:
    rows = repository.record_allocation("account-a", "batch-1", [IMAGE, AUDIO], SLOTS, NOW)
    assert [(row.sha256, row.remote_asset_id) for row in rows] == [
        (IMAGE.sha256, "asset-image"),
        (AUDIO.sha256, "asset-audio"),
    ]


def test_mismatched_slot_count_rolls_back_every_row(
    repository: RemoteAssetRepository,
) -> None:
    with pytest.raises(RemoteAssetPersistenceError):
        repository.record_allocation("account-a", "batch-1", [IMAGE, AUDIO], SLOTS[:1], NOW)
    assert repository.find_by_keys("account-a", [IMAGE, AUDIO]) == []


def test_allocation_replay_is_idempotent_and_account_scoped(
    repository: RemoteAssetRepository,
) -> None:
    first = repository.record_allocation("account-a", "batch-1", [IMAGE, AUDIO], SLOTS, NOW)
    replay = repository.record_allocation("account-a", "batch-1", [IMAGE, AUDIO], SLOTS, NOW)

    assert [asset.remote_asset_id_local for asset in replay] == [
        asset.remote_asset_id_local for asset in first
    ]
    assert repository.find_by_keys("account-b", [IMAGE, AUDIO]) == []


def test_batch_state_preserves_ready_and_records_failure(
    repository: RemoteAssetRepository,
) -> None:
    repository.record_allocation("account-a", "batch-1", [IMAGE, AUDIO], SLOTS, NOW)
    repository.apply_batch_state(
        AssetBatchState(
            batch_id="batch-1",
            statuses={
                "asset-image": ProviderAssetStatus.COMPLETED,
                "asset-audio": ProviderAssetStatus.FAILED,
            },
            error_codes={"asset-audio": "invalid_audio"},
            error_messages={"asset-audio": "Audio rejected"},
        ),
        NOW + timedelta(seconds=1),
    )
    repository.apply_batch_state(
        AssetBatchState(
            batch_id="batch-1",
            statuses={
                "asset-image": ProviderAssetStatus.PROCESSING,
                "asset-audio": ProviderAssetStatus.FAILED,
            },
        ),
        NOW + timedelta(seconds=2),
    )

    rows = {row.remote_asset_id: row for row in repository.list_by_batch("batch-1")}
    assert rows["asset-image"].status is RemoteAssetStatus.READY
    assert rows["asset-audio"].status is RemoteAssetStatus.FAILED
    assert rows["asset-audio"].last_error_code == "invalid_audio"
