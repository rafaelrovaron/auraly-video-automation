from __future__ import annotations

from datetime import timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from auraly_pipeline.editing.domain import EditingModel, Identifier, Sha, relative_path, safe_id


class DeliveryRequest(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    video_id: Identifier
    plan_hash: Sha
    output_variant_id: Identifier
    render_key: Sha
    review_key: Sha
    _ids = field_validator("campaign_id", "video_id", "output_variant_id")(safe_id)


class DeliveryRecord(DeliveryRequest):
    delivered_at: AwareDatetime
    path: str
    master_sha256: Sha
    size_bytes: int = Field(strict=True, gt=0)
    receipt_sha256: Sha
    qc_report_sha256: Sha
    review_sha256: Sha
    _path = field_validator("path")(relative_path)

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.delivered_at.utcoffset() != timedelta(0):
            raise ValueError("UTC delivery timestamp required")
        if self.path != f"{self.campaign_id}/{self.output_variant_id}/{self.review_key}/master.mp4":
            raise ValueError("delivery path identity mismatch")
        return self


class DeliveryState(EditingModel):
    request: DeliveryRequest
    record: DeliveryRecord | None

    @model_validator(mode="after")
    def context(self) -> Self:
        if self.record is not None and any(getattr(self.record, name) != getattr(self.request, name)
                                           for name in DeliveryRequest.model_fields):
            raise ValueError("delivery state context mismatch")
        return self
