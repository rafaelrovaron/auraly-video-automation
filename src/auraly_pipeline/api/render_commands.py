from __future__ import annotations

from collections.abc import Callable
from typing import cast

from pydantic import JsonValue

from auraly_pipeline.api.contracts import ERROR_MESSAGES, ErrorCode, QueryError
from auraly_pipeline.api.render_contracts import RenderJobSubmission, RenderJobView
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_domain import RenderBatchResult
from auraly_pipeline.editing.render_job_domain import RenderJobRequest, render_status
from auraly_pipeline.editing.render_service import RenderService
from auraly_pipeline.editing.resolver import content_hash
from auraly_pipeline.jobs.domain import JobSubmit, RetrySafety
from auraly_pipeline.jobs.service import JobNotFoundError, JobService
from auraly_pipeline.jobs.state_machine import JobStatus


class RenderCommands:
    def __init__(self, *, jobs: JobService, editing: EditBatchService, renderer: RenderService,
                 require_campaign: Callable[[str], object]) -> None:
        self.jobs, self.editing, self.renderer = jobs, editing, renderer
        self.require_campaign = require_campaign

    def _plan(self, request: RenderJobRequest) -> EditBatchPlan:
        try:
            return self.editing.get_plan(request.campaign_id, request.video_id, request.plan_hash)
        except (EditingError, OSError):
            raise QueryError("artifact_invalid") from None

    def submit(self, request: RenderJobRequest) -> RenderJobSubmission:
        request = RenderJobRequest.model_validate(request.model_dump(mode="json", by_alias=True))
        self.require_campaign(request.campaign_id)
        self._plan(request)
        payload = cast(dict[str, JsonValue], request.model_dump(mode="json", by_alias=True))
        job = self.jobs.submit_job(JobSubmit(campaign_id=request.campaign_id, job_type="editing.render",
            input=payload, idempotency_key="editing.render:" + content_hash(payload),
            retry_safety=RetrySafety.MANUAL_ONLY, max_attempts=1))
        return RenderJobSubmission(**request.model_dump(), job_id=job.job_id)

    def get(self, campaign_id: str, job_id: str) -> RenderJobView:
        self.require_campaign(campaign_id)
        try:
            job = self.jobs.get_job(job_id)
        except JobNotFoundError:
            raise QueryError("not_found", "jobId") from None
        except (ValueError, OSError):
            raise QueryError("artifact_invalid") from None
        if job.campaign_id != campaign_id or job.job_type != "editing.render":
            raise QueryError("not_found", "jobId")
        try:
            request = RenderJobRequest.model_validate(job.input)
            if request.campaign_id != campaign_id:
                raise ValueError("request campaign mismatch")
            plan = self._plan(request)
            result = None
            aggregate = None
            if job.status == JobStatus.COMPLETED:
                result = RenderBatchResult.model_validate(job.output)
                if [(o.key, o.output_variant_id) for o in result.outputs] != [
                        (o.key, o.output_variant_id) for o in plan.outputs]:
                    raise ValueError("render outputs mismatch")
                aggregate = render_status(result)
            error = None if job.last_error_code is None else cast(ErrorCode,
                job.last_error_code if job.last_error_code in ERROR_MESSAGES else "internal_error")
            return RenderJobView(**request.model_dump(), job_id=job.job_id, status=job.status,
                                 result=result, render_status=aggregate, error_code=error)
        except (ValueError, OSError):
            raise QueryError("artifact_invalid") from None

    def list(self, campaign_id: str, *, video_id: str | None = None,
             plan_hash: str | None = None) -> list[RenderJobView]:
        self.require_campaign(campaign_id)
        views = []
        try:
            jobs = self.jobs.list_jobs(campaign_id=campaign_id)
        except (ValueError, OSError):
            raise QueryError("artifact_invalid") from None
        for job in jobs:
            if job.job_type != "editing.render":
                continue
            if video_id is not None and job.input.get("videoId") != video_id:
                continue
            if plan_hash is not None and job.input.get("planHash") != plan_hash:
                continue
            views.append(self.get(campaign_id, job.job_id))
        return sorted(views, key=lambda v: v.job_id)
