from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, Sequence

from pydantic import ConfigDict, Field, field_validator

from auraly_pipeline.jobs.domain import Job
from auraly_pipeline.metadata_security import (
    validate_safe_error_message,
    validate_safe_identifier,
)
from auraly_pipeline.models import ContractModel
from auraly_pipeline.voices.domain import validate_workspace_path


_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_IDEMPOTENCY_PATTERN = r"^heygen\.asset\.upload:[0-9a-f]{64}$"
_MIME_PATTERN = r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$"


class RemoteAssetKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"


class RemoteAssetStatus(StrEnum):
    ALLOCATED = "allocated"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class ProviderAssetStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"


class HeyGenContract(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class AssetSource(HeyGenContract):
    source_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    kind: RemoteAssetKind
    local_path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    mime_type: str = Field(pattern=_MIME_PATTERN, max_length=120)
    size_bytes: int = Field(gt=0)

    _local_path = field_validator("local_path")(validate_workspace_path)


class RemoteAsset(HeyGenContract):
    remote_asset_id_local: str = Field(pattern=_UUID_PATTERN, max_length=36)
    provider: Literal["heygen"] = "heygen"
    provider_account_ref: str = Field(max_length=200)
    kind: RemoteAssetKind
    sha256: str = Field(pattern=_SHA256_PATTERN)
    mime_type: str = Field(pattern=_MIME_PATTERN, max_length=120)
    size_bytes: int = Field(gt=0)
    remote_asset_id: str | None = Field(default=None, max_length=200)
    remote_batch_id: str | None = Field(default=None, max_length=200)
    status: RemoteAssetStatus
    last_error_code: str | None = Field(default=None, max_length=120)
    last_error_message: str | None = Field(default=None, max_length=512)
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "provider_account_ref",
        "remote_asset_id",
        "remote_batch_id",
        "last_error_code",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return validate_safe_identifier(value, getattr(info, "field_name", "identifier"), max_length=200)

    @field_validator("last_error_message")
    @classmethod
    def validate_error(cls, value: str | None) -> str | None:
        if value is not None:
            validate_safe_error_message(value)
        return value


class HeyGenPreflight(HeyGenContract):
    connected: bool
    account_ref: str | None = Field(default=None, max_length=200)
    capabilities: list[str] = Field(default_factory=list)
    max_batch_size: int | None = Field(default=None, ge=1, le=100)
    credits_remaining: float | None = Field(default=None, ge=0)

    @field_validator("account_ref")
    @classmethod
    def validate_account_ref(cls, value: str | None) -> str | None:
        if value is not None:
            validate_safe_identifier(value, "account_ref", max_length=200)
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        return [validate_safe_identifier(item, "capability", max_length=120) for item in value]


class AssetPreparationPlan(HeyGenContract):
    campaign_id: str = Field(max_length=80)
    account_ref: str = Field(max_length=200)
    sources: list[AssetSource] = Field(min_length=1, max_length=100)
    reused_assets: list[RemoteAsset] = Field(default_factory=list)
    upload_sources: list[AssetSource] = Field(default_factory=list, max_length=100)

    @field_validator("campaign_id", "account_ref")
    @classmethod
    def validate_identifiers(cls, value: str, info: object) -> str:
        return validate_safe_identifier(value, getattr(info, "field_name", "identifier"), max_length=200)


class AssetPreparationSubmission(HeyGenContract):
    plan: AssetPreparationPlan
    job: Job | None = None
    upload_count: int = Field(ge=0, le=100)


class AssetUploadSlot(HeyGenContract):
    source_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    asset_id: str = Field(max_length=200)
    upload_url: str = Field(repr=False, exclude=True)
    upload_headers: dict[str, str] = Field(repr=False, exclude=True)
    expires_in_seconds: int = Field(gt=0)
    max_bytes: int = Field(gt=0)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "asset_id", max_length=200)


class AssetBatchAllocation(HeyGenContract):
    batch_id: str = Field(max_length=200)
    slots: list[AssetUploadSlot] = Field(min_length=1, max_length=100)

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "batch_id", max_length=200)


class AssetBatchState(HeyGenContract):
    batch_id: str = Field(max_length=200)
    statuses: dict[str, ProviderAssetStatus]
    error_codes: dict[str, str] = Field(default_factory=dict)
    error_messages: dict[str, str] = Field(default_factory=dict)

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "batch_id", max_length=200)

    @field_validator("error_messages")
    @classmethod
    def validate_errors(cls, value: dict[str, str]) -> dict[str, str]:
        for message in value.values():
            validate_safe_error_message(message)
        return value


class AssetUploadJobInput(HeyGenContract):
    account_ref: str = Field(max_length=200)
    idempotency_key: str = Field(pattern=_IDEMPOTENCY_PATTERN, max_length=84)
    sources: list[AssetSource] = Field(min_length=1, max_length=100)

    @field_validator("account_ref", "idempotency_key")
    @classmethod
    def validate_identifiers(cls, value: str, info: object) -> str:
        return validate_safe_identifier(value, getattr(info, "field_name", "identifier"), max_length=200)


def asset_batch_idempotency_key(
    account_ref: str, sources: Sequence[AssetSource]
) -> str:
    validate_safe_identifier(account_ref, "account_ref", max_length=200)
    canonical = {
        "accountRef": account_ref,
        "assets": sorted(f"{source.kind.value}:{source.sha256}" for source in sources),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return f"heygen.asset.upload:{digest}"
