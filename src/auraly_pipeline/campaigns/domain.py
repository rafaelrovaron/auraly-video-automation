from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Literal, Self

from pydantic import ConfigDict, Field, JsonValue, computed_field, model_validator

from auraly_pipeline.models import ContractModel

_SENSITIVE_METADATA_KEYS = {
    "apikey",
    "auth",
    "authorization",
    "browserprofile",
    "cookie",
    "cookies",
    "oauthtoken",
    "password",
    "refreshtoken",
    "secret",
    "signedurl",
    "storagestate",
    "token",
    "accesstoken",
}


_SENSITIVE_METADATA_SUFFIXES = (
    "accesskey",
    "apikey",
    "cookie",
    "credential",
    "credentials",
    "password",
    "privatekey",
    "secret",
    "secretkey",
    "signedurl",
    "token",
)


def _contains_sensitive_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if (
                normalized in _SENSITIVE_METADATA_KEYS
                or normalized.endswith(_SENSITIVE_METADATA_SUFFIXES)
                or _contains_sensitive_key(child)
            ):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def _contains_sensitive_value(value: JsonValue) -> bool:
    if isinstance(value, str):
        normalized = value.casefold()
        return any(
            marker in normalized
            for marker in (
                "?x-amz-signature=",
                "&x-amz-signature=",
                "?x-goog-signature=",
                "&x-goog-signature=",
                "?signature=",
                "&signature=",
                "?sig=",
                "&sig=",
            )
        )
    if isinstance(value, dict):
        return any(_contains_sensitive_value(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_value(child) for child in value)
    return False


def _contains_non_finite_number(value: JsonValue) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite_number(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite_number(child) for child in value)
    return False


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
        if _contains_sensitive_key(self.budget):
            raise ValueError("budget contains a forbidden sensitive key")
        if _contains_sensitive_key(self.config):
            raise ValueError("config contains a forbidden sensitive key")
        if _contains_sensitive_value(self.budget):
            raise ValueError("budget contains forbidden sensitive data")
        if _contains_sensitive_value(self.config):
            raise ValueError("config contains forbidden sensitive data")
        if _contains_non_finite_number(self.budget):
            raise ValueError("budget contains a non-finite number")
        if _contains_non_finite_number(self.config):
            raise ValueError("config contains a non-finite number")
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
