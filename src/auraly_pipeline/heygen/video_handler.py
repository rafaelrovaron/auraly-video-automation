from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CopyMasterRow, SceneVariantRow
from auraly_pipeline.heygen.db_models import RemoteAssetRow
from auraly_pipeline.heygen.domain import ProviderAssetStatus
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.provider import HeyGenMcpAdapter, HeyGenProviderFailure
from auraly_pipeline.heygen.video_domain import HeyGenRenderStatus, VideoPlanItem
from auraly_pipeline.heygen.video_media import download_source, recover_source
from auraly_pipeline.heygen.video_repository import HeyGenVideoRepository
from auraly_pipeline.images.db_models import ImageCandidateRow
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext
from auraly_pipeline.voices.audio import probe_wav_duration
from auraly_pipeline.voices.db_models import VoiceMasterRow
from auraly_pipeline.voices.domain import validate_workspace_path


def verified_video_path(root: Path, relative: str, sha: str) -> Path:
    validate_workspace_path(relative)
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("approved video input unavailable")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != sha:
            raise ValueError("approved video input changed")
    return path


def validate_video_item(
    factory: sessionmaker[Session],
    root: Path,
    item: VideoPlanItem,
    *,
    session: Session | None = None,
) -> None:
    with factory() if session is None else nullcontext(session) as session:
        scene = session.get(SceneVariantRow, item.scene_variant_id)
        image = session.get(ImageCandidateRow, item.image_candidate_id)
        voice = session.get(VoiceMasterRow, item.voice_master_id)
        if (
            scene is None
            or scene.campaign_id != item.campaign_id
            or image is None
            or image.scene_variant_id != scene.id
            or image.review_status != "approved"
            or image.sha256 != item.image_sha256
            or voice is None
            or voice.campaign_id != item.campaign_id
            or voice.status != "approved"
            or voice.processed_sha256 != item.audio_sha256
            or not voice.processed_audio_path
        ):
            raise ValueError("video requires approved matching inputs")
        copy = session.get(CopyMasterRow, voice.copy_master_id)
        if (
            copy is None
            or copy.approval_state != "approved"
            or copy.version != voice.copy_master_version
        ):
            raise ValueError("video requires approved matching copy")
        verified_video_path(root, image.source_path, item.image_sha256)
        audio_path = verified_video_path(root, voice.processed_audio_path, item.audio_sha256)
        duration = probe_wav_duration(audio_path)
        if duration <= 0 or abs(duration - item.duration_seconds) > 0.05:
            raise ValueError("processed WAV duration changed")
        for kind, sha, asset_id in [
            ("image", item.image_sha256, item.image_asset_id),
            ("audio", item.audio_sha256, item.audio_asset_id),
        ]:
            asset = session.scalar(
                select(RemoteAssetRow).where(
                    RemoteAssetRow.provider_account_ref == item.account_ref,
                    RemoteAssetRow.kind == kind,
                    RemoteAssetRow.sha256 == sha,
                    RemoteAssetRow.remote_asset_id == asset_id,
                    RemoteAssetRow.status == "ready",
                )
            )
            if asset is None:
                raise ValueError("video requires matching ready remote assets")


class HeyGenVideoHandler:
    retry_safety = RetrySafety.RECONCILE_BEFORE_RETRY

    def __init__(
        self,
        factory: sessionmaker[Session],
        provider: HeyGenMcpAdapter | FakeHeyGenProvider,
        work_root: Path,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        client_factory: Callable[[], httpx.Client] = httpx.Client,
    ) -> None:
        self._factory = factory
        self._provider = provider
        self._root = work_root
        self._repo = HeyGenVideoRepository(factory)
        self._sleep = sleep
        self._monotonic = monotonic
        self._client_factory = client_factory

    def _owns_lease(self, context: JobExecutionContext) -> bool:
        with self._factory() as session:
            job = session.get(JobRow, context.job_id)
            return bool(
                job
                and job.status == "running"
                and job.attempt_count == context.attempt_number
                and job.lease_expires_at is not None
                and job.lease_expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
            )

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        render = None
        try:
            if "render_id" in context.input:
                render = self._repo.get(str(context.input["render_id"]))
            else:
                render = self._repo.find(str(context.input.get("logical_key", "")))
            if (
                render is None
                or render.job_id != context.job_id
                or render.item.campaign_id != context.campaign_id
            ):
                raise ValueError("video job identity mismatch")
            if render.dispatch_started_at is not None and render.remote_video_id is None:
                raise ValueError("ambiguous video dispatch requires reconciliation")
            preflight = self._provider.preflight_video(render.item.config)
            if preflight.account_ref != render.item.account_ref:
                raise ValueError("video account changed")
            validate_video_item(self._factory, self._root, render.item)
            assets = self._provider.get_assets(
                [render.item.image_asset_id, render.item.audio_asset_id]
            )
            if any(
                assets.statuses.get(a) != ProviderAssetStatus.COMPLETED
                for a in (render.item.image_asset_id, render.item.audio_asset_id)
            ):
                raise ValueError("remote assets no longer ready")
            if not self._owns_lease(context):
                raise ValueError("video lease lost")
            if render.status == HeyGenRenderStatus.READY:
                source = recover_source(render, work_root=self._root)
                if source is None:
                    raise ValueError("ready video source missing")
                return JobExecutionResult(
                    outcome=JobExecutionOutcome.SUCCESS, result={"render_id": render.render_id}
                )
            if render.remote_video_id is None:
                if preflight.schema_fingerprint != render.item.schema_fingerprint:
                    raise ValueError("video schema changed before dispatch")
                render = self._repo.mark_dispatch(render.render_id)
                try:
                    video_id = self._provider.create_video(
                        render.item, callback_id=render.logical_key
                    )
                except HeyGenProviderFailure as error:
                    if not error.request_dispatched:
                        self._repo.reset_no_dispatch(render.render_id)
                    raise
                render = self._repo.record_video(render.render_id, video_id)
            assert render.remote_video_id is not None
            deadline = self._monotonic() + render.item.config.poll_timeout_seconds
            delay = float(render.item.config.poll_initial_seconds)
            while self._monotonic() < deadline:
                if not self._owns_lease(context):
                    raise ValueError("video lease lost")
                try:
                    state = self._provider.get_video(render.remote_video_id)
                except HeyGenProviderFailure as error:
                    if error.kind not in {"retryable", "ambiguous"}:
                        raise
                    state = None
                if state is not None:
                    if state.video_id != render.remote_video_id:
                        raise ValueError("video ID mismatch")
                    if state.status == ProviderAssetStatus.FAILED:
                        self._repo.set_status(
                            render.render_id,
                            HeyGenRenderStatus.FAILED,
                            error_code="heygen_video_failed",
                        )
                        return JobExecutionResult(
                            outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                            error_code="heygen_video_failed",
                            error_message="HeyGen video generation failed.",
                        )
                    if state.status == ProviderAssetStatus.COMPLETED:
                        self._repo.set_status(render.render_id, HeyGenRenderStatus.DOWNLOAD_PENDING)
                        source = recover_source(render, work_root=self._root)
                        if source is None:
                            if not state.download_url:
                                raise ValueError("video URL missing")
                            with self._client_factory() as client:
                                source = download_source(
                                    render, state.download_url, work_root=self._root, client=client
                                )
                        if not self._owns_lease(context):
                            raise ValueError("video lease lost")
                        self._repo.record_source(render.render_id, source)
                        return JobExecutionResult(
                            outcome=JobExecutionOutcome.SUCCESS,
                            result={"render_id": render.render_id},
                        )
                    if state.status not in {
                        ProviderAssetStatus.PROCESSING,
                        ProviderAssetStatus.QUEUED,
                    }:
                        raise ValueError("unknown video status")
                self._sleep(min(delay, max(0, deadline - self._monotonic())))
                delay = min(delay * 2, render.item.config.poll_max_seconds)
            raise ValueError("video polling deadline exceeded")
        except Exception:
            if render is not None:
                self._repo.set_status(
                    render.render_id,
                    HeyGenRenderStatus.RECONCILIATION_REQUIRED,
                    error_code="heygen_video_blocked",
                )
            return JobExecutionResult(
                outcome=JobExecutionOutcome.BLOCKED,
                error_code="heygen_video_blocked",
                error_message="HeyGen video requires reconciliation; no automatic paid retry.",
            )
