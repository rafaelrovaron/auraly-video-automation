from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from auraly_pipeline.campaigns.domain import CopyMaster
from auraly_pipeline.editing.domain import (
    AssetRef, EditManifestV2, EditOverrides, EditingModel, Identifier,
    IdentityRef, Nonnegative, Positive, ProfileRef, Sha, SourceVideoRef, Text, safe_id,
)
from auraly_pipeline.metadata_security import validate_safe_identifier


def operator(value: str) -> str:
    return validate_safe_identifier(value, "acceptedBy", max_length=120)


class CopyRef(EditingModel):
    id: Text
    version: int = Field(gt=0, strict=True)
    hash: Sha


class EditVariant(EditingModel):
    key: Identifier
    label: Text
    overrides: EditOverrides = Field(default_factory=EditOverrides)
    _key = field_validator("key")(safe_id)


class EditBatchRequest(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    render_id: Identifier
    video_id: Identifier
    profile_ref: ProfileRef
    headline_text: Text
    campaign: EditOverrides = Field(default_factory=EditOverrides)
    video: EditOverrides = Field(default_factory=EditOverrides)
    music_accepted: bool = False
    variants: list[EditVariant] = Field(min_length=1)
    max_outputs: int = Field(default=3, gt=0, strict=True)
    timing_ref: AssetRef | None = None
    _ids = field_validator("campaign_id", "render_id", "video_id")(safe_id)

    @model_validator(mode="after")
    def limits(self) -> Self:
        if len(self.variants) > self.max_outputs:
            raise ValueError("variants exceed maxOutputs")
        keys = [v.key for v in self.variants]
        if len(set(keys)) != len(keys):
            raise ValueError("variant keys must be unique")
        return self


class CaptionTimingCue(EditingModel):
    start_sec: Nonnegative
    end_sec: Positive
    token_start: int = Field(ge=0, strict=True)
    token_end: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.end_sec <= self.start_sec or self.token_end <= self.token_start:
            raise ValueError("cue intervals must be increasing")
        return self


class CaptionTimingInput(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    source_sha256: Sha
    copy_master_id: Text
    copy_hash: Sha
    processed_audio_sha256: Sha
    timebase: Literal["source_mp4"]
    origin: Literal["manual", "external_alignment"]
    accepted_by: Text
    cues: list[CaptionTimingCue] = Field(min_length=1)
    _operator = field_validator("accepted_by")(operator)


class ResolvedCaptionCue(CaptionTimingCue):
    text: Text


class CaptionInput(EditingModel):
    copy_ref: CopyRef
    voice_ref: IdentityRef
    text: Text
    timing_status: Literal["missing", "provided"]
    timing_ref: AssetRef | None = None
    origin: Literal["manual", "external_alignment"] | None = None
    accepted_by: Text | None = None
    timebase: Literal["source_mp4"] | None = None
    cues: list[ResolvedCaptionCue] = Field(default_factory=list)

    @field_validator("accepted_by")
    @classmethod
    def accepted_operator(cls, value: str | None) -> str | None:
        return operator(value) if value is not None else None

    @model_validator(mode="after")
    def timing_consistency(self) -> Self:
        metadata = (self.timing_ref, self.origin, self.accepted_by, self.timebase)
        if self.timing_status == "missing":
            if any(v is not None for v in metadata) or self.cues:
                raise ValueError("missing timing cannot contain cues or metadata")
        elif any(v is None for v in metadata) or not self.cues:
            raise ValueError("provided timing requires cues and provenance")
        return self


class EditPlannedOutput(EditingModel):
    key: Identifier
    label: Text
    output_variant_id: Identifier
    manifest: EditManifestV2
    manifest_hash: Sha
    output_hash: Sha
    filename: Text
    caption_state: Literal["disabled", "timing_missing", "timing_provided"]
    _ids = field_validator("key", "output_variant_id")(safe_id)


class EditBatchPlan(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    planner_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    render_id: Identifier
    video_id: Identifier
    source: SourceVideoRef
    copy_ref: CopyRef
    voice_ref: IdentityRef
    image_ref: IdentityRef
    max_outputs: int = Field(gt=0, strict=True)
    output_count: int = Field(gt=0, strict=True)
    caption_input: CaptionInput
    outputs: list[EditPlannedOutput] = Field(min_length=1)
    plan_hash: Sha
    _ids = field_validator("campaign_id", "render_id", "video_id")(safe_id)


@dataclass
class BatchInputs:
    source: SourceVideoRef
    copy: CopyMaster
    voice_ref: IdentityRef
    image_ref: IdentityRef
