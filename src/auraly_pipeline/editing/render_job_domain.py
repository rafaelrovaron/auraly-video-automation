from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import field_validator

from auraly_pipeline.editing.domain import EditingModel, Identifier, Sha, safe_id
from auraly_pipeline.editing.render_domain import RenderBatchResult


class RenderJobRequest(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    video_id: Identifier
    plan_hash: Sha
    execution_id: UUID
    _ids = field_validator("campaign_id", "video_id")(safe_id)


RenderStatus = Literal["succeeded", "partial_failure", "failed"]


def validate_job_result(result: RenderBatchResult) -> None:
    if (result.dry_run or not result.outputs
            or any(o.status == "planned" for o in result.outputs)
            or len({o.key for o in result.outputs}) != len(result.outputs)
            or len({o.output_variant_id for o in result.outputs}) != len(result.outputs)):
        raise ValueError("invalid render job result")


def render_status(result: RenderBatchResult) -> RenderStatus:
    validate_job_result(result)
    failed = sum(o.status == "failed" for o in result.outputs)
    return "succeeded" if not failed else "failed" if failed == len(result.outputs) else "partial_failure"
