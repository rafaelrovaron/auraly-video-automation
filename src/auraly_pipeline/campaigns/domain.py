from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Self

from pydantic import ConfigDict, Field, JsonValue, computed_field, model_validator

from auraly_pipeline.metadata_security import validate_goal_1_campaign_metadata
from auraly_pipeline.models import ContractModel


class CampaignContract(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class CopyMasterContent(CampaignContract):
    """Canonical campaign copy with narration derived from non-visual sections."""

    source_text: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    body: str = Field(min_length=1)
    cta: str = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spoken_text(self) -> str:
        return "\n\n".join((self.hook, self.body, self.cta))


    @computed_field  # type: ignore[prop-decorator]
    @property
    def sha256(self) -> str:
        canonical = json.dumps(
            {
                "body": self.body,
                "cta": self.cta,
                "headline": self.headline,
                "hook": self.hook,
                "sourceText": self.source_text,
                "spokenText": self.spoken_text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class CopyMasterCreate(CopyMasterContent):
    approval_state: Literal["approved"] = "approved"
    approved_by: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_approver_for_approval(self) -> Self:
        if self.approval_state == "approved" and self.approved_by is None:
            raise ValueError("approved copy master requires approved_by")
        return self


class SceneVariantCreate(CampaignContract):
    variant_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    location: str = Field(min_length=1)
    time_atmosphere: str | None = Field(default=None, min_length=1)
    action: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    proof_object: str | None = Field(default=None, min_length=1)
    status: Literal["not_started"] = "not_started"


class CampaignCreate(CampaignContract):
    campaign_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    character: Literal["susan-smith", "soul-constellation"]
    proof_object: str = Field(min_length=1)
    voice_preset: str = Field(min_length=1)
    edit_preset: str = Field(min_length=1)
    budget: dict[str, JsonValue]
    config: dict[str, JsonValue]
    status: Literal["draft"] = "draft"
    copy_master: CopyMasterCreate
    scene_variants: list[SceneVariantCreate] = Field(min_length=3)

    @model_validator(mode="after")
    def reject_sensitive_metadata(self) -> Self:
        validate_goal_1_campaign_metadata(self.budget, "budget")
        validate_goal_1_campaign_metadata(self.config, "config")
        return self

    @model_validator(mode="after")
    def require_unique_variant_ids(self) -> Self:
        variant_ids = [variant.variant_id for variant in self.scene_variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("scene variant IDs must be unique")
        locations = [
            " ".join(variant.location.casefold().split()) for variant in self.scene_variants
        ]
        if len(locations) != len(set(locations)):
            raise ValueError("scene variant locations must be distinct")
        return self


class CopyMaster(CopyMasterContent):
    copy_master_id: str
    campaign_id: str
    version: int = Field(ge=1)
    approval_state: Literal["draft", "approved"]
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SceneVariant(SceneVariantCreate):
    scene_variant_id: str
    campaign_id: str
    created_at: datetime
    updated_at: datetime


class Campaign(CampaignContract):
    campaign_id: str
    character: Literal["susan-smith", "soul-constellation"]
    proof_object: str
    voice_preset: str
    edit_preset: str
    budget: dict[str, JsonValue]
    config: dict[str, JsonValue]
    status: Literal["draft"]
    created_at: datetime
    updated_at: datetime
    copy_masters: list[CopyMaster]
    scene_variants: list[SceneVariant]
