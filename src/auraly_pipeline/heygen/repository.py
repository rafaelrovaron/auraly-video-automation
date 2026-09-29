from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.heygen.db_models import RemoteAssetRow
from auraly_pipeline.heygen.domain import (
    AssetBatchState,
    AssetSource,
    AssetUploadSlot,
    ProviderAssetStatus,
    RemoteAsset,
    RemoteAssetKind,
    RemoteAssetStatus,
)


Result = TypeVar("Result")


class RemoteAssetPersistenceError(RuntimeError):
    pass


def _to_domain(row: RemoteAssetRow) -> RemoteAsset:
    return RemoteAsset(
        remote_asset_id_local=row.id,
        provider="heygen",
        provider_account_ref=row.provider_account_ref,
        kind=RemoteAssetKind(row.kind),
        sha256=row.sha256,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        remote_asset_id=row.remote_asset_id,
        remote_batch_id=row.remote_batch_id,
        status=RemoteAssetStatus(row.status),
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class RemoteAssetRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _immediate(self, operation: Callable[[Session], Result]) -> Result:
        with self._session_factory() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                result = operation(session)
                session.commit()
                return result
            except BaseException:
                session.rollback()
                raise

    @staticmethod
    def _find_rows(
        session: Session, account_ref: str, sources: Sequence[AssetSource]
    ) -> list[RemoteAssetRow]:
        if not sources:
            return []
        wanted = {(source.kind.value, source.sha256) for source in sources}
        rows = session.scalars(
            select(RemoteAssetRow).where(
                RemoteAssetRow.provider == "heygen",
                RemoteAssetRow.provider_account_ref == account_ref,
            )
        ).all()
        by_key = {(row.kind, row.sha256): row for row in rows if (row.kind, row.sha256) in wanted}
        return [by_key[key] for source in sources if (key := (source.kind.value, source.sha256)) in by_key]

    def find_by_keys(
        self, account_ref: str, sources: Sequence[AssetSource]
    ) -> list[RemoteAsset]:
        with self._session_factory() as session:
            return [_to_domain(row) for row in self._find_rows(session, account_ref, sources)]

    def record_allocation(
        self,
        account_ref: str,
        batch_id: str,
        sources: Sequence[AssetSource],
        slots: Sequence[AssetUploadSlot],
        now: datetime,
    ) -> list[RemoteAsset]:
        if len(sources) != len(slots) or any(
            source.source_id != slot.source_id for source, slot in zip(sources, slots, strict=True)
        ):
            raise RemoteAssetPersistenceError("allocation response does not match submitted assets")

        def persist(session: Session) -> list[RemoteAsset]:
            existing = self._find_rows(session, account_ref, sources)
            if existing:
                if len(existing) != len(sources):
                    raise RemoteAssetPersistenceError("allocation replay conflicts with stored assets")
                for row, source, slot in zip(existing, sources, slots, strict=True):
                    if (
                        row.remote_batch_id != batch_id
                        or row.remote_asset_id != slot.asset_id
                        or row.mime_type != source.mime_type
                        or row.size_bytes != source.size_bytes
                    ):
                        raise RemoteAssetPersistenceError(
                            "allocation replay conflicts with stored assets"
                        )
                return [_to_domain(row) for row in existing]

            rows = [
                RemoteAssetRow(
                    id=str(uuid4()),
                    provider="heygen",
                    provider_account_ref=account_ref,
                    kind=source.kind.value,
                    sha256=source.sha256,
                    mime_type=source.mime_type,
                    size_bytes=source.size_bytes,
                    remote_asset_id=slot.asset_id,
                    remote_batch_id=batch_id,
                    status=RemoteAssetStatus.ALLOCATED.value,
                    created_at=now,
                    updated_at=now,
                )
                for source, slot in zip(sources, slots, strict=True)
            ]
            session.add_all(rows)
            try:
                session.flush()
            except IntegrityError as error:
                raise RemoteAssetPersistenceError("remote asset allocation conflicts") from error
            return [_to_domain(row) for row in rows]

        return self._immediate(persist)

    def list_by_batch(self, batch_id: str) -> list[RemoteAsset]:
        statement = (
            select(RemoteAssetRow)
            .where(RemoteAssetRow.remote_batch_id == batch_id)
            .order_by(RemoteAssetRow.created_at, RemoteAssetRow.id)
        )
        with self._session_factory() as session:
            return [_to_domain(row) for row in session.scalars(statement)]

    def apply_batch_state(self, batch_state: AssetBatchState, now: datetime) -> list[RemoteAsset]:
        def apply(session: Session) -> list[RemoteAsset]:
            rows = list(
                session.scalars(
                    select(RemoteAssetRow)
                    .where(RemoteAssetRow.remote_batch_id == batch_state.batch_id)
                    .order_by(RemoteAssetRow.created_at, RemoteAssetRow.id)
                )
            )
            for row in rows:
                provider_status = batch_state.statuses.get(row.remote_asset_id)
                if provider_status is None or row.status == RemoteAssetStatus.READY.value:
                    continue
                if provider_status is ProviderAssetStatus.COMPLETED:
                    row.status = RemoteAssetStatus.READY.value
                    row.last_error_code = None
                    row.last_error_message = None
                elif provider_status is ProviderAssetStatus.FAILED:
                    row.status = RemoteAssetStatus.FAILED.value
                    if error_code := batch_state.error_codes.get(row.remote_asset_id):
                        row.last_error_code = error_code
                    if error_message := batch_state.error_messages.get(row.remote_asset_id):
                        row.last_error_message = error_message
                elif provider_status is ProviderAssetStatus.NOT_FOUND:
                    row.status = RemoteAssetStatus.RECONCILIATION_REQUIRED.value
                else:
                    row.status = RemoteAssetStatus.PROCESSING.value
                row.updated_at = now
            session.flush()
            return [_to_domain(row) for row in rows]

        return self._immediate(apply)

    def mark_reconciliation_required(
        self, batch_id: str, code: str, message: str, now: datetime
    ) -> list[RemoteAsset]:
        def mark(session: Session) -> list[RemoteAsset]:
            rows = list(
                session.scalars(
                    select(RemoteAssetRow).where(RemoteAssetRow.remote_batch_id == batch_id)
                )
            )
            for row in rows:
                if row.status != RemoteAssetStatus.READY.value:
                    row.status = RemoteAssetStatus.RECONCILIATION_REQUIRED.value
                    row.last_error_code = code
                    row.last_error_message = message
                    row.updated_at = now
            session.flush()
            return [_to_domain(row) for row in rows]

        return self._immediate(mark)
