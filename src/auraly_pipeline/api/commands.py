from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.api.action_contracts import (
    EditPlanOperation, EditPlanResult, ImageImportItemResult, ImageImportOperation,
    ImageImportOperationResult, ImagePrepareOperation, ImagePrepareResult, ImageManifestOperation, ImageManifestResult,
    LocalOperationRequest, OperationResult, OperationSubmission, OperationView,
    VoiceImportOperation, VoiceImportResult, VoiceReviewOperation, VoiceReviewResult,
    HeyGenAssetsOperation, HeyGenAssetsResult, HeyGenVideoPlanOperation, HeyGenVideoPlanResult,
    HeyGenVideoSubmitOperation, HeyGenVideoSubmitResult, HeyGenReconcileOperation, HeyGenReconcileResult,
    ImageReviewAction, ImageImportDiagnostic,
)
from auraly_pipeline.api.contracts import (
    ApiSettings, ERROR_MESSAGES, ErrorCode, QueryError, RenderSummary,
    CampaignDetail, ImageSummary, ProfileView, JobSummary,
)
from auraly_pipeline.api.queries import ApiQueries
from auraly_pipeline.api.operations import ApiOperationHandler, LOCAL_OPERATION_JOB
from auraly_pipeline.campaigns.service import CampaignNotFoundError, CampaignService, CampaignError
from auraly_pipeline.campaigns.domain import CampaignCreate, CopyMasterCreate
from auraly_pipeline.editing.batch_planner import verify_batch_plan
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditingError, EditingArtifactNotFoundError, EditProfile, relative_path
from auraly_pipeline.editing.resolver import profile_hash
from auraly_pipeline.editing.service import validate_editing_path, EditingService
from auraly_pipeline.heygen.provider import HeyGenMcpAdapter
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.handler import HeyGenAssetUploadHandler
from auraly_pipeline.heygen.service import HeyGenService, HeyGenServiceError
from auraly_pipeline.heygen.video_domain import HeyGenRender
from auraly_pipeline.heygen.video_handler import HeyGenVideoHandler
from auraly_pipeline.heygen.video_repository import HeyGenVideoRepository
from auraly_pipeline.heygen.video_service import HeyGenVideoService
from auraly_pipeline.images.import_batch import ImageImportBatch, ImageImportError, ImageImportService, ImageImportValidationError
from auraly_pipeline.images.service import ImageService, ImageError, ImageCandidateNotFoundError
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.domain import JobSubmit, RetrySafety
from auraly_pipeline.jobs.repository import JobRepository
from auraly_pipeline.jobs.service import JobNotFoundError, JobService, JobTransitionError
from auraly_pipeline.voices.domain import VoiceGenerateRequest, VoiceMaster
from auraly_pipeline.voices.handler import SpeechProvider, TranscriptProvider, VoiceGenerateHandler
from auraly_pipeline.voices.import_audio import VoiceImportError, VoiceImportHandler, VoiceImportService
from auraly_pipeline.voices.service import VoiceMasterError, VoiceMasterNotFoundError, VoiceMasterService


class ApiCommands:
    def __init__(
        self, settings: ApiSettings, engine: Engine, *,
        speech_provider: SpeechProvider | None = None,
        transcriber: TranscriptProvider | None = None,
        heygen_provider: HeyGenMcpAdapter | FakeHeyGenProvider | None = None,
    ) -> None:
        self.settings = settings
        self._speech_provider = speech_provider
        self._transcriber = transcriber
        self._heygen_provider = heygen_provider
        self._engine = engine
        self._heygen_jobs: JobService | None = None
        self._heygen_assets: HeyGenService | None = None
        self._heygen_videos: HeyGenVideoService | None = None
        self._sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
        self.campaigns = CampaignService(engine)
        self.images = ImageImportService.from_engine(engine, work_root=settings.work_root)
        self.editing = EditBatchService(
            project_root=settings.project_root, work_root=settings.work_root, database_path=settings.database,
        )
        self.jobs = JobService(
            engine, JobRepository(self._sessions),
            handlers={
                LOCAL_OPERATION_JOB: ApiOperationHandler(self),
                "voice.generate": VoiceGenerateHandler(
                    self._sessions, work_root=settings.work_root,
                    provider=speech_provider, transcriber=transcriber,
                ),
                "voice.import": VoiceImportHandler(
                    self._sessions, work_root=settings.work_root, transcriber=transcriber,
                ),
            },
        )
        self.voices = VoiceMasterService(
            engine, work_root=settings.work_root, provider=speech_provider, transcriber=transcriber,
        )
        self.voice_import = VoiceImportService(
            self._sessions, self.jobs, settings.project_root, settings.work_root,
        )
        self.queries = ApiQueries(settings, engine)
        self.image_review = ImageService(engine, self.jobs, work_root=settings.work_root)
        self.profiles = EditingService(project_root=settings.project_root, work_root=settings.work_root)

    def create_campaign(self, request: CampaignCreate) -> CampaignDetail:
        try:
            campaign = self.campaigns.create_campaign(request)
        except CampaignError:
            raise QueryError("operation_not_allowed") from None
        return self.queries.get_campaign(campaign.campaign_id)

    def add_copy(self, campaign_id: str, request: CopyMasterCreate) -> CampaignDetail:
        self.require_campaign(campaign_id)
        self.campaigns.add_copy_master_version(campaign_id, request)
        return self.queries.get_campaign(campaign_id)

    def review_image(self, campaign_id: str, candidate_id: str, request: ImageReviewAction) -> ImageSummary:
        self.require_campaign(campaign_id)
        try:
            candidate = self.image_review.get_candidate(candidate_id)
        except ImageCandidateNotFoundError:
            raise QueryError("not_found") from None
        scene_ids = {scene.scene_variant_id for scene in self.campaigns.get_campaign(campaign_id).scene_variants}
        if candidate.scene_variant_id not in scene_ids:
            raise QueryError("not_found")
        try:
            if request.action == "approve":
                reviewed = self.image_review.approve_candidate(candidate_id, request.actor)
            elif request.action == "reject":
                assert request.reason is not None
                reviewed = self.image_review.reject_candidate(candidate_id, request.actor, request.reason)
            else:
                reviewed = self.image_review.replace_approved_candidate(
                    candidate.scene_variant_id, candidate_id, request.actor,
                )
        except ImageError:
            raise QueryError("operation_not_allowed") from None
        return ImageSummary.model_validate(reviewed.model_dump(include=set(ImageSummary.model_fields)))

    def publish_profile(self, profile: EditProfile, *, base_version: int | None = None) -> ProfileView:
        if base_version is not None and profile.version != base_version + 1:
            raise QueryError("invalid_request")
        try:
            if base_version is None:
                self.profiles.create_profile(profile, validate_assets=False)
            else:
                self.profiles.create_profile_version(
                    profile.profile_id, base_version, profile, validate_assets=False,
                )
        except EditingArtifactNotFoundError:
            raise QueryError("not_found") from None
        except EditingError:
            raise QueryError("artifact_invalid") from None
        return ProfileView(profile=profile, profile_hash=profile_hash(profile))

    def change_job(self, campaign_id: str, job_id: str, *, resume: bool) -> JobSummary:
        self.queries.get_job(campaign_id, job_id)
        try:
            current = self.jobs.get_job(job_id)
            if (resume and current.job_type == LOCAL_OPERATION_JOB
                    and current.status in {"failed", "blocked"} and current.output):
                operation: LocalOperationRequest = TypeAdapter(LocalOperationRequest).validate_python(current.input)
                if not isinstance(operation, (VoiceImportOperation, HeyGenAssetsOperation,
                                              HeyGenVideoSubmitOperation, HeyGenReconcileOperation)):
                    raise QueryError("operation_not_allowed")
                result = self.execute_operation(operation, job_id=job_id)
                if result.model_dump(mode="json", by_alias=True) != current.output:
                    raise QueryError("artifact_invalid")
                job = self.jobs.complete_checkpointed_job(current)
            else:
                job = self.jobs.resume_job(job_id) if resume else self.jobs.cancel_job(job_id)
        except JobTransitionError:
            raise QueryError("operation_not_allowed") from None
        return JobSummary.model_validate(job.model_dump(include=set(JobSummary.model_fields)))

    @property
    def heygen_jobs(self) -> JobService:
        if self._heygen_jobs is None:
            provider = self._heygen_provider or HeyGenMcpAdapter()
            self._heygen_jobs = JobService(
                self._engine, JobRepository(self._sessions), handlers={
                    "heygen.asset.upload": HeyGenAssetUploadHandler(
                        self._sessions, provider, self.settings.work_root,
                    ),
                    "heygen.video.generate": HeyGenVideoHandler(
                        self._sessions, provider, self.settings.work_root,
                    ),
                },
            )
            self._heygen_assets = HeyGenService(
                self._sessions, provider, self._heygen_jobs, self.settings.work_root,
            )
            self._heygen_videos = HeyGenVideoService(
                self._sessions, provider, self._heygen_jobs, self.settings.work_root,
            )
        return self._heygen_jobs

    @property
    def heygen_assets(self) -> HeyGenService:
        self.heygen_jobs
        assert self._heygen_assets is not None
        return self._heygen_assets

    @property
    def heygen_videos(self) -> HeyGenVideoService:
        self.heygen_jobs
        assert self._heygen_videos is not None
        return self._heygen_videos

    def require_render(self, campaign_id: str, render_id: str) -> HeyGenRender:
        try:
            render = HeyGenVideoRepository(self._sessions).get(render_id)
        except ValueError:
            raise QueryError("not_found") from None
        if render.item.campaign_id != campaign_id:
            raise QueryError("not_found")
        return render

    @staticmethod
    def render_summary(render: HeyGenRender) -> RenderSummary:
        return RenderSummary(
            render_id=render.render_id, campaign_id=render.item.campaign_id,
            scene_variant_id=render.item.scene_variant_id,
            image_candidate_id=render.item.image_candidate_id,
            voice_master_id=render.item.voice_master_id, job_id=render.job_id,
            status=render.status, remote_video_id=render.remote_video_id,
            manual_binding=render.manual_binding, image_sha256=render.item.image_sha256,
            audio_sha256=render.item.audio_sha256, source=render.source, error_code=render.error_code,
            created_at=render.created_at, updated_at=render.updated_at,
        )

    def _checkpoint_operation(
        self, session: Session, job_id: str, request: LocalOperationRequest, result: OperationResult,
    ) -> None:
        row = session.get(JobRow, job_id)
        if (row is None or row.status != "running" or row.attempt_count != 1
                or row.campaign_id != request.campaign_id or row.job_type != LOCAL_OPERATION_JOB
                or row.input_json != request.model_dump(mode="json", by_alias=True)
                or row.lease_expires_at is None
                or row.lease_expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)):
            raise QueryError("operation_not_allowed")
        row.output_json = result.model_dump(mode="json", by_alias=True)

    def _validate_heygen_result(self, campaign_id: str, result: OperationResult) -> None:
        if isinstance(result, HeyGenAssetsResult) and result.job_id is not None:
            child = self.jobs.get_job(result.job_id)
            if child.campaign_id != campaign_id or child.job_type != "heygen.asset.upload":
                raise QueryError("artifact_invalid")
        renders = (result.renders if isinstance(result, HeyGenVideoSubmitResult)
                   else [result.render] if isinstance(result, HeyGenReconcileResult) else [])
        for summary in renders:
            render = self.require_render(campaign_id, summary.render_id)
            child = self.jobs.get_job(summary.job_id)
            if (summary.campaign_id != campaign_id or child.campaign_id != campaign_id
                    or child.job_type != "heygen.video.generate"
                    or child.input != {"logical_key": render.logical_key}
                    or summary.scene_variant_id != render.item.scene_variant_id
                    or summary.image_candidate_id != render.item.image_candidate_id
                    or summary.voice_master_id != render.item.voice_master_id
                    or summary.image_sha256 != render.item.image_sha256
                    or summary.audio_sha256 != render.item.audio_sha256):
                raise QueryError("artifact_invalid")

    def submit_voice(self, request: VoiceGenerateRequest) -> OperationSubmission:
        self.require_campaign(request.campaign_id)
        try:
            submission = self.voices.generate(request)
        except VoiceMasterError:
            raise QueryError("operation_not_allowed") from None
        return OperationSubmission(
            operation="voice_generate", campaign_id=request.campaign_id,
            job_id=submission.job.job_id, voice_master_id=submission.voice_master.voice_master_id,
        )

    def require_voice(self, campaign_id: str, voice_id: str) -> VoiceMaster:
        try:
            voice = self.voices.get(voice_id)
        except VoiceMasterNotFoundError:
            raise QueryError("not_found") from None
        if voice.campaign_id != campaign_id:
            raise QueryError("not_found")
        return voice

    def require_campaign(self, campaign_id: str) -> None:
        try:
            self.campaigns.get_campaign(campaign_id)
        except CampaignNotFoundError:
            raise QueryError("not_found", "campaignId") from None

    def _path(self, value: str, *, root: Path | None = None) -> Path:
        root = root or self.settings.project_root
        try:
            path = Path(value)
            if not path.is_absolute():
                path = root / relative_path(value)
            relative_path(path.relative_to(root).as_posix())
            return validate_editing_path(root, path)
        except (OSError, ValueError):
            raise QueryError("invalid_request") from None

    def _manifest(self, value: str, campaign_id: str) -> tuple[Path, str]:
        path = self._path(value)
        try:
            data = path.read_bytes()
            batch = ImageImportBatch.model_validate_json(data)
        except (OSError, ValueError):
            raise QueryError("invalid_request") from None
        if batch.campaign_id != campaign_id:
            raise QueryError("invalid_request")
        return path, hashlib.sha256(data).hexdigest()

    def submit_operation(self, request: LocalOperationRequest) -> OperationSubmission:
        request = TypeAdapter(LocalOperationRequest).validate_python(
            request.model_dump(mode="json", by_alias=True),
        )
        self.require_campaign(request.campaign_id)
        if isinstance(request, ImagePrepareOperation):
            output = self._path(request.output_path, root=self.settings.work_root)
            request = request.model_copy(update={
                "output_path": output.relative_to(self.settings.work_root).as_posix(),
            })
        elif isinstance(request, ImageManifestOperation):
            directory = self._path(request.directory_path)
            try:
                directory.relative_to(self.settings.work_root)
            except ValueError:
                raise QueryError("invalid_request") from None
            request = request.model_copy(update={
                "directory_path": directory.relative_to(self.settings.project_root).as_posix(),
                "items": sorted(request.items, key=lambda item: item.variant_id),
            })
        elif isinstance(request, ImageImportOperation):
            path, digest = self._manifest(request.manifest_path, request.campaign_id)
            if request.manifest_sha256 is not None and request.manifest_sha256 != digest:
                raise QueryError("artifact_invalid")
            request = request.model_copy(update={
                "manifest_path": path.relative_to(self.settings.project_root).as_posix(),
                "manifest_sha256": digest,
            })
        elif isinstance(request, (EditPlanOperation, VoiceImportOperation)) and request.request.campaign_id != request.campaign_id:
            raise QueryError("invalid_request")
        if isinstance(request, VoiceImportOperation):
            path = self._path(request.source_path)
            request = request.model_copy(update={
                "source_path": path.relative_to(self.settings.project_root).as_posix(),
            })
        elif isinstance(request, VoiceReviewOperation):
            self.require_voice(request.campaign_id, request.voice_id)
        elif isinstance(request, HeyGenReconcileOperation):
            self.require_render(request.campaign_id, request.render_id)
        payload = cast(dict[str, JsonValue], request.model_dump(mode="json", by_alias=True))
        if isinstance(request, ImageImportOperation):
            # Preserve identities of legacy persisted requests, before these fields existed.
            for key, default in (("includeDiagnostics", False), ("validationId", None), ("expectedSources", None)):
                if payload[key] == default:
                    payload.pop(key)
        identity = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        job = self.jobs.submit_job(JobSubmit(
            campaign_id=request.campaign_id, job_type=LOCAL_OPERATION_JOB,
            idempotency_key=f"{LOCAL_OPERATION_JOB}:{identity}", input=payload,
            max_attempts=1, retry_safety=RetrySafety.MANUAL_ONLY,
        ))
        return OperationSubmission(
            job_id=job.job_id, campaign_id=request.campaign_id, operation=request.operation,
        )

    def get_operation(self, campaign_id: str, job_id: str) -> OperationView:
        self.require_campaign(campaign_id)
        try:
            job = self.jobs.get_job(job_id)
        except JobNotFoundError:
            raise QueryError("not_found", "jobId") from None
        if job.campaign_id != campaign_id or job.job_type != LOCAL_OPERATION_JOB:
            raise QueryError("not_found", "jobId")
        try:
            request: LocalOperationRequest = TypeAdapter(LocalOperationRequest).validate_python(job.input)
            if request.campaign_id != campaign_id:
                raise ValueError("invalid operation identity")
            result: OperationResult | None = None
            if job.status == "completed":
                result = TypeAdapter(OperationResult).validate_python(job.output)
                if result.operation != request.operation:
                    raise ValueError("invalid operation result")
                self._validate_heygen_result(campaign_id, result)
                if isinstance(result, EditPlanResult):
                    verify_batch_plan(result.plan)
                    if (not isinstance(request, EditPlanOperation)
                            or result.plan.campaign_id != campaign_id
                            or result.plan.render_id != request.request.render_id
                            or result.plan.video_id != request.request.video_id
                            or result.persist != request.persist):
                        raise ValueError("invalid editorial result identity")
                    if result.persist:
                        self.editing.get_plan(
                            campaign_id, result.plan.video_id, result.plan.plan_hash,
                        )
            code = None if job.last_error_code is None else cast(
                ErrorCode, job.last_error_code if job.last_error_code in ERROR_MESSAGES
                else "internal_error",
            )
            return OperationView(
                job_id=job.job_id, campaign_id=campaign_id, operation=request.operation,
                status=job.status, result=result, error_code=code,
            )
        except (ValueError, OSError):
            raise QueryError("artifact_invalid") from None

    def execute_operation(
        self, request: LocalOperationRequest, *, job_id: str | None = None,
    ) -> OperationResult:
        self.require_campaign(request.campaign_id)
        try:
            if isinstance(request, (HeyGenAssetsOperation, HeyGenVideoPlanOperation,
                                    HeyGenVideoSubmitOperation, HeyGenReconcileOperation)):
                if job_id is None:
                    raise QueryError("invalid_request")
                wrapper = self.jobs.get_job(job_id)
                if (wrapper.campaign_id != request.campaign_id or wrapper.job_type != LOCAL_OPERATION_JOB
                        or wrapper.input != request.model_dump(mode="json", by_alias=True)):
                    raise QueryError("invalid_request")
                if wrapper.output:
                    saved: OperationResult = TypeAdapter(OperationResult).validate_python(wrapper.output)
                    if saved.operation != request.operation:
                        raise QueryError("artifact_invalid")
                    self._validate_heygen_result(request.campaign_id, saved)
                    return saved
                if isinstance(request, HeyGenAssetsOperation):
                    assets = self.heygen_assets
                    asset_plan = assets.plan_assets(request.campaign_id)
                    asset_result = HeyGenAssetsResult(
                        upload_count=len(asset_plan.upload_sources), reused_count=len(asset_plan.reused_assets), job_id=None,
                    )

                    def asset_checkpoint(session: Session, child: JobRow) -> None:
                        self._checkpoint_operation(
                            session, job_id, request, asset_result.model_copy(update={"job_id": child.id}),
                        )

                    asset_submission = assets.submit_assets(asset_plan, before_commit=asset_checkpoint)
                    return HeyGenAssetsResult(
                        upload_count=asset_submission.upload_count,
                        reused_count=len(asset_submission.plan.reused_assets),
                        job_id=None if asset_submission.job is None else asset_submission.job.job_id,
                    )
                if isinstance(request, HeyGenVideoSubmitOperation):
                    def video_checkpoint(session: Session, renders: list[HeyGenRender]) -> None:
                        self._checkpoint_operation(session, job_id, request, HeyGenVideoSubmitResult(
                            renders=[self.render_summary(render) for render in renders],
                        ))

                    renders = self.heygen_videos.submit_videos(
                        request.campaign_id, request.config, max_paid_renders=request.max_paid_renders,
                        approved_by=request.approved_by, before_commit=video_checkpoint,
                    )
                    return HeyGenVideoSubmitResult(renders=[self.render_summary(r) for r in renders])
                if isinstance(request, HeyGenVideoPlanOperation):
                    plan_videos = self.heygen_videos.plan_videos(
                        request.campaign_id, request.config, max_paid_renders=request.max_paid_renders,
                    )
                    return HeyGenVideoPlanResult(
                        new_count=plan_videos.new_count, reused_count=plan_videos.reused_count,
                        reserved_count=plan_videos.reserved_count, max_paid_renders=plan_videos.max_paid_renders,
                        total_audio_seconds=plan_videos.total_audio_seconds,
                        scene_variant_ids=[item.scene_variant_id for item in plan_videos.items],
                    )
                self.require_render(request.campaign_id, request.render_id)
                def reconcile_checkpoint(session: Session, reconciled: HeyGenRender) -> None:
                    self._checkpoint_operation(session, job_id, request, HeyGenReconcileResult(
                        render=self.render_summary(reconciled),
                    ))

                render = self.heygen_videos.reconcile_video(
                    request.render_id, video_id=request.video_id,
                    confirm_manual_binding=request.confirm_manual_binding,
                    before_commit=reconcile_checkpoint,
                )
                return HeyGenReconcileResult(render=self.render_summary(render))
            if isinstance(request, VoiceImportOperation):
                if job_id is None:
                    raise QueryError("invalid_request")
                if request.request.campaign_id != request.campaign_id:
                    raise QueryError("invalid_request")
                wrapper = self.jobs.get_job(job_id)
                if (wrapper.job_type != LOCAL_OPERATION_JOB
                        or wrapper.campaign_id != request.campaign_id
                        or wrapper.input != request.model_dump(mode="json", by_alias=True)):
                    raise QueryError("artifact_invalid")
                if wrapper.output:
                    result = VoiceImportResult.model_validate(wrapper.output)
                    voice = self.require_voice(request.campaign_id, result.voice_master_id)
                    child = self.jobs.get_job(result.job_id)
                    if (child.job_type != "voice.import"
                            or child.campaign_id != request.campaign_id
                            or child.input != {"voiceMasterId": voice.voice_master_id}):
                        raise QueryError("artifact_invalid")
                    return result

                def checkpoint(session: Session, child: JobRow) -> None:
                    row = session.get(JobRow, job_id)
                    if (row is None or row.status != "running" or row.attempt_count != 1
                            or row.campaign_id != request.campaign_id
                            or row.job_type != LOCAL_OPERATION_JOB
                            or row.input_json != wrapper.input
                            or row.lease_expires_at is None
                            or row.lease_expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)):
                        raise QueryError("operation_not_allowed")
                    result = VoiceImportResult(
                        voice_master_id=str(child.input_json["voiceMasterId"]), job_id=child.id,
                    )
                    row.output_json = result.model_dump(mode="json", by_alias=True)

                submitted = self.voice_import.import_audio(
                    request.request, source=self._path(request.source_path), before_commit=checkpoint,
                )
                return VoiceImportResult(
                    voice_master_id=submitted.voice_master.voice_master_id, job_id=submitted.job.job_id,
                )
            if isinstance(request, VoiceReviewOperation):
                voice = self.require_voice(request.campaign_id, request.voice_id)
                if request.action == "approve":
                    if not (voice.status == "approved" and voice.approved_by == request.actor
                            and voice.approval_review_reason == request.reason):
                        voice = self.voices.approve(
                            request.voice_id, approved_by=request.actor,
                            approval_review_reason=request.reason,
                        )
                elif not (voice.status == "rejected" and voice.rejected_by == request.actor
                          and voice.rejection_reason == request.reason):
                    assert request.reason is not None
                    voice = self.voices.reject(
                        request.voice_id, rejected_by=request.actor, reason=request.reason,
                    )
                return VoiceReviewResult(voice_master_id=voice.voice_master_id, status=voice.status)
            if isinstance(request, ImagePrepareOperation):
                prepared = self.images.prepare_directory(
                    request.campaign_id, self._path(request.output_path, root=self.settings.work_root),
                )
                return ImagePrepareResult(
                    manifest_path=prepared.manifest_path.relative_to(self.settings.project_root).as_posix(),
                    images_path=prepared.images_path.relative_to(self.settings.project_root).as_posix(),
                    variant_count=prepared.variant_count,
                )
            if isinstance(request, ImageManifestOperation):
                published = self.images.publish_manifest(
                    request.campaign_id, self._path(request.directory_path), request.items,
                )
                return ImageManifestResult(
                    manifest_path=published.manifest_path.relative_to(self.settings.project_root).as_posix(),
                    images_path=published.images_path.relative_to(self.settings.project_root).as_posix(),
                    manifest_sha256=published.manifest_sha256, items=published.items,
                )
            if isinstance(request, ImageImportOperation):
                path, digest = self._manifest(request.manifest_path, request.campaign_id)
                if digest != request.manifest_sha256:
                    raise QueryError("artifact_invalid")
                try:
                    plan = self.images.plan(path, expected_sha256=request.manifest_sha256)
                except ImageImportValidationError as error:
                    if request.mode != "dry_run" or not request.include_diagnostics:
                        raise
                    return ImageImportOperationResult(
                        mode="dry_run", total=len(self.campaigns.get_campaign(request.campaign_id).scene_variants),
                        created=0, reused=0, approved=0, items=[], valid=False,
                        manifest_sha256=digest, validation_id=request.validation_id,
                        issues=[ImageImportDiagnostic(code=issue.code, variant_id=issue.variant_id)
                                for issue in error.issues],
                    )
                if plan.batch.campaign_id != request.campaign_id:
                    raise QueryError("artifact_invalid")
                if request.mode == "dry_run":
                    return ImageImportOperationResult(
                        mode=request.mode, total=len(plan.items), created=0, reused=0, approved=0,
                        valid=True if request.include_diagnostics else None,
                        manifest_sha256=digest, validation_id=request.validation_id,
                        items=[ImageImportItemResult(
                            variant_id=item.variant_id, scene_variant_id=item.scene_variant_id,
                            action=item.action, sha256=item.sha256, width=item.width,
                            height=item.height, size_bytes=item.size_bytes, format=item.format,
                        ) for item in plan.items],
                    )
                if request.expected_sources is not None:
                    expected = {item.variant_id: item.sha256 for item in request.expected_sources}
                    if expected != {item.variant_id: item.sha256 for item in plan.items}:
                        raise QueryError("artifact_invalid")
                imported = self.images.execute(plan)
                return ImageImportOperationResult(
                    mode=request.mode, total=imported.total, created=imported.created,
                    reused=imported.reused, approved=imported.approved,
                    items=[ImageImportItemResult(
                        variant_id=item.variant_id, scene_variant_id=item.scene_variant_id,
                        image_candidate_id=item.image_candidate_id, action=item.action,
                    ) for item in imported.items],
                )
            if isinstance(request, EditPlanOperation):
                if request.request.campaign_id != request.campaign_id:
                    raise QueryError("invalid_request")
                return EditPlanResult(
                    persist=request.persist, plan=self.editing.plan(
                        request.request, database_path=self.settings.database, persist=request.persist,
                    ),
                )
        except (ImageImportError, EditingError, OSError):
            raise QueryError("artifact_invalid") from None
        except (VoiceImportError, VoiceMasterError):
            raise QueryError("operation_not_allowed") from None
        except HeyGenServiceError:
            raise QueryError("operation_not_allowed") from None
        except ValueError:
            if isinstance(request, (HeyGenAssetsOperation, HeyGenVideoPlanOperation,
                                    HeyGenVideoSubmitOperation, HeyGenReconcileOperation)):
                raise QueryError("operation_not_allowed") from None
            raise
        raise QueryError("invalid_request")
