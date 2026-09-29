from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.heygen.db_models import HeyGenRenderRow
from auraly_pipeline.heygen.video_domain import (
    HeyGenRender,
    HeyGenRenderStatus,
    VideoPlanItem,
    VideoSource,
    material_config_sha256,
    video_logical_key,
)
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.metadata_security import validate_safe_error_message, validate_safe_identifier


def _domain(row: HeyGenRenderRow) -> HeyGenRender:
    return HeyGenRender(
        render_id=row.id,
        item=VideoPlanItem.model_validate(row.item_json),
        logical_key=row.logical_key,
        config_sha256=row.config_sha256,
        job_id=row.job_id,
        status=HeyGenRenderStatus(row.status),
        max_paid_renders=row.max_paid_renders,
        approved_by=row.approved_by,
        dispatch_started_at=row.dispatch_started_at,
        remote_video_id=row.remote_video_id,
        manual_binding=bool(row.manual_binding),
        source=None if row.source_json is None else VideoSource.model_validate(row.source_json),
        error_code=row.error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class HeyGenVideoRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, render_id: str) -> HeyGenRender:
        with self._session_factory() as session:
            row = session.get(HeyGenRenderRow, render_id)
            if row is None:
                raise ValueError("render not found")
            return _domain(row)

    def list_campaign(self, campaign_id: str) -> list[HeyGenRender]:
        with self._session_factory() as session:
            return [
                _domain(r)
                for r in session.scalars(
                    select(HeyGenRenderRow)
                    .where(HeyGenRenderRow.campaign_id == campaign_id)
                    .order_by(HeyGenRenderRow.created_at, HeyGenRenderRow.id)
                )
            ]

    def find(self, logical_key: str) -> HeyGenRender | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(HeyGenRenderRow).where(HeyGenRenderRow.logical_key == logical_key)
            )
            return None if row is None else _domain(row)

    def create_in_session(
        self,
        session: Session,
        job: JobRow,
        item: VideoPlanItem,
        *,
        max_paid_renders: int,
        approved_by: str,
    ) -> HeyGenRender:
        validate_safe_identifier(approved_by, "approved_by", max_length=200)
        now = datetime.now(UTC)
        row = HeyGenRenderRow(
            id=str(uuid4()),
            campaign_id=item.campaign_id,
            scene_variant_id=item.scene_variant_id,
            image_candidate_id=item.image_candidate_id,
            voice_master_id=item.voice_master_id,
            job_id=job.id,
            provider_account_ref=item.account_ref,
            logical_key=video_logical_key(item),
            config_sha256=material_config_sha256(item.config),
            item_json=item.model_dump(mode="json"),
            status="planned",
            max_paid_renders=max_paid_renders,
            approved_by=approved_by,
            manual_binding=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return _domain(row)

    def check_budget_in_session(
        self, session: Session, campaign_id: str, max_paid_renders: int
    ) -> None:
        count = (
            session.scalar(
                select(func.count())
                .select_from(HeyGenRenderRow)
                .where(HeyGenRenderRow.campaign_id == campaign_id)
            )
            or 0
        )
        if isinstance(max_paid_renders, bool) or max_paid_renders < 1 or count > max_paid_renders:
            raise ValueError("approved render budget exceeded")

    def _update(self, render_id: str, mutate: Callable[[HeyGenRenderRow], None]) -> HeyGenRender:
        try:
            with self._session_factory() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                row = session.get(HeyGenRenderRow, render_id)
                if row is None:
                    raise ValueError("render not found")
                mutate(row)
                row.updated_at = datetime.now(UTC)
                session.flush()
                result = _domain(row)
                session.commit()
                return result
        except IntegrityError:
            raise ValueError("render checkpoint conflict") from None

    def mark_dispatch(self, render_id: str) -> HeyGenRender:
        def mutate(row: HeyGenRenderRow) -> None:
            if (
                row.dispatch_started_at is not None
                or row.remote_video_id is not None
                or row.status != "planned"
            ):
                raise ValueError("dispatch already started")
            row.status = "submitting"
            row.dispatch_started_at = datetime.now(UTC)

        return self._update(render_id, mutate)

    def reset_no_dispatch(self, render_id: str) -> HeyGenRender:
        def mutate(row: HeyGenRenderRow) -> None:
            if row.remote_video_id is not None:
                raise ValueError("cannot reset known dispatch")
            row.dispatch_started_at = None
            row.status = "planned"
            row.error_code = None

        return self._update(render_id, mutate)

    def record_video(
        self, render_id: str, video_id: str, *, manual_binding: bool = False
    ) -> HeyGenRender:
        validate_safe_identifier(video_id, "video_id", max_length=200)

        def mutate(row: HeyGenRenderRow) -> None:
            if row.remote_video_id is not None and row.remote_video_id != video_id:
                raise ValueError("video ID cannot be replaced")
            row.remote_video_id = video_id
            row.manual_binding = int(manual_binding or bool(row.manual_binding))
            row.status = "processing"

        return self._update(render_id, mutate)

    def set_status(
        self,
        render_id: str,
        status: HeyGenRenderStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> HeyGenRender:
        if error_code is not None:
            validate_safe_identifier(error_code, "error_code", max_length=120)
        if error_message is not None:
            validate_safe_error_message(error_message)

        def mutate(row: HeyGenRenderRow) -> None:
            row.status = status.value
            row.error_code = error_code
            row.error_message = error_message

        return self._update(render_id, mutate)

    def record_source(self, render_id: str, source: VideoSource) -> HeyGenRender:
        def mutate(row: HeyGenRenderRow) -> None:
            row.source_json = source.model_dump(mode="json", exclude_computed_fields=True)
            row.status = "ready"
            row.error_code = None

        return self._update(render_id, mutate)
