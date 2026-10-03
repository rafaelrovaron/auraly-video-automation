from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, SceneVariantRow
from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.heygen.domain import (
    AssetPreparationPlan,
    AssetPreparationSubmission,
    AssetSource,
    AssetUploadJobInput,
    ProviderAssetStatus,
    RemoteAssetKind,
    RemoteAssetStatus,
    asset_batch_idempotency_key,
)
from auraly_pipeline.heygen.handler import HeyGenAssetUploadHandler
from auraly_pipeline.heygen.provider import HeyGenMcpAdapter, HeyGenProvider
from auraly_pipeline.heygen.repository import (
    RemoteAssetPersistenceError,
    RemoteAssetRepository,
)
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.jobs.domain import JobSubmit, RetrySafety
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.jobs.state_machine import JobStatus
from auraly_pipeline.voices.repository import VoiceMasterRepository


class HeyGenServiceError(RuntimeError):
    pass


class HeyGenService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: HeyGenProvider,
        jobs: JobService,
        work_root: Path,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._jobs = jobs
        self._work_root = work_root.resolve()
        self._assets = RemoteAssetRepository(session_factory)

    @classmethod
    def for_database(
        cls,
        database_path: Path,
        work_root: Path,
        provider: HeyGenProvider | None = None,
    ) -> HeyGenService:
        migrate_database(database_path)
        factory = sessionmaker(
            bind=create_sqlite_engine(database_path), expire_on_commit=False, class_=Session
        )
        resolved_provider = provider or HeyGenMcpAdapter()
        handler = HeyGenAssetUploadHandler(factory, resolved_provider, work_root)
        jobs = JobService.for_database(
            database_path,
            handlers={"heygen.asset.upload": handler},
            work_root=work_root,
        )
        return cls(factory, resolved_provider, jobs, work_root)

    def close(self) -> None:
        self._jobs.close()
        bind = self._session_factory.kw.get("bind")
        if bind is not None:
            bind.dispose()

    def connect(self):
        return self._provider.connect()

    def disconnect(self) -> None:
        disconnect = getattr(self._provider, "disconnect", None)
        if callable(disconnect):
            disconnect()

    def connection_status(self) -> bool:
        storage = getattr(self._provider, "storage", None)
        return bool(storage and asyncio.run(storage.has_session()))

    def preflight(self):
        return self._provider.preflight()

    def _verified_path(self, local_path: str, sha256: str, size_bytes: int | None) -> Path:
        path = (self._work_root / local_path).resolve()
        if not path.is_relative_to(self._work_root) or not path.is_file():
            raise HeyGenServiceError("Approved local asset is unavailable")
        if size_bytes is not None and path.stat().st_size != size_bytes:
            raise HeyGenServiceError("Approved local asset changed")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise HeyGenServiceError("Approved local asset changed")
        return path

    def plan_assets(self, campaign_id: str) -> AssetPreparationPlan:
        preflight = self._provider.preflight()
        if not preflight.account_ref:
            raise HeyGenServiceError("HeyGen account is unavailable")
        with self._session_factory() as session:
            campaign = session.get(CampaignRow, campaign_id)
            if campaign is None:
                raise HeyGenServiceError("Campaign not found")
            scenes = list(
                session.scalars(
                    select(SceneVariantRow)
                    .where(SceneVariantRow.campaign_id == campaign_id)
                    .order_by(SceneVariantRow.variant_id, SceneVariantRow.id)
                )
            )
            voice = VoiceMasterRepository(session).approved_for_campaign(campaign_id)
            if voice is None or not voice.processed_audio_path or not voice.processed_sha256:
                raise HeyGenServiceError("Campaign requires an approved Voice Master")
            sources: list[AssetSource] = []
            for scene in scenes:
                image = ImageRepository.approved_candidate_for_scene_in_session(session, scene.id)
                if image is None:
                    raise HeyGenServiceError("Every scene requires one approved image")
                self._verified_path(image.source_path, image.sha256, image.size_bytes)
                image_format = "jpeg" if image.format.casefold() in {"jpg", "jpeg"} else image.format.casefold()
                sources.append(
                    AssetSource(
                        source_id=image.id,
                        kind=RemoteAssetKind.IMAGE,
                        local_path=image.source_path,
                        sha256=image.sha256,
                        mime_type=f"image/{image_format}",
                        size_bytes=image.size_bytes,
                    )
                )
            voice_path = self._verified_path(
                voice.processed_audio_path, voice.processed_sha256, None
            )
            sources.append(
                AssetSource(
                    source_id=voice.id,
                    kind=RemoteAssetKind.AUDIO,
                    local_path=voice.processed_audio_path,
                    sha256=voice.processed_sha256,
                    mime_type="audio/wav",
                    size_bytes=voice_path.stat().st_size,
                )
            )

        unique_sources: dict[tuple[RemoteAssetKind, str], AssetSource] = {}
        for source in sources:
            unique_sources.setdefault((source.kind, source.sha256), source)
        sources = list(unique_sources.values())

        reusable = [
            asset
            for asset in self._assets.find_by_keys(preflight.account_ref, sources)
            if asset.status is RemoteAssetStatus.READY
        ]
        ready = {(asset.kind, asset.sha256) for asset in reusable}
        uploads = [source for source in sources if (source.kind, source.sha256) not in ready]
        return AssetPreparationPlan(
            campaign_id=campaign_id,
            account_ref=preflight.account_ref,
            sources=sources,
            reused_assets=reusable,
            upload_sources=uploads,
        )

    def submit_assets(
        self, plan: AssetPreparationPlan, *,
        before_commit: Callable[[Session, JobRow], None] | None = None,
    ) -> AssetPreparationSubmission:
        current = self.plan_assets(plan.campaign_id)
        if current.account_ref != plan.account_ref or current.sources != plan.sources:
            raise HeyGenServiceError("Asset plan changed before submission")
        if not current.upload_sources:
            return AssetPreparationSubmission(plan=current, job=None, upload_count=0)
        key = asset_batch_idempotency_key(current.account_ref, current.upload_sources)
        job_input = AssetUploadJobInput(
            account_ref=current.account_ref,
            idempotency_key=key,
            sources=current.upload_sources,
        )
        request = JobSubmit(
            job_type="heygen.asset.upload",
            campaign_id=current.campaign_id,
            idempotency_key=key,
            input=job_input.model_dump(mode="json", by_alias=True),
            retry_safety=RetrySafety.RECONCILE_BEFORE_RETRY,
        )
        if before_commit is None:
            job = self._jobs.submit_job(request)
        else:
            def check(session: Session) -> None:
                row = session.scalar(select(JobRow).where(JobRow.idempotency_key == key))
                if row is None:
                    raise HeyGenServiceError("Asset job checkpoint missing")
                before_commit(session, row)

            job = self._jobs.submit_linked_batch(
                [request], lambda session, row: row.id, lambda job: job.job_id,
                before_commit=check,
            )[0].job
        return AssetPreparationSubmission(
            plan=current, job=job, upload_count=len(current.upload_sources)
        )

    def reconcile_upload(self, job_id: str):
        job = self._jobs.get_job(job_id)
        if job.job_type != "heygen.asset.upload" or job.status is not JobStatus.BLOCKED:
            raise HeyGenServiceError("Only a blocked HeyGen upload can be reconciled")
        request = AssetUploadJobInput.model_validate(job.input)
        preflight = self._provider.preflight()
        if preflight.account_ref != request.account_ref:
            raise HeyGenServiceError(
                "The connected HeyGen account does not match the upload job"
            )
        rows = self._assets.find_by_keys(request.account_ref, request.sources)
        if not rows:
            raise HeyGenServiceError("Unknown upload allocation requires manual reconciliation")
        batch_id = rows[0].remote_batch_id
        if not batch_id:
            raise HeyGenServiceError("Remote batch identity is unavailable")
        state = self._provider.get_asset_batch(batch_id)
        try:
            updated = self._assets.apply_batch_state(state, job.updated_at)
        except RemoteAssetPersistenceError as error:
            raise HeyGenServiceError("Remote batch status is incomplete") from error
        if all(status is ProviderAssetStatus.QUEUED for status in state.statuses.values()):
            return job
        if any(
            asset.status in {RemoteAssetStatus.PROCESSING, RemoteAssetStatus.RECONCILIATION_REQUIRED}
            for asset in updated
        ):
            return job
        return self._jobs.resume_reconciled_job(
            job_id, reason="remote_asset_batch_reconciled"
        )
