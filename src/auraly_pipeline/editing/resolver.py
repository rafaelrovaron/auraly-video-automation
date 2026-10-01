from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from auraly_pipeline.editing.domain import (
    EditManifestV2, EditProfile, EditResolveRequest, EditingError, Origin,
)


def content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def profile_hash(profile: EditProfile) -> str:
    return content_hash(profile.model_dump(mode="json", by_alias=True, exclude={"created_at"}))


def resolve_manifest(profile: EditProfile, request: EditResolveRequest) -> EditManifestV2:
    profile = EditProfile.model_validate(profile.model_dump(mode="json", by_alias=True))
    request = EditResolveRequest.model_validate(request.model_dump(mode="json", by_alias=True))
    ref = request.profile_ref
    if (ref.profile_id, ref.version, ref.hash) != (profile.profile_id, profile.version, profile_hash(profile)):
        raise EditingError("profileRef", "profile identity/version/hash mismatch")
    sections = profile.defaults.model_dump(mode="json", by_alias=True)
    provenance: dict[str, Origin] = {
        f"{section}.{field}": "profile" for section, values in sections.items() for field in values
    }
    sections["headline"]["text"] = request.headline_text
    provenance["headline.text"] = "input"
    layers = {"campaign": request.campaign, "video": request.video,
              "outputVariant": request.output_variant}
    for layer, overrides in layers.items():
        for section, values in overrides.model_dump(mode="json", by_alias=True).items():
            for field, value in values.items():
                sections[section][field] = value
                provenance[f"{section}.{field}"] = layer  # type: ignore[assignment]
    if sections["headline"]["endSec"] is None:
        sections["headline"]["endSec"] = request.source.duration_sec
        provenance["headline.endSec"] = "source"
    sections["headline"]["spoken"] = False
    provenance["headline.spoken"] = "profile"

    def reject(field: str, message: str) -> None:
        raise EditingError(field, message, provenance.get(field))

    for section in ("headline", "captions"):
        style = sections[section]
        if style["enabled"] and style["font"] is None:
            reject(f"{section}.font", "local font required for enabled text")
        for a, b in (("safeLeft", "safeRight"), ("safeTop", "safeBottom")):
            if style[a] + style[b] >= 1:
                reject(f"{section}.{b}", "safe zones leave no canvas area")
    headline = sections["headline"]
    if not headline["startSec"] < headline["endSec"] <= request.source.duration_sec:
        reject("headline.endSec", "interval must be within source duration and after start")
    music = sections["music"]
    if music["enabled"] and music["asset"] is None:
        reject("music.asset", "local music asset required")
    if music["enabled"] and not request.music_accepted:
        raise EditingError("musicAccepted", "explicit operator acceptance required")
    if music["trimEndSec"] is not None and music["trimEndSec"] <= music["trimStartSec"]:
        reject("music.trimEndSec", "trim end must be after start")
    if music["enabled"]:
        for field in ("fadeInSec", "fadeOutSec"):
            if music[field] > request.source.duration_sec:
                reject(f"music.{field}", "fade exceeds output duration")
    payload = {
        "schemaVersion": "2.0", "resolverVersion": "1.0",
        "campaignId": request.campaign_id, "videoId": request.video_id,
        "outputVariantId": request.output_variant_id,
        "source": request.source.model_dump(mode="json", by_alias=True),
        "profileRef": ref.model_dump(mode="json", by_alias=True),
        "copyRef": request.copy_ref.model_dump(mode="json", by_alias=True) if request.copy_ref else None,
        "voiceRef": request.voice_ref.model_dump(mode="json", by_alias=True) if request.voice_ref else None,
        "musicAccepted": request.music_accepted, **sections,
        "overrides": {key: val.model_dump(mode="json", by_alias=True) for key, val in layers.items()},
        "provenance": provenance,
    }
    try:
        # Normalize through the final contract before hashing (e.g. numeric floats).
        manifest = EditManifestV2.model_validate({**payload, "manifestHash": "0" * 64})
    except ValidationError as exc:
        field = ".".join(str(part) for part in exc.errors()[0]["loc"])
        raise EditingError(field, "invalid resolved field", provenance.get(field)) from None
    manifest.manifest_hash = content_hash(manifest.model_dump(mode="json", by_alias=True, exclude={"manifest_hash"}))
    return manifest


def verify_manifest_hash(manifest: EditManifestV2) -> None:
    validated = EditManifestV2.model_validate(manifest.model_dump(mode="json", by_alias=True))
    actual = content_hash(validated.model_dump(mode="json", by_alias=True, exclude={"manifest_hash"}))
    if actual != validated.manifest_hash:
        raise EditingError("manifestHash", "manifest content mismatch")
