from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from auraly_pipeline.models import ContractModel


Number = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[Number, Field(gt=0)]
Nonnegative = Annotated[Number, Field(ge=0)]
Fraction = Annotated[Number, Field(ge=0, le=1)]
Scale = Annotated[Number, Field(ge=1, le=1.25)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Text = Annotated[str, Field(min_length=1)]
Origin = Literal["profile", "input", "source", "campaign", "video", "outputVariant"]


class EditingError(ValueError):
    def __init__(self, field: str, message: str, layer: str | None = None) -> None:
        self.field = field
        self.layer = layer
        super().__init__(f"{layer + '.' if layer else ''}{field}: {message}")


def _omitted() -> Any:
    """Internal absent value, never serialized as an explicit partial field."""
    return None


def safe_id(value: str) -> str:
    import re

    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) or value in reserved:
        raise ValueError("invalid identifier")
    return value


def relative_path(value: str) -> str:
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if (not value or "\x00" in value or ":" in value or "\\" in value
            or posix.is_absolute() or windows.is_absolute() or windows.drive
            or any(part in {"..", ".", ""} for part in value.split("/"))
            or any(part.rstrip(" .") != part or PureWindowsPath(part).is_reserved()
                   for part in posix.parts)):
        raise ValueError("expected safe project-relative POSIX path")
    return value


class EditingModel(ContractModel):
    model_config = ConfigDict(allow_inf_nan=False, revalidate_instances="always")

    @field_validator("*", mode="after")
    @classmethod
    def public_strings(cls, value: object) -> object:
        if isinstance(value, str) and ("\x00" in value or not value.strip()):
            raise ValueError("nonempty public text required")
        return value


class AssetRef(EditingModel):
    path: str
    sha256: Sha
    _path = field_validator("path")(relative_path)


class SourceVideoRef(AssetRef):
    id: Identifier
    duration_sec: Positive
    _id = field_validator("id")(safe_id)


class IdentityRef(EditingModel):
    id: Text
    hash: Sha


class ProfileRef(EditingModel):
    profile_id: Identifier
    version: int = Field(gt=0)
    hash: Sha
    _id = field_validator("profile_id")(safe_id)


class TextStyle(EditingModel):
    enabled: bool = False
    style_id: Text = "plain"
    font: AssetRef | None = None
    font_weight: int = Field(default=700, ge=100, le=900)
    font_size_px: Positive = 60
    line_height: Positive = 1.1
    color: Color = "#FFFFFF"
    stroke_width_px: Nonnegative = 0
    stroke_color: Color = "#000000"
    shadow_enabled: bool = False
    shadow_color: Color = "#000000"
    shadow_offset_x: Number = 0
    shadow_offset_y: Number = 0
    background_enabled: bool = False
    background_color: Color = "#000000"
    background_padding_px: Nonnegative = 0
    anchor: Literal["top", "center", "bottom"] = "top"
    x: Fraction = 0.5
    y: Fraction = 0.1
    safe_top: Fraction = 0.05
    safe_right: Fraction = 0.05
    safe_bottom: Fraction = 0.05
    safe_left: Fraction = 0.05
    max_lines: int = Field(default=3, gt=0)
    fit_policy: Literal["wrap", "shrink", "error"] = "wrap"

    @model_validator(mode="after")
    def safe_zones(self) -> Self:
        if self.safe_left + self.safe_right >= 1 or self.safe_top + self.safe_bottom >= 1:
            raise ValueError("safe zones leave no canvas area")
        return self


class HeadlineStyle(TextStyle):
    start_sec: Nonnegative = 0
    end_sec: Positive | None = None


class CaptionStyle(TextStyle):
    anchor: Literal["top", "center", "bottom"] = "bottom"
    y: Fraction = 0.8
    highlight_enabled: bool = False
    highlight_color: Color = "#FFFF00"


class ResolvedHeadline(HeadlineStyle):
    text: Text
    end_sec: Positive
    spoken: Literal[False] = False


class OutputStyle(EditingModel):
    width: int = Field(default=1080, gt=0)
    height: int = Field(default=1920, gt=0)
    fps: Positive = 30
    format: Literal["mp4"] = "mp4"
    codec: Literal["h264"] = "h264"


class MusicStyle(EditingModel):
    enabled: bool = False
    asset: AssetRef | None = None
    volume_db: Number = -22
    duck_under_voice_db: Number = -8
    loop: bool = True
    trim_start_sec: Nonnegative = 0
    trim_end_sec: Positive | None = None
    fade_in_sec: Nonnegative = 0.4
    fade_out_sec: Nonnegative = 1


class FramingStyle(EditingModel):
    fit: Literal["cover", "contain"] = "cover"
    scale: Scale = 1
    x: Fraction = 0.5
    y: Fraction = 0.5
    zoom_start: Scale = 1
    zoom_end: Scale = 1


# Omitted partial fields have unvalidated None defaults, but explicit null is
# rejected by non-nullable annotations. Always serialize partials exclude_unset.
class TextOverride(EditingModel):
    model_config = ConfigDict(revalidate_instances="never")
    enabled: bool = Field(default_factory=_omitted)
    style_id: Text = Field(default_factory=_omitted)
    font: AssetRef | None = None
    font_weight: int = Field(default_factory=_omitted, ge=100, le=900)
    font_size_px: Positive = Field(default_factory=_omitted)
    line_height: Positive = Field(default_factory=_omitted)
    color: Color = Field(default_factory=_omitted)
    stroke_width_px: Nonnegative = Field(default_factory=_omitted)
    stroke_color: Color = Field(default_factory=_omitted)
    shadow_enabled: bool = Field(default_factory=_omitted)
    shadow_color: Color = Field(default_factory=_omitted)
    shadow_offset_x: Number = Field(default_factory=_omitted)
    shadow_offset_y: Number = Field(default_factory=_omitted)
    background_enabled: bool = Field(default_factory=_omitted)
    background_color: Color = Field(default_factory=_omitted)
    background_padding_px: Nonnegative = Field(default_factory=_omitted)
    anchor: Literal["top", "center", "bottom"] = Field(default_factory=_omitted)
    x: Fraction = Field(default_factory=_omitted)
    y: Fraction = Field(default_factory=_omitted)
    safe_top: Fraction = Field(default_factory=_omitted)
    safe_right: Fraction = Field(default_factory=_omitted)
    safe_bottom: Fraction = Field(default_factory=_omitted)
    safe_left: Fraction = Field(default_factory=_omitted)
    max_lines: int = Field(default_factory=_omitted, gt=0)
    fit_policy: Literal["wrap", "shrink", "error"] = Field(default_factory=_omitted)


class HeadlineOverride(TextOverride):
    text: Text = Field(default_factory=_omitted)
    start_sec: Nonnegative = Field(default_factory=_omitted)
    end_sec: Positive | None = None


class CaptionOverride(TextOverride):
    highlight_enabled: bool = Field(default_factory=_omitted)
    highlight_color: Color = Field(default_factory=_omitted)


class MusicOverride(EditingModel):
    model_config = ConfigDict(revalidate_instances="never")
    enabled: bool = Field(default_factory=_omitted)
    asset: AssetRef | None = None
    volume_db: Number = Field(default_factory=_omitted)
    duck_under_voice_db: Number = Field(default_factory=_omitted)
    loop: bool = Field(default_factory=_omitted)
    trim_start_sec: Nonnegative = Field(default_factory=_omitted)
    trim_end_sec: Positive | None = None
    fade_in_sec: Nonnegative = Field(default_factory=_omitted)
    fade_out_sec: Nonnegative = Field(default_factory=_omitted)


class FramingOverride(EditingModel):
    model_config = ConfigDict(revalidate_instances="never")
    fit: Literal["cover", "contain"] = Field(default_factory=_omitted)
    scale: Scale = Field(default_factory=_omitted)
    x: Fraction = Field(default_factory=_omitted)
    y: Fraction = Field(default_factory=_omitted)
    zoom_start: Scale = Field(default_factory=_omitted)
    zoom_end: Scale = Field(default_factory=_omitted)


class OutputOverride(EditingModel):
    model_config = ConfigDict(revalidate_instances="never")
    width: int = Field(default_factory=_omitted, gt=0)
    height: int = Field(default_factory=_omitted, gt=0)
    fps: Positive = Field(default_factory=_omitted)
    format: Literal["mp4"] = Field(default_factory=_omitted)
    codec: Literal["h264"] = Field(default_factory=_omitted)


class EditOverrides(EditingModel):
    output: OutputOverride = Field(default_factory=OutputOverride)
    headline: HeadlineOverride = Field(default_factory=HeadlineOverride)
    captions: CaptionOverride = Field(default_factory=CaptionOverride)
    music: MusicOverride = Field(default_factory=MusicOverride)
    framing: FramingOverride = Field(default_factory=FramingOverride)


class EditDefaults(EditingModel):
    output: OutputStyle = Field(default_factory=OutputStyle)
    headline: HeadlineStyle = Field(default_factory=HeadlineStyle)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    music: MusicStyle = Field(default_factory=MusicStyle)
    framing: FramingStyle = Field(default_factory=FramingStyle)


class EditProfile(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    profile_id: Identifier
    name: Text
    version: int = Field(gt=0)
    created_at: AwareDatetime
    defaults: EditDefaults
    _id = field_validator("profile_id")(safe_id)


class EditResolveRequest(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    profile_ref: ProfileRef
    campaign_id: Identifier
    video_id: Identifier
    output_variant_id: Identifier
    source: SourceVideoRef
    headline_text: Text
    copy_ref: IdentityRef | None = None
    voice_ref: IdentityRef | None = None
    music_accepted: bool = False
    campaign: EditOverrides = Field(default_factory=EditOverrides)
    video: EditOverrides = Field(default_factory=EditOverrides)
    output_variant: EditOverrides = Field(default_factory=EditOverrides)
    _ids = field_validator("campaign_id", "video_id", "output_variant_id")(safe_id)


class EditManifestV2(EditingModel):
    schema_version: Literal["2.0"] = "2.0"
    resolver_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    video_id: Identifier
    output_variant_id: Identifier
    source: SourceVideoRef
    profile_ref: ProfileRef
    copy_ref: IdentityRef | None = None
    voice_ref: IdentityRef | None = None
    music_accepted: bool
    output: OutputStyle
    headline: ResolvedHeadline
    captions: CaptionStyle
    music: MusicStyle
    framing: FramingStyle
    overrides: dict[Literal["campaign", "video", "outputVariant"], EditOverrides]
    provenance: dict[str, Origin]
    manifest_hash: Sha
    _ids = field_validator("campaign_id", "video_id", "output_variant_id")(safe_id)
