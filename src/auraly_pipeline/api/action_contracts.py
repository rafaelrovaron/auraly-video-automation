from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator

from auraly_pipeline.api.contracts import ErrorCode
from auraly_pipeline.editing.batch_domain import EditBatchPlan, EditBatchRequest
from auraly_pipeline.editing.domain import Sha, relative_path
from auraly_pipeline.jobs.state_machine import JobStatus
from auraly_pipeline.models import ContractModel

OperationKind = Literal["image_prepare", "image_import", "edit_plan"]


class OperationRequest(ContractModel):
    campaign_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ImagePrepareOperation(OperationRequest):
    operation: Literal["image_prepare"]
    output_path: str = Field(min_length=1)


class ImageImportOperation(OperationRequest):
    operation: Literal["image_import"]
    manifest_path: str = Field(min_length=1)
    mode: Literal["dry_run", "execute"]
    manifest_sha256: Sha | None = None


class EditPlanOperation(OperationRequest):
    operation: Literal["edit_plan"]
    request: EditBatchRequest
    persist: bool = True


LocalOperationRequest: TypeAlias = Annotated[
    ImagePrepareOperation | ImageImportOperation | EditPlanOperation,
    Field(discriminator="operation"),
]


class ImagePrepareResult(ContractModel):
    operation: Literal["image_prepare"] = "image_prepare"
    manifest_path: str
    images_path: str
    variant_count: int
    _paths = field_validator("manifest_path", "images_path")(relative_path)


class ImageImportItemResult(ContractModel):
    variant_id: str
    scene_variant_id: str
    image_candidate_id: str | None = None
    action: Literal["create", "reuse"]


class ImageImportOperationResult(ContractModel):
    operation: Literal["image_import"] = "image_import"
    mode: Literal["dry_run", "execute"]
    total: int
    created: int
    reused: int
    approved: int
    items: list[ImageImportItemResult]


class EditPlanResult(ContractModel):
    operation: Literal["edit_plan"] = "edit_plan"
    persist: bool
    plan: EditBatchPlan


OperationResult: TypeAlias = Annotated[
    ImagePrepareResult | ImageImportOperationResult | EditPlanResult,
    Field(discriminator="operation"),
]


class OperationSubmission(ContractModel):
    job_id: str
    campaign_id: str
    operation: OperationKind


class OperationView(OperationSubmission):
    status: JobStatus
    result: OperationResult | None = None
    error_code: ErrorCode | None = None
