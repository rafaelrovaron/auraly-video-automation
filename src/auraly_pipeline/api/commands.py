from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.api.action_contracts import (
    EditPlanOperation, EditPlanResult, ImageImportItemResult, ImageImportOperation,
    ImageImportOperationResult, ImagePrepareOperation, ImagePrepareResult,
    LocalOperationRequest, OperationResult, OperationSubmission, OperationView,
)
from auraly_pipeline.api.contracts import ApiSettings, ERROR_MESSAGES, ErrorCode, QueryError
from auraly_pipeline.api.operations import ApiOperationHandler, LOCAL_OPERATION_JOB
from auraly_pipeline.campaigns.service import CampaignNotFoundError, CampaignService
from auraly_pipeline.editing.batch_planner import verify_batch_plan
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditingError, relative_path
from auraly_pipeline.editing.service import validate_editing_path
from auraly_pipeline.heygen.provider import HeyGenProvider
from auraly_pipeline.images.import_batch import ImageImportBatch, ImageImportError, ImageImportService
from auraly_pipeline.jobs.domain import JobSubmit, RetrySafety
from auraly_pipeline.jobs.repository import JobRepository
from auraly_pipeline.jobs.service import JobNotFoundError, JobService
from auraly_pipeline.voices.handler import SpeechProvider, TranscriptProvider


class ApiCommands:
    def __init__(
        self, settings: ApiSettings, engine: Engine, *,
        speech_provider: SpeechProvider | None = None,
        transcriber: TranscriptProvider | None = None,
        heygen_provider: HeyGenProvider | None = None,
    ) -> None:
        self.settings = settings
        self._speech_provider = speech_provider
        self._transcriber = transcriber
        self._heygen_provider = heygen_provider
        self.campaigns = CampaignService(engine)
        self.images = ImageImportService.from_engine(engine, work_root=settings.work_root)
        self.editing = EditBatchService(
            project_root=settings.project_root, work_root=settings.work_root,
        )
        self.jobs = JobService(
            engine, JobRepository(sessionmaker(engine, expire_on_commit=False, class_=Session)),
            handlers={LOCAL_OPERATION_JOB: ApiOperationHandler(self)},
        )

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
        elif isinstance(request, ImageImportOperation):
            path, digest = self._manifest(request.manifest_path, request.campaign_id)
            if request.manifest_sha256 is not None and request.manifest_sha256 != digest:
                raise QueryError("artifact_invalid")
            request = request.model_copy(update={
                "manifest_path": path.relative_to(self.settings.project_root).as_posix(),
                "manifest_sha256": digest,
            })
        elif request.request.campaign_id != request.campaign_id:
            raise QueryError("invalid_request")
        payload = cast(dict[str, JsonValue], request.model_dump(mode="json", by_alias=True))
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

    def execute_operation(self, request: LocalOperationRequest) -> OperationResult:
        self.require_campaign(request.campaign_id)
        try:
            if isinstance(request, ImagePrepareOperation):
                prepared = self.images.prepare_directory(
                    request.campaign_id, self._path(request.output_path, root=self.settings.work_root),
                )
                return ImagePrepareResult(
                    manifest_path=prepared.manifest_path.relative_to(self.settings.project_root).as_posix(),
                    images_path=prepared.images_path.relative_to(self.settings.project_root).as_posix(),
                    variant_count=prepared.variant_count,
                )
            if isinstance(request, ImageImportOperation):
                path, digest = self._manifest(request.manifest_path, request.campaign_id)
                if digest != request.manifest_sha256:
                    raise QueryError("artifact_invalid")
                plan = self.images.plan(path)
                if request.mode == "dry_run":
                    return ImageImportOperationResult(
                        mode=request.mode, total=len(plan.items), created=0, reused=0, approved=0,
                        items=[ImageImportItemResult(
                            variant_id=item.variant_id, scene_variant_id=item.scene_variant_id,
                            action=item.action,
                        ) for item in plan.items],
                    )
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
        raise QueryError("invalid_request")
