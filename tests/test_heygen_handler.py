from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.heygen.domain import (
    AssetBatchState,
    AssetSource,
    AssetUploadJobInput,
    AssetUploadSlot,
    ProviderAssetStatus,
    RemoteAssetKind,
    RemoteAssetStatus,
    asset_batch_idempotency_key,
)
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.handler import HeyGenAssetUploadHandler
from auraly_pipeline.heygen.provider import HeyGenProviderFailure
from auraly_pipeline.heygen.repository import RemoteAssetRepository
from auraly_pipeline.jobs.handlers import JobExecutionContext


NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def _source(
    work_root: Path,
    source_id: str,
    name: str,
    content: bytes,
    kind: RemoteAssetKind,
) -> AssetSource:
    path = work_root / "campaigns" / "one" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return AssetSource(
        source_id=source_id,
        kind=kind,
        local_path=path.relative_to(work_root).as_posix(),
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="audio/wav" if kind is RemoteAssetKind.AUDIO else "image/png",
        size_bytes=len(content),
    )


def _setup(tmp_path: Path) -> tuple[RemoteAssetRepository, sessionmaker[Session]]:
    database = tmp_path / "auraly.db"
    migrate_database(database)
    factory = sessionmaker(
        bind=create_sqlite_engine(database), expire_on_commit=False, class_=Session
    )
    return RemoteAssetRepository(factory), factory


def _context(sources: list[AssetSource]) -> JobExecutionContext:
    key = asset_batch_idempotency_key("account-fake", sources)
    payload = AssetUploadJobInput(
        account_ref="account-fake", idempotency_key=key, sources=sources
    )
    return JobExecutionContext(
        job_id="00000000-0000-4000-8000-000000000010",
        job_type="heygen.asset.upload",
        input=payload.model_dump(mode="json", by_alias=True),
        attempt_number=1,
        campaign_id="campaign-one",
    )


def test_handler_persists_remote_ids_before_first_put(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    sources = [
        _source(tmp_path, "00000000-0000-4000-8000-000000000001", "image.png", b"img", RemoteAssetKind.IMAGE),
        _source(tmp_path, "00000000-0000-4000-8000-000000000002", "voice.wav", b"wav", RemoteAssetKind.AUDIO),
    ]

    class CheckingProvider(FakeHeyGenProvider):
        checked = False

        def upload_file(self, slot: AssetUploadSlot, local_path: Path) -> None:
            if not self.checked:
                assert len(repository.list_by_batch("batch-1")) == 2
                self.events.append("first_persisted_check")
                self.checked = True
            super().upload_file(slot, local_path)

    provider = CheckingProvider()
    handler = HeyGenAssetUploadHandler(factory, provider, tmp_path, clock=lambda: NOW)

    result = handler.execute(_context(sources))

    assert result.outcome == "success"
    assert provider.events == [
        "preflight",
        "allocate",
        "first_persisted_check",
        f"put:{sources[0].source_id}",
        f"put:{sources[1].source_id}",
        "complete",
        "poll",
    ]
    assert {asset.status for asset in repository.list_by_batch("batch-1")} == {
        RemoteAssetStatus.READY
    }


def test_handler_rejects_changed_file_before_provider_mutation(tmp_path: Path) -> None:
    _repository, factory = _setup(tmp_path)
    source = _source(
        tmp_path, "00000000-0000-4000-8000-000000000001", "image.png", b"img", RemoteAssetKind.IMAGE
    )
    (tmp_path / source.local_path).write_bytes(b"changed")
    provider = FakeHeyGenProvider()

    result = HeyGenAssetUploadHandler(factory, provider, tmp_path, clock=lambda: NOW).execute(
        _context([source])
    )

    assert result.outcome == "terminal_failure"
    assert provider.events == ["preflight"]


def test_handler_blocks_account_mismatch_without_remote_mutation(tmp_path: Path) -> None:
    _repository, factory = _setup(tmp_path)
    source = _source(
        tmp_path, "00000000-0000-4000-8000-000000000001", "image.png", b"img", RemoteAssetKind.IMAGE
    )
    provider = FakeHeyGenProvider(account_ref="account-other")

    result = HeyGenAssetUploadHandler(factory, provider, tmp_path, clock=lambda: NOW).execute(
        _context([source])
    )

    assert result.outcome == "blocked"
    assert provider.events == ["preflight"]


def test_handler_blocks_incomplete_status_response(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    sources = [
        _source(tmp_path, "00000000-0000-4000-8000-000000000001", "one.png", b"one", RemoteAssetKind.IMAGE),
        _source(tmp_path, "00000000-0000-4000-8000-000000000002", "two.png", b"two", RemoteAssetKind.IMAGE),
    ]

    class PartialProvider(FakeHeyGenProvider):
        def get_asset_batch(self, batch_id: str) -> AssetBatchState:
            state = super().get_asset_batch(batch_id)
            asset_id = next(iter(state.statuses))
            return AssetBatchState(
                batch_id=batch_id,
                statuses={asset_id: ProviderAssetStatus.COMPLETED},
            )

    result = HeyGenAssetUploadHandler(
        factory, PartialProvider(), tmp_path, clock=lambda: NOW
    ).execute(_context(sources))

    assert result.outcome == "blocked"
    assert {asset.status for asset in repository.list_by_batch("batch-1")} == {
        RemoteAssetStatus.RECONCILIATION_REQUIRED
    }


def test_handler_blocks_ambiguous_allocation_without_rows(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    source = _source(
        tmp_path, "00000000-0000-4000-8000-000000000001", "image.png", b"img", RemoteAssetKind.IMAGE
    )

    result = HeyGenAssetUploadHandler(
        factory, FakeHeyGenProvider(scenario="ambiguous"), tmp_path, clock=lambda: NOW
    ).execute(_context([source]))

    assert result.outcome == "blocked"
    assert repository.find_by_keys("account-fake", [source]) == []


def test_handler_marks_ambiguous_put_for_reconciliation(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    source = _source(
        tmp_path, "00000000-0000-4000-8000-000000000001", "image.png", b"img", RemoteAssetKind.IMAGE
    )

    class AmbiguousPutProvider(FakeHeyGenProvider):
        def upload_file(self, slot: AssetUploadSlot, local_path: Path) -> None:
            raise HeyGenProviderFailure("ambiguous", "Fake PUT outcome is unknown")

    result = HeyGenAssetUploadHandler(
        factory, AmbiguousPutProvider(), tmp_path, clock=lambda: NOW
    ).execute(_context([source]))

    assert result.outcome == "blocked"
    assert repository.list_by_batch("batch-1")[0].status is (
        RemoteAssetStatus.RECONCILIATION_REQUIRED
    )


def test_handler_poll_timeout_preserves_processing_checkpoint(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    source = _source(
        tmp_path, "00000000-0000-4000-8000-000000000001", "image.png", b"img", RemoteAssetKind.IMAGE
    )

    class QueuedProvider(FakeHeyGenProvider):
        def get_asset_batch(self, batch_id: str) -> AssetBatchState:
            state = super().get_asset_batch(batch_id)
            return AssetBatchState(
                batch_id=batch_id,
                statuses={asset_id: ProviderAssetStatus.QUEUED for asset_id in state.statuses},
            )

    result = HeyGenAssetUploadHandler(
        factory,
        QueuedProvider(),
        tmp_path,
        clock=lambda: NOW,
        poll_timeout_seconds=0,
    ).execute(_context([source]))

    assert result.outcome == "blocked"
    assert repository.list_by_batch("batch-1")[0].status is RemoteAssetStatus.PROCESSING


def test_handler_persists_partial_failure_as_terminal(tmp_path: Path) -> None:
    repository, factory = _setup(tmp_path)
    sources = [
        _source(tmp_path, "00000000-0000-4000-8000-000000000001", "one.png", b"one", RemoteAssetKind.IMAGE),
        _source(tmp_path, "00000000-0000-4000-8000-000000000002", "two.png", b"two", RemoteAssetKind.IMAGE),
    ]

    class PartialFailureProvider(FakeHeyGenProvider):
        def get_asset_batch(self, batch_id: str) -> AssetBatchState:
            state = super().get_asset_batch(batch_id)
            first, second = state.statuses
            return AssetBatchState(
                batch_id=batch_id,
                statuses={
                    first: ProviderAssetStatus.COMPLETED,
                    second: ProviderAssetStatus.FAILED,
                },
                error_codes={second: "processing_failed"},
                error_messages={second: "Asset processing failed."},
            )

    result = HeyGenAssetUploadHandler(
        factory, PartialFailureProvider(), tmp_path, clock=lambda: NOW
    ).execute(_context(sources))

    assert result.outcome == "terminal_failure"
    assert {asset.status for asset in repository.list_by_batch("batch-1")} == {
        RemoteAssetStatus.READY,
        RemoteAssetStatus.FAILED,
    }
