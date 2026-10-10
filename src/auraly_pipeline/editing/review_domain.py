from __future__ import annotations

from datetime import timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator

from auraly_pipeline.editing.domain import EditingModel, Identifier, Sha, relative_path, safe_id
from auraly_pipeline.editing.resolver import content_hash


def review_key(*, campaign_id: str, video_id: str, plan_hash: str, output_variant_id: str,
               render_key: str, master_sha256: str, receipt_sha256: str) -> str:
    return content_hash({"campaignId": campaign_id, "videoId": video_id, "planHash": plan_hash,
        "outputVariantId": output_variant_id, "renderKey": render_key,
        "masterSha256": master_sha256, "receiptSha256": receipt_sha256})


class HumanReviewRequest(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    video_id: Identifier
    plan_hash: Sha
    output_variant_id: Identifier
    render_key: Sha
    qc_key: Sha
    decision: Literal["approved", "rejected"]
    reviewed_by: str = Field(min_length=1)
    comment: str | None = None
    watched_and_listened: StrictBool
    _ids = field_validator("campaign_id", "video_id", "output_variant_id")(safe_id)

    @field_validator("reviewed_by", "comment", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def human_gate(self) -> Self:
        if self.decision == "approved" and not self.watched_and_listened:
            raise ValueError("approval requires watched and listened confirmation")
        if self.decision == "rejected" and not self.comment:
            raise ValueError("rejection requires a comment")
        return self


class HumanReviewRecord(HumanReviewRequest):
    reviewed_at: AwareDatetime
    review_key: Sha
    master_path: str
    master_sha256: Sha
    size_bytes: int = Field(strict=True, gt=0)
    receipt_sha256: Sha
    qc_report_sha256: Sha
    qc_status: Literal["passed", "blocked"]
    _path = field_validator("master_path")(relative_path)

    @model_validator(mode="after")
    def evidence(self) -> Self:
        if self.reviewed_at.utcoffset() != timedelta(0):
            raise ValueError("UTC review timestamp required")
        if self.decision == "approved" and self.qc_status != "passed":
            raise ValueError("approval requires passed QC")
        expected = review_key(campaign_id=self.campaign_id, video_id=self.video_id,
            plan_hash=self.plan_hash, output_variant_id=self.output_variant_id,
            render_key=self.render_key, master_sha256=self.master_sha256,
            receipt_sha256=self.receipt_sha256)
        if self.review_key != expected:
            raise ValueError("review version identity mismatch")
        return self
