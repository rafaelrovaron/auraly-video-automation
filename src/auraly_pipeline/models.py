from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


def _workspace_path(value: str | None) -> str | None:
    if value is None:
        return None
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if not value or posix.is_absolute() or windows.is_absolute() or ".." in posix.parts:
        raise ValueError("expected a workspace-relative path")
    return value


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ProjectSpec(ContractModel):
    reel_id: str
    character: Literal["susan-smith", "soul-constellation"]
    template: str


class SourceSpec(ContractModel):
    video: str
    copy_path: str = Field(alias="copy")
    transcript: str | None = None
    duration_sec: float = Field(gt=0)

    _validate_paths = field_validator("video", "copy_path", "transcript")(_workspace_path)


class CanvasSpec(ContractModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)


class HeadlineSpec(ContractModel):
    text: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    spoken: bool = Field(default=False, json_schema_extra={"const": False})

    @field_validator("spoken")
    @classmethod
    def require_visual_only(cls, value: bool) -> bool:
        if value:
            raise ValueError("headline must remain visual-only")
        return value


class CutSpec(ContractModel):
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    action: Literal["keep", "remove"]
    reason: str | None = None


class WordSpec(ContractModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class CaptionSpec(ContractModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    words: list[WordSpec] = Field(default_factory=list)


class PunchInSpec(ContractModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    scale: float = Field(gt=1, le=1.25)
    reason: str | None = None


class BrollSpec(ContractModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    asset: str
    license: str = Field(min_length=1)
    presentation: Literal["full-screen", "picture-in-picture", "overlay"] = "full-screen"
    reason: str | None = None

    _validate_asset = field_validator("asset")(_workspace_path)


class MusicSpec(ContractModel):
    asset: str | None = None
    volume_db: float = -22
    duck_under_voice_db: float = -8
    fade_in_sec: float = Field(default=0.4, ge=0)
    fade_out_sec: float = Field(default=1.0, ge=0)


class ReviewSpec(ContractModel):
    status: Literal["draft", "pending", "changes_requested", "approved", "rendered"] = "draft"
    warnings: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: str | None = None

    @model_validator(mode="after")
    def require_human_approval(self) -> Self:
        if self.status in {"approved", "rendered"} and not (
            self.approved_by and self.approved_at
        ):
            raise ValueError("human approval is required before final render")
        return self


class OutputSpec(ContractModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    format: Literal["mp4"] = "mp4"
    codec: Literal["h264"] = "h264"


class EditManifest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    project: ProjectSpec
    source: SourceSpec
    canvas: CanvasSpec
    headline: HeadlineSpec
    cuts: list[CutSpec] = Field(default_factory=list)
    captions: list[CaptionSpec] = Field(default_factory=list)
    punch_ins: list[PunchInSpec] = Field(default_factory=list)
    broll: list[BrollSpec] = Field(default_factory=list)
    music: MusicSpec = Field(default_factory=MusicSpec)
    render: OutputSpec
    review: ReviewSpec = Field(default_factory=ReviewSpec)

    @model_validator(mode="after")
    def require_events_within_source(self) -> Self:
        duration = self.source.duration_sec
        events: list[HeadlineSpec | CaptionSpec | PunchInSpec | BrollSpec] = [
            self.headline,
            *self.captions,
            *self.punch_ins,
            *self.broll,
        ]
        if any(event.end <= event.start for event in events):
            raise ValueError("timeline event end must be after start")
        if any(cut.source_end <= cut.source_start for cut in self.cuts):
            raise ValueError("cut end must be after start")
        if any(event.end > duration for event in events):
            raise ValueError("timeline event exceeds source duration")
        if any(cut.source_end > duration for cut in self.cuts):
            raise ValueError("cut exceeds source duration")
        return self
