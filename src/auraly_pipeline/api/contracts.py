from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Generic, Literal, TypeVar

from auraly_pipeline.campaigns.domain import CopyMaster, SceneVariant

from auraly_pipeline.campaigns.persistence import default_database_path
from auraly_pipeline.config_paths import DEFAULT_PROJECT_ROOT, WORK_ROOT_RELATIVE
from auraly_pipeline.editing.service import validate_editing_path
from auraly_pipeline.editing.domain import EditProfile, IdentityRef, SourceVideoRef
from auraly_pipeline.editing.batch_domain import CopyRef
from auraly_pipeline.heygen.video_domain import HeyGenRenderStatus, VideoSource
from auraly_pipeline.images.domain import ImageCandidateReviewStatus, ImageSourceKind
from auraly_pipeline.jobs.domain import RetrySafety
from auraly_pipeline.jobs.state_machine import JobStatus
from auraly_pipeline.models import ContractModel
from auraly_pipeline.voices.domain import TranscriptMatchStatus, VoiceMasterStatus

ErrorCode = Literal[
    "invalid_request", "not_found", "artifact_invalid", "storage_unavailable",
    "internal_error", "method_not_allowed",
    "operation_conflict", "operation_not_allowed",
]
ERROR_MESSAGES: dict[ErrorCode, str] = {
    "invalid_request": "Invalid request.",
    "not_found": "Resource not found.",
    "artifact_invalid": "Stored artifact is invalid.",
    "storage_unavailable": "Local storage is unavailable or incompatible.",
    "internal_error": "The local query failed safely.",
    "method_not_allowed": "This API is read-only.",
    "operation_conflict": "Another local operation is running.",
    "operation_not_allowed": "The operation is not allowed in the current state.",
}


class QueryError(ValueError):
    def __init__(self, code: ErrorCode, field: str | None = None) -> None:
        self.code = code
        self.field = field if field in {
            "campaignId", "jobId", "profileId", "version", "videoId", "planHash",
        } else None
        super().__init__(ERROR_MESSAGES[code])


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: str
    field: str | None = None


class ErrorBody(ContractModel):
    error: ErrorDetail


class Health(ContractModel):
    status: Literal["ok"] = "ok"
    api_version: Literal[1] = 1


@dataclass(frozen=True)
class ApiSettings:
    project_root: Path
    work_root: Path
    database: Path

    def __post_init__(self) -> None:
        try:
            project = self.project_root.expanduser().absolute()
            work = self.work_root.expanduser().absolute()
            database = self.database.expanduser().absolute()
            validate_editing_path(project, project)
            validate_editing_path(project, work)
            validate_editing_path(database.parent, database)
            object.__setattr__(self, "project_root", project.resolve())
            object.__setattr__(self, "work_root", work.resolve())
            object.__setattr__(self, "database", database.resolve())
        except (ValueError, OSError):
            raise ValueError("Invalid local API configuration.") from None

    @classmethod
    def from_options(
        cls, *, project_root: Path | None = None, work_root: Path | None = None,
        database: Path | None = None,
    ) -> ApiSettings:
        configured = os.getenv("AURALY_PROJECT_ROOT", "").strip()
        project = project_root if project_root is not None else (
            Path(configured) if configured else DEFAULT_PROJECT_ROOT
        )
        return cls(project, work_root if work_root is not None else project / WORK_ROOT_RELATIVE,
                   database if database is not None else default_database_path())


T = TypeVar("T")


class Items(ContractModel, Generic[T]):
    items: list[T]


OperationalStatus = Literal[
    "needs_input", "needs_review", "in_progress", "needs_attention",
    "ready_for_editing", "editing_planned",
]
PendingCode = Literal[
    "attention_required", "wait_for_job", "editing_plan_missing", "caption_timing_missing",
    "renderer_not_implemented", "copy_approval_missing", "voice_review_required",
    "voice_missing", "image_review_required", "image_missing", "heygen_render_missing",
]


class PendingItem(ContractModel):
    code: PendingCode
    stage: Literal["copy", "voice", "images", "heygen", "editing"]
    entity_id: str
    message: str


class SceneStatus(ContractModel):
    scene_variant_id: str
    variant_id: str
    current_copy_id: str | None
    current_voice_id: str | None
    approved_image_id: str | None
    ready_render_ids: list[str]
    plan_hashes: list[str]
    pending: list[PendingItem]


class CampaignStatus(ContractModel):
    campaign_id: str
    stored_status: str
    operational_status: OperationalStatus
    next_pending: PendingItem | None
    scene_count: int
    approved_copy_count: int
    approved_voice_count: int
    approved_image_count: int
    ready_render_count: int
    plan_count: int
    scenes: list[SceneStatus]


class CampaignSummary(ContractModel):
    campaign_id: str
    character: Literal["susan-smith", "soul-constellation"]
    stored_status: str
    scene_count: int
    created_at: datetime
    updated_at: datetime
    operational_status: OperationalStatus
    next_pending: PendingItem | None


class CampaignDetail(CampaignSummary):
    proof_object: str
    voice_preset: str
    edit_preset: str
    copy_masters: list[CopyMaster]
    scene_variants: list[SceneVariant]


class ImageSummary(ContractModel):
    image_candidate_id: str
    scene_variant_id: str
    source_kind: ImageSourceKind
    image_generation_id: str | None
    review_status: ImageCandidateReviewStatus
    sha256: str
    source_path: str
    width: int
    height: int
    size_bytes: int
    format: str
    approved_at: datetime | None
    approved_by: str | None
    rejected_at: datetime | None
    rejected_by: str | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


class SceneImages(Items[ImageSummary]):
    scene_variant_id: str


class VoiceSummary(ContractModel):
    voice_master_id: str
    campaign_id: str
    copy_master_id: str
    copy_master_version: int
    generation: int
    provider: Literal["elevenlabs", "imported"]
    status: VoiceMasterStatus
    processed_audio_path: str | None
    processed_sha256: str | None
    duration_seconds: float | None
    transcript_match_status: TranscriptMatchStatus | None
    headline_spoken: bool | None
    qc_findings: list[str]
    approved_at: datetime | None
    approved_by: str | None
    approval_review_reason: str | None
    rejected_at: datetime | None
    rejected_by: str | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


class RenderSummary(ContractModel):
    render_id: str
    campaign_id: str
    scene_variant_id: str
    image_candidate_id: str
    voice_master_id: str
    job_id: str
    status: HeyGenRenderStatus
    remote_video_id: str | None
    manual_binding: bool
    image_sha256: str
    audio_sha256: str
    source: VideoSource | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class JobSummary(ContractModel):
    job_id: str
    job_type: str
    campaign_id: str | None
    scene_variant_id: str | None
    status: JobStatus
    attempt_count: int
    max_attempts: int
    retry_safety: RetrySafety
    created_at: datetime
    updated_at: datetime
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    next_retry_at: datetime | None
    last_error_code: str | None


class ProfileView(ContractModel):
    profile: EditProfile
    profile_hash: str


class OutputSummary(ContractModel):
    key: str
    label: str
    output_variant_id: str
    manifest_hash: str
    output_hash: str
    filename: str
    caption_state: Literal["disabled", "timing_missing", "timing_provided"]


class PlanSummary(ContractModel):
    video_id: str
    render_id: str
    plan_hash: str
    output_count: int
    max_outputs: int
    timing_status: Literal["missing", "provided"]
    copy_ref: CopyRef
    voice_ref: IdentityRef
    source: SourceVideoRef
    outputs: list[OutputSummary]
