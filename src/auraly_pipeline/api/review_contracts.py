from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import field_validator, model_validator

from auraly_pipeline.api.action_contracts import EditQcOperation, EditQcResult, OperationView
from auraly_pipeline.editing.domain import EditingModel, Identifier, Sha, safe_id
from auraly_pipeline.editing.qc_domain import QcReport
from auraly_pipeline.editing.review_domain import HumanReviewRecord


class ReviewTarget(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    job_id: UUID
    video_id: Identifier
    plan_hash: Sha
    output_variant_id: Identifier
    render_key: Sha
    _ids = field_validator("campaign_id", "video_id", "output_variant_id")(safe_id)


class QcRunView(OperationView):
    operation: Literal["edit_qc"] = "edit_qc"
    request: EditQcOperation
    result: EditQcResult | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        UUID(self.job_id)
        if self.campaign_id != self.request.campaign_id or self.voice_master_id is not None:
            raise ValueError("QC operation context mismatch")
        if self.status == "completed":
            if self.result is None or self.result.request != self.request or self.error_code is not None:
                raise ValueError("invalid completed QC operation")
        elif self.result is not None:
            raise ValueError("result requires completed QC operation")
        return self


class FinalReviewState(ReviewTarget):
    reports: list[QcReport]
    review: HumanReviewRecord | None
    runs: list[QcRunView]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        names = ("campaign_id", "video_id", "plan_hash", "output_variant_id", "render_key")
        expected = tuple(getattr(self, name) for name in names)
        for item in [*self.reports, *([self.review] if self.review is not None else [])]:
            if tuple(getattr(item, name) for name in names) != expected:
                raise ValueError("review evidence context mismatch")
        for run in self.runs:
            if (tuple(getattr(run.request, name) for name in names) != expected
                    or run.request.job_id != self.job_id):
                raise ValueError("QC run context mismatch")
        keys = [report.qc_key for report in self.reports]
        executions = [run.request.execution_id for run in self.runs]
        if len(set(keys)) != len(keys) or len(set(executions)) != len(executions):
            raise ValueError("duplicate evidence identity")
        if self.review is not None and self.review.qc_key not in keys:
            raise ValueError("review original QC missing")
        return self
