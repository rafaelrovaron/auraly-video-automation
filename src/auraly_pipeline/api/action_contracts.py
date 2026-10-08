from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from auraly_pipeline.api.contracts import ErrorCode, RenderSummary
from auraly_pipeline.editing.batch_domain import EditBatchPlan, EditBatchRequest
from auraly_pipeline.editing.domain import Sha, relative_path
from auraly_pipeline.jobs.state_machine import JobStatus
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig
from auraly_pipeline.models import ContractModel
from auraly_pipeline.images.import_batch import ImageImportItem
from auraly_pipeline.metadata_security import validate_safe_identifier, validate_safe_error_message
from auraly_pipeline.voices.domain import VoiceImportRequest, VoiceMasterStatus

OperationKind = Literal[
    "image_prepare", "image_manifest", "image_import", "edit_plan", "voice_generate", "voice_import", "voice_review",
    "heygen_assets", "heygen_video_plan", "heygen_video_submit", "heygen_reconcile",
]


class OperationRequest(ContractModel):
    campaign_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ImagePrepareOperation(OperationRequest):
    operation: Literal["image_prepare"] = "image_prepare"
    output_path: str = Field(min_length=1)


class ImageSourceSnapshot(ContractModel):
    variant_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    sha256: Sha


class ImageImportOperation(OperationRequest):
    operation: Literal["image_import"] = "image_import"
    manifest_path: str = Field(min_length=1)
    mode: Literal["dry_run", "execute"]
    manifest_sha256: Sha | None = None
    include_diagnostics: bool = False
    validation_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_sources: list[ImageSourceSnapshot] | None = Field(default=None, min_length=1)
    _validation_id = field_validator("validation_id")(
        lambda value: None if value is None else validate_safe_identifier(value, "validation_id", max_length=120)
    )

    @model_validator(mode="after")
    def compatible_fields(self) -> Self:
        if self.mode != "dry_run" and (self.include_diagnostics or self.validation_id is not None):
            raise ValueError("diagnostics and validationId require dry_run")
        if self.expected_sources is not None:
            if self.mode != "execute":
                raise ValueError("source snapshot requires execute")
            ids = [source.variant_id for source in self.expected_sources]
            if len(ids) != len(set(ids)):
                raise ValueError("source snapshot variant IDs must be unique")
        return self


class ImageManifestOperation(OperationRequest):
    operation: Literal["image_manifest"] = "image_manifest"
    directory_path: str = Field(min_length=1)
    items: list[ImageImportItem] = Field(min_length=1)
    _directory = field_validator("directory_path")(relative_path)


class EditPlanOperation(OperationRequest):
    operation: Literal["edit_plan"] = "edit_plan"
    request: EditBatchRequest
    persist: bool = True


class VoiceImportOperation(OperationRequest):
    operation: Literal["voice_import"] = "voice_import"
    request: VoiceImportRequest
    source_path: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)


class VoiceReviewOperation(OperationRequest):
    operation: Literal["voice_review"] = "voice_review"
    voice_id: str = Field(min_length=1)
    action: Literal["approve", "reject"]
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("actor")
    @classmethod
    def safe_actor(cls, value: str) -> str:
        return validate_safe_identifier(value, "actor", max_length=120)

    @model_validator(mode="after")
    def review_reason(self) -> Self:
        if self.action == "reject" and self.reason is None:
            raise ValueError("rejection reason required")
        if self.reason is not None:
            validate_safe_error_message(self.reason, "reason")
        return self


class HeyGenAssetsOperation(OperationRequest):
    operation: Literal["heygen_assets"] = "heygen_assets"
    request_id: str = Field(min_length=1, max_length=120)


class HeyGenVideoOperation(OperationRequest):
    request_id: str = Field(min_length=1, max_length=120)
    config: HeyGenVideoConfig
    max_paid_renders: int = Field(gt=0, strict=True)


class HeyGenVideoPlanOperation(HeyGenVideoOperation):
    operation: Literal["heygen_video_plan"] = "heygen_video_plan"


class HeyGenVideoSubmitOperation(HeyGenVideoOperation):
    operation: Literal["heygen_video_submit"] = "heygen_video_submit"
    approved_by: str = Field(min_length=1, max_length=200)
    _actor = field_validator("approved_by")(
        lambda value: validate_safe_identifier(value, "approved_by", max_length=200)
    )


class HeyGenReconcileOperation(OperationRequest):
    operation: Literal["heygen_reconcile"] = "heygen_reconcile"
    request_id: str = Field(min_length=1, max_length=120)
    render_id: str = Field(min_length=1)
    video_id: str | None = Field(default=None, min_length=1, max_length=200)
    confirm_manual_binding: bool = False
    _video = field_validator("video_id")(
        lambda value: None if value is None else validate_safe_identifier(value, "video_id", max_length=200)
    )


LocalOperationRequest: TypeAlias = Annotated[
    ImagePrepareOperation | ImageManifestOperation | ImageImportOperation | EditPlanOperation |
    VoiceImportOperation | VoiceReviewOperation | HeyGenAssetsOperation |
    HeyGenVideoPlanOperation | HeyGenVideoSubmitOperation | HeyGenReconcileOperation,
    Field(discriminator="operation"),
]


class ImagePrepareResult(ContractModel):
    operation: Literal["image_prepare"] = "image_prepare"
    manifest_path: str
    images_path: str
    variant_count: int
    output_path: str | None = None
    _paths = field_validator("manifest_path", "images_path")(relative_path)
    _output = field_validator("output_path")(lambda value: None if value is None else relative_path(value))


class ImageImportItemResult(ContractModel):
    variant_id: str
    scene_variant_id: str
    image_candidate_id: str | None = None
    action: Literal["create", "reuse"]
    sha256: Sha | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    size_bytes: int | None = Field(default=None, gt=0)
    format: str | None = Field(default=None, min_length=1)


class ImageManifestResult(ContractModel):
    operation: Literal["image_manifest"] = "image_manifest"
    manifest_path: str
    images_path: str
    manifest_sha256: Sha
    items: list[ImageImportItem]
    _paths = field_validator("manifest_path", "images_path")(relative_path)


IMAGE_DIAGNOSTIC_CODES = frozenset({
    "image_import_manifest_invalid", "image_import_campaign_not_found",
    "image_import_variant_coverage_invalid", "image_import_source_path_invalid",
    "image_import_media_invalid", "image_import_orientation_invalid",
    "image_import_approved_candidate_conflict", "image_validation_failed",
})


class ImageImportDiagnostic(ContractModel):
    code: str
    variant_id: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    @field_validator("code")
    @classmethod
    def safe_code(cls, value: str) -> str:
        return value if value in IMAGE_DIAGNOSTIC_CODES else "image_validation_failed"


class ImageImportOperationResult(ContractModel):
    operation: Literal["image_import"] = "image_import"
    mode: Literal["dry_run", "execute"]
    total: int
    created: int
    reused: int
    approved: int
    items: list[ImageImportItemResult]
    valid: bool | None = None
    manifest_sha256: Sha | None = None
    validation_id: str | None = None
    issues: list[ImageImportDiagnostic] = Field(default_factory=list)


class EditPlanResult(ContractModel):
    operation: Literal["edit_plan"] = "edit_plan"
    persist: bool
    plan: EditBatchPlan


class VoiceImportResult(ContractModel):
    operation: Literal["voice_import"] = "voice_import"
    voice_master_id: str
    job_id: str


class VoiceReviewResult(ContractModel):
    operation: Literal["voice_review"] = "voice_review"
    voice_master_id: str
    status: VoiceMasterStatus


class HeyGenAssetsResult(ContractModel):
    operation: Literal["heygen_assets"] = "heygen_assets"
    upload_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    job_id: str | None


class HeyGenVideoPlanResult(ContractModel):
    operation: Literal["heygen_video_plan"] = "heygen_video_plan"
    new_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    reserved_count: int = Field(ge=0)
    max_paid_renders: int = Field(gt=0, strict=True)
    total_audio_seconds: float = Field(ge=0, allow_inf_nan=False)
    scene_variant_ids: list[str]


class HeyGenVideoSubmitResult(ContractModel):
    operation: Literal["heygen_video_submit"] = "heygen_video_submit"
    renders: list[RenderSummary]


class HeyGenReconcileResult(ContractModel):
    operation: Literal["heygen_reconcile"] = "heygen_reconcile"
    render: RenderSummary


OperationResult: TypeAlias = Annotated[
    ImagePrepareResult | ImageManifestResult | ImageImportOperationResult | EditPlanResult |
    VoiceImportResult | VoiceReviewResult | HeyGenAssetsResult |
    HeyGenVideoPlanResult | HeyGenVideoSubmitResult | HeyGenReconcileResult,
    Field(discriminator="operation"),
]


class OperationSubmission(ContractModel):
    job_id: str
    campaign_id: str
    operation: OperationKind
    voice_master_id: str | None = None


class OperationView(OperationSubmission):
    status: JobStatus
    result: OperationResult | None = None
    error_code: ErrorCode | None = None


WorkerKind = Literal["local_operations", "voice_generate", "voice_import", "heygen_assets", "heygen_videos", "editing_render"]


class WorkerState(ContractModel):
    state: Literal["idle", "running", "stopping"] = "idle"
    campaign_id: str | None = None
    kind: WorkerKind | None = None
    error_code: ErrorCode | None = None


class WorkerStartAction(OperationRequest):
    kind: WorkerKind


class ImageReviewAction(OperationRequest):
    action: Literal["approve", "reject", "replace"]
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, min_length=1, max_length=512)
    _actor = field_validator("actor")(lambda value: validate_safe_identifier(value, "actor", max_length=120))

    @model_validator(mode="after")
    def valid_reason(self) -> Self:
        if self.action == "reject" and self.reason is None:
            raise ValueError("rejection reason required")
        if self.reason is not None:
            validate_safe_error_message(self.reason, "reason")
        return self
