from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from auraly_pipeline.api.contracts import ErrorCode
from auraly_pipeline.editing.render_domain import RenderBatchResult
from auraly_pipeline.editing.render_job_domain import RenderJobRequest, RenderStatus, render_status
from auraly_pipeline.jobs.state_machine import JobStatus


class RenderJobSubmission(RenderJobRequest):
    job_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")


class RenderJobView(RenderJobSubmission):
    status: JobStatus
    result: RenderBatchResult | None = None
    render_status: RenderStatus | None = None
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.status == JobStatus.COMPLETED:
            if (self.result is None or self.result.plan_hash != self.plan_hash
                    or self.render_status != render_status(self.result) or self.error_code is not None):
                raise ValueError("invalid completed render job")
        elif self.result is not None or self.render_status is not None:
            raise ValueError("result requires completed render job")
        return self
