from __future__ import annotations

import pytest
from pydantic import ValidationError

from auraly_pipeline.heygen.domain import (
    AssetSource,
    AssetUploadJobInput,
    AssetUploadSlot,
    RemoteAssetKind,
    asset_batch_idempotency_key,
)


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


def test_asset_batch_key_is_order_independent_and_account_scoped() -> None:
    first = asset_batch_idempotency_key("account-a", [IMAGE, AUDIO])
    assert first == asset_batch_idempotency_key("account-a", [AUDIO, IMAGE])
    assert first != asset_batch_idempotency_key("account-b", [IMAGE, AUDIO])
    assert first.startswith("heygen.asset.upload:")


def test_asset_source_rejects_absolute_path_and_bad_hash() -> None:
    with pytest.raises(ValidationError):
        AssetSource(
            source_id="00000000-0000-4000-8000-000000000001",
            kind="image",
            local_path="C:/secret.png",
            sha256="bad",
            mime_type="image/png",
            size_bytes=1,
        )


def test_job_input_rejects_more_than_one_hundred_sources() -> None:
    with pytest.raises(ValidationError):
        AssetUploadJobInput(
            account_ref="account-a",
            idempotency_key="heygen.asset.upload:" + "0" * 64,
            sources=[IMAGE] * 101,
        )


def test_upload_slot_never_serializes_or_reprs_temporary_credentials() -> None:
    slot = AssetUploadSlot(
        source_id=IMAGE.source_id,
        asset_id="asset-1",
        upload_url="https://storage.example/signed?token=secret",
        upload_headers={"Authorization": "secret"},
        size_bytes=10,
        expires_in_seconds=300,
        max_bytes=100,
    )

    assert "secret" not in repr(slot)
    assert "uploadUrl" not in slot.model_dump(by_alias=True)
    assert "uploadHeaders" not in slot.model_dump(by_alias=True)
