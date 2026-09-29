from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from auraly_pipeline.heygen.domain import HeyGenContract, ProviderAssetStatus
from auraly_pipeline.metadata_security import validate_safe_identifier
from auraly_pipeline.probe import MediaProbe
from auraly_pipeline.voices.domain import validate_workspace_path

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
SHA_PATTERN = r"^[0-9a-f]{64}$"


class HeyGenVideoConfig(HeyGenContract):
    schema_version: Literal[1] = 1
    generation_mode: Literal["image"] = "image"
    engine_selection: Literal["provider_default"] = "provider_default"
    aspect_ratio: Literal["9:16"] = "9:16"
    resolution: Literal["1080p", "720p"] = "1080p"
    output_format: Literal["mp4"] = "mp4"
    fit: Literal["cover", "contain"] = "cover"
    expressiveness: Literal["low", "medium", "high"] = "medium"
    motion_prompt: str | None = Field(default=None, max_length=2000)
    concurrency: int = Field(default=2, ge=1, le=2, strict=True)
    poll_initial_seconds: int = Field(default=10, ge=1, le=60, strict=True)
    poll_max_seconds: int = Field(default=60, ge=1, le=60, strict=True)
    poll_timeout_seconds: int = Field(default=1800, ge=1, le=1800, strict=True)

    @model_validator(mode="after")
    def valid_polling(self) -> HeyGenVideoConfig:
        if self.poll_initial_seconds > self.poll_max_seconds:
            raise ValueError("initial polling interval exceeds maximum")
        return self


class VideoPlanItem(HeyGenContract):
    campaign_id: str = Field(max_length=80)
    scene_variant_id: str = Field(pattern=UUID_PATTERN)
    image_candidate_id: str = Field(pattern=UUID_PATTERN)
    voice_master_id: str = Field(pattern=UUID_PATTERN)
    account_ref: str = Field(max_length=200)
    image_sha256: str = Field(pattern=SHA_PATTERN)
    audio_sha256: str = Field(pattern=SHA_PATTERN)
    image_asset_id: str = Field(max_length=200)
    audio_asset_id: str = Field(max_length=200)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    config: HeyGenVideoConfig
    schema_fingerprint: str = Field(pattern=SHA_PATTERN)

    @field_validator("campaign_id", "account_ref", "image_asset_id", "audio_asset_id")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_safe_identifier(value, "video_identifier", max_length=200)


class VideoPlan(HeyGenContract):
    items: list[VideoPlanItem]
    new_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    reserved_count: int = Field(ge=0)
    max_paid_renders: int = Field(gt=0, strict=True)
    total_audio_seconds: float = Field(ge=0)


class HeyGenRenderStatus(StrEnum):
    PLANNED = "planned"
    SUBMITTING = "submitting"
    PROCESSING = "processing"
    DOWNLOAD_PENDING = "download_pending"
    READY = "ready"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class VideoSource(HeyGenContract):
    path: str
    sha256: str = Field(pattern=SHA_PATTERN)
    size_bytes: int = Field(gt=0)
    probe: MediaProbe

    _path = field_validator("path")(validate_workspace_path)


class HeyGenRender(HeyGenContract):
    render_id: str = Field(pattern=UUID_PATTERN)
    item: VideoPlanItem
    logical_key: str = Field(pattern=SHA_PATTERN)
    config_sha256: str = Field(pattern=SHA_PATTERN)
    job_id: str = Field(pattern=UUID_PATTERN)
    status: HeyGenRenderStatus
    max_paid_renders: int = Field(gt=0)
    approved_by: str
    dispatch_started_at: datetime | None = None
    remote_video_id: str | None = None
    manual_binding: bool = False
    source: VideoSource | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("approved_by", "remote_video_id", "error_code")
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else validate_safe_identifier(value, "render_identifier", max_length=200)
        )


class VideoPreflight(HeyGenContract):
    account_ref: str
    tool_name: Literal["create_video_from_image"] = "create_video_from_image"
    schema_fingerprint: str = Field(pattern=SHA_PATTERN)

    _account = field_validator("account_ref")(VideoPlanItem.identifiers)


class ProviderVideo(HeyGenContract):
    video_id: str
    status: ProviderAssetStatus
    download_url: str | None = Field(default=None, exclude=True, repr=False)
    callback_id: str | None = None
    image_asset_id: str | None = None
    audio_asset_id: str | None = None

    _ids = field_validator("video_id", "callback_id", "image_asset_id", "audio_asset_id")(
        HeyGenRender.identifiers
    )


class VideoRunSummary(HeyGenContract):
    renders: list[HeyGenRender]
    ready_count: int
    failed_count: int
    blocked_count: int


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def material_config_sha256(config: HeyGenVideoConfig) -> str:
    return _digest(
        config.model_dump(
            exclude={
                "concurrency",
                "poll_initial_seconds",
                "poll_max_seconds",
                "poll_timeout_seconds",
            }
        )
    )


def video_logical_key(item: VideoPlanItem) -> str:
    data = item.model_dump(exclude={"config", "schema_fingerprint"})
    data["config_sha256"] = material_config_sha256(item.config)
    return _digest(data)
