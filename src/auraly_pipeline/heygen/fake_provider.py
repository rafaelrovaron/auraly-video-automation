from __future__ import annotations

from collections.abc import Sequence
import hashlib
from pathlib import Path
from typing import Literal

from auraly_pipeline.heygen.domain import (
    AssetBatchAllocation,
    AssetBatchState,
    AssetSource,
    AssetUploadSlot,
    HeyGenPreflight,
    ProviderAssetStatus,
)
from auraly_pipeline.heygen.provider import HeyGenProviderFailure, REQUIRED_TOOLS
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig, ProviderVideo, VideoPlanItem, VideoPreflight


class FakeHeyGenProvider:
    def __init__(
        self,
        *,
        scenario: Literal["success", "terminal", "timeout", "ambiguous"] = "success",
        account_ref: str = "account-fake",
    ) -> None:
        self.scenario = scenario
        self.account_ref = account_ref
        self.events: list[str] = []
        self._allocations: dict[str, AssetBatchAllocation] = {}
        self._videos: dict[str, tuple[VideoPlanItem,str]] = {}

    def connect(self) -> HeyGenPreflight:
        return self.preflight()

    def preflight_video(self, config: HeyGenVideoConfig) -> VideoPreflight:
        return VideoPreflight(account_ref=self.account_ref,schema_fingerprint=hashlib.sha256(b'fake-video-schema-v1').hexdigest())

    def create_video(self, item: VideoPlanItem, *, callback_id: str) -> str:
        self.events.append('create_video')
        if self.scenario=='ambiguous':
            raise HeyGenProviderFailure('ambiguous','Fake video outcome unknown',request_dispatched=True)
        video_id=f'video-{len(self._videos)+1}'
        self._videos[video_id]=(item,callback_id)
        return video_id

    def get_video(self, video_id: str) -> ProviderVideo:
        self.events.append('get_video')
        if video_id not in self._videos:
            raise HeyGenProviderFailure('configuration','Fake video not found')
        item,callback_id=self._videos[video_id]
        status=ProviderAssetStatus.COMPLETED
        if self.scenario=='timeout':
            status=ProviderAssetStatus.PROCESSING
        elif self.scenario=='terminal':
            status=ProviderAssetStatus.FAILED
        return ProviderVideo(video_id=video_id,status=status,download_url=f'https://fake.invalid/{video_id}.mp4',callback_id=callback_id,image_asset_id=item.image_asset_id,audio_asset_id=item.audio_asset_id)

    def preflight(self) -> HeyGenPreflight:
        self.events.append("preflight")
        return HeyGenPreflight(
            connected=True,
            account_ref=self.account_ref,
            capabilities=sorted(REQUIRED_TOOLS),
            max_batch_size=100,
        )

    def allocate_asset_batch(
        self, sources: Sequence[AssetSource], idempotency_key: str
    ) -> AssetBatchAllocation:
        self.events.append("allocate")
        if self.scenario != "success":
            kind = {
                "terminal": "terminal",
                "timeout": "retryable",
                "ambiguous": "ambiguous",
            }[self.scenario]
            raise HeyGenProviderFailure(kind, "Fake allocation failure")  # type: ignore[arg-type]
        if idempotency_key not in self._allocations:
            batch_number = len(self._allocations) + 1
            self._allocations[idempotency_key] = AssetBatchAllocation(
                batch_id=f"batch-{batch_number}",
                slots=[
                    AssetUploadSlot(
                        source_id=source.source_id,
                        asset_id=f"asset-{source.source_id}",
                        upload_url=f"https://fake.invalid/{source.source_id}",
                        upload_headers={},
                        size_bytes=source.size_bytes,
                        expires_in_seconds=300,
                        max_bytes=source.size_bytes,
                    )
                    for source in sources
                ],
            )
        return self._allocations[idempotency_key]

    def upload_file(self, slot: AssetUploadSlot, local_path: Path) -> None:
        self.events.append(f"put:{slot.source_id}")
        if not local_path.is_file() or local_path.stat().st_size != slot.size_bytes:
            raise HeyGenProviderFailure("terminal", "Local upload source changed")

    def complete_asset_batch(self, batch_id: str, idempotency_key: str) -> None:
        self.events.append("complete")

    def get_asset_batch(self, batch_id: str) -> AssetBatchState:
        self.events.append("poll")
        allocation = next(
            allocation
            for allocation in self._allocations.values()
            if allocation.batch_id == batch_id
        )
        return AssetBatchState(
            batch_id=batch_id,
            statuses={
                slot.asset_id: ProviderAssetStatus.COMPLETED for slot in allocation.slots
            },
        )

    def get_assets(self, asset_ids: Sequence[str]) -> AssetBatchState:
        return AssetBatchState(
            batch_id="assets",
            statuses={asset_id: ProviderAssetStatus.COMPLETED for asset_id in asset_ids},
        )
