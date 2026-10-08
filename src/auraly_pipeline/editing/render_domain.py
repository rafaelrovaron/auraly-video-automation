from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from auraly_pipeline.editing.batch_domain import CaptionInput
from auraly_pipeline.editing.domain import (
    EditManifestV2, EditingError, EditingModel, Identifier, Sha, relative_path, safe_id,
)
from auraly_pipeline.editing.resolver import content_hash
from auraly_pipeline.probe import MediaProbe


class RenderRuntime(EditingModel):
    fingerprint: Sha
    ffmpeg_version: str
    libass_version: str
    encoding: dict[str, str]


def render_key(output_hash: str, runtime: RenderRuntime) -> str:
    return content_hash({"outputHash": output_hash, "rendererVersion": "1.0",
                         "runtimeFingerprint": runtime.fingerprint})


class RenderReceipt(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    renderer_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    video_id: Identifier
    output_variant_id: Identifier
    plan_hash: Sha
    manifest_hash: Sha
    output_hash: Sha
    render_key: Sha
    runtime: RenderRuntime
    inputs: dict[Literal["source", "headlineFont", "captionsFont", "music", "timing"], Sha]
    mix_policy: Literal["source_copy", "fixed_duck_mix"]
    path: str
    size_bytes: int = Field(gt=0, strict=True)
    sha256: Sha
    probe: MediaProbe
    full_decode_passed: Literal[True] = True
    _ids = field_validator("campaign_id", "video_id", "output_variant_id")(safe_id)
    _path = field_validator("path")(relative_path)

    @model_validator(mode="after")
    def key_matches(self) -> Self:
        if self.render_key != render_key(self.output_hash, self.runtime) or "source" not in self.inputs:
            raise ValueError("receipt identity mismatch")
        return self


class RenderError(EditingModel):
    field: str
    message: str


class RenderOutputResult(EditingModel):
    key: Identifier
    output_variant_id: Identifier
    status: Literal["rendered", "reused", "failed", "planned"]
    render_key: Sha
    fit_measured: bool = False
    path: str | None = None
    error: RenderError | None = None
    _ids = field_validator("key", "output_variant_id")(safe_id)

    @field_validator("path")
    @classmethod
    def path_relative(cls, value: str | None) -> str | None:
        return relative_path(value) if value is not None else None

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("error required only for failed output")
        if self.status in {"rendered", "reused"} and (self.path is None or not self.fit_measured):
            raise ValueError("completed output requires path and measured fit")
        return self


class RenderBatchResult(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_hash: Sha
    dry_run: bool
    outputs: list[RenderOutputResult]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_failures(self) -> bool:
        return any(o.status == "failed" for o in self.outputs)


def validate_supported(manifest: EditManifestV2, caption: CaptionInput) -> None:
    if (manifest.output.width, manifest.output.height, manifest.output.fps) != (1080, 1920, 30):
        raise EditingError("output", "renderer requires 1080x1920 at 30 FPS")
    if manifest.captions.enabled:
        if caption.timing_status != "provided":
            raise EditingError("captionInput", "accepted caption timing required")
        if manifest.captions.highlight_enabled:
            raise EditingError("captions.highlightEnabled", "word highlight is not supported")
    for field, style in (("headline", manifest.headline), ("captions", manifest.captions)):
        if style.enabled and style.font_weight not in {400, 700}:
            raise EditingError(field + ".fontWeight", "renderer supports weights 400 and 700")
