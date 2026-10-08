from __future__ import annotations

from typing import cast

from pydantic import JsonValue, ValidationError

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_job_domain import RenderJobRequest, validate_job_result
from auraly_pipeline.editing.render_service import RenderService
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext


class RenderJobHandler:
    retry_safety = RetrySafety.MANUAL_ONLY

    def __init__(self, renderer: RenderService) -> None:
        self.renderer = renderer

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        try:
            request = RenderJobRequest.model_validate(context.input)
            if context.job_type != "editing.render" or request.campaign_id != context.campaign_id:
                raise ValueError("invalid render request")
        except (ValueError, ValidationError):
            return JobExecutionResult(outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                error_code="invalid_request", error_message="Invalid render request.")
        try:
            result = self.renderer.render(request.campaign_id, request.video_id, request.plan_hash, dry_run=False)
            validate_job_result(result)
            if result.plan_hash != request.plan_hash:
                raise ValueError("render result plan mismatch")
            return JobExecutionResult(outcome=JobExecutionOutcome.SUCCESS,
                result=cast(dict[str, JsonValue], result.model_dump(mode="json", by_alias=True,
                                                                   exclude_computed_fields=True)))
        except (EditingError, ValueError):
            return JobExecutionResult(outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                error_code="artifact_invalid", error_message="Local render artifact is invalid.")
        except Exception:
            return JobExecutionResult(outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                error_code="internal_error", error_message="Local render failed safely.")
