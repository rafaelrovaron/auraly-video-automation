from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from typing import Any

from pydantic import TypeAdapter, ValidationError

from auraly_pipeline.editing.domain import (
    AssetRef, EditManifestV2, EditProfile, EditResolveRequest, EditingError, Sha, safe_id,
)
from auraly_pipeline.editing.resolver import profile_hash, resolve_manifest, verify_manifest_hash
from auraly_pipeline.probe import ProbeError, probe_media


def _safe_path(root: Path, path: Path) -> Path:
    root, path = root.absolute(), path.absolute()
    for current in (path, *path.parents, root, *root.parents):
        try:
            facts = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(facts.st_mode) or getattr(facts, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise EditingError("path", "links and reparse points are not supported")
    if not path.resolve().is_relative_to(root.resolve()):
        raise EditingError("path", "path escapes trusted root")
    return path


def validate_editing_path(root: Path, path: Path) -> Path:
    """Validate lexical trusted paths before callers canonicalize them."""
    return _safe_path(root, path)


def _read(root: Path, path: Path) -> dict[str, Any]:
    try:
        _safe_path(root, path)
        if not path.is_file():
            raise EditingError("artifact", "regular JSON file required")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise EditingError("artifact", "JSON object required")
        return payload
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise EditingError("artifact", "cannot read valid JSON artifact") from None


def _publish(root: Path, path: Path, payload: dict[str, Any]) -> None:
    _safe_path(root, path)
    if path.exists():
        if _read(root, path) != payload:
            raise EditingError("artifact", "existing content conflicts; no overwrite")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_path(root, path)
    staging: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".editing-", suffix=".part", dir=path.parent)
        staging = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _safe_path(root, path)
        try:
            os.link(staging, path)
        except FileExistsError:
            if _read(root, path) != payload:
                raise EditingError("artifact", "concurrent publication conflicts") from None
    except OSError:
        raise EditingError("artifact", "publication failed safely") from None
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


class EditingService:
    def __init__(self, *, project_root: Path, work_root: Path) -> None:
        self.project_root = _safe_path(project_root, project_root)
        self.work_root = _safe_path(work_root, work_root)

    def _profile_path(self, profile_id: str, version: int) -> Path:
        try:
            safe_id(profile_id)
            if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
                raise ValueError()
        except ValueError:
            raise EditingError("profileRef", "invalid profile identifier/version") from None
        return _safe_path(self.work_root, self.work_root / "editing" / "profiles" / profile_id / str(version) / "profile.json")

    def create_profile(self, profile: EditProfile) -> Path:
        profile = EditProfile.model_validate(profile.model_dump(mode="json", by_alias=True))
        path = self._profile_path(profile.profile_id, profile.version)
        for field, asset in (("headline.font", profile.defaults.headline.font),
                             ("captions.font", profile.defaults.captions.font)):
            if asset:
                self._font(asset, field)
        if profile.defaults.music.asset:
            self._audio_duration(profile.defaults.music.asset)
        if path.exists():
            existing = self.get_profile(profile.profile_id, profile.version)
            if profile_hash(existing) != profile_hash(profile):
                raise EditingError("profileRef", "published version conflicts")
            return path
        _publish(self.work_root, path, {"profile": profile.model_dump(mode="json", by_alias=True),
                                       "profileHash": profile_hash(profile)})
        return path

    def get_profile(self, profile_id: str, version: int) -> EditProfile:
        payload = _read(self.work_root, self._profile_path(profile_id, version))
        try:
            if set(payload) != {"profile", "profileHash"}:
                raise ValueError()
            profile = EditProfile.model_validate(payload["profile"])
            if ((profile.profile_id, profile.version) != (profile_id, version)
                    or profile_hash(profile) != payload["profileHash"]):
                raise ValueError()
            return profile
        except (ValidationError, ValueError, KeyError):
            raise EditingError("profileRef", "stored profile identity/content mismatch") from None

    def list_profiles(self) -> list[EditProfile]:
        root = _safe_path(self.work_root, self.work_root / "editing" / "profiles")
        profiles: list[EditProfile] = []
        for path in root.glob("*/*/profile.json"):
            try:
                version = int(path.parent.name)
            except ValueError:
                raise EditingError("profileRef", "invalid stored version") from None
            if str(version) != path.parent.name:
                raise EditingError("profileRef", "noncanonical stored version")
            profiles.append(self.get_profile(path.parent.parent.name, version))
        return sorted(profiles, key=lambda item: (item.profile_id, item.version))

    def create_profile_version(self, profile_id: str, base_version: int, replacement: EditProfile) -> Path:
        self.get_profile(profile_id, base_version)
        if replacement.profile_id != profile_id or replacement.version != base_version + 1:
            raise EditingError("profileRef", "replacement must be the next version of this profile")
        return self.create_profile(replacement)

    def _asset(self, asset: AssetRef, field: str) -> Path:
        try:
            path = _safe_path(self.project_root, self.project_root / asset.path)
            if not path.is_file():
                raise EditingError(field, "local regular asset required")
            with path.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            if sha != asset.sha256:
                raise EditingError(field + ".sha256", "asset content mismatch")
            return path
        except OSError:
            raise EditingError(field, "cannot read local asset") from None

    def _font(self, asset: AssetRef, field: str) -> None:
        path = self._asset(asset, field)
        try:
            with path.open("rb") as stream:
                header = stream.read(12)
                if (path.suffix.lower() not in {".ttf", ".otf"} or len(header) != 12
                        or header[:4] not in {b"\x00\x01\x00\x00", b"OTTO"}):
                    raise ValueError()
                count = struct.unpack(">H", header[4:6])[0]
                size = path.stat().st_size
                if count == 0 or 12 + count * 16 > size:
                    raise ValueError()
                tags: set[bytes] = set()
                for _ in range(count):
                    tag, _, offset, length = struct.unpack(">4sIII", stream.read(16))
                    if tag in tags or length == 0 or offset < 12 + count * 16 or offset + length > size:
                        raise ValueError()
                    tags.add(tag)
                if not {b"cmap", b"head", b"hhea", b"hmtx", b"maxp", b"name"} <= tags:
                    raise ValueError()
        except (OSError, ValueError, struct.error):
            raise EditingError(field, "valid local TTF/OTF font required") from None

    def _audio_duration(self, asset: AssetRef) -> float:
        path = self._asset(asset, "music.asset")
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams",
                "-show_format", "-of", "json", str(path),
            ], check=True, capture_output=True, timeout=30)
            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])
            if not data["streams"] or not math.isfinite(duration) or duration <= 0:
                raise ValueError()
            return duration
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            raise EditingError("music.asset", "valid local audio required") from None

    def _manifest_path(self, campaign_id: str, video_id: str, output_variant_id: str, manifest_hash: str) -> Path:
        try:
            for identifier in (campaign_id, video_id, output_variant_id):
                safe_id(identifier)
            TypeAdapter(Sha).validate_python(manifest_hash)
        except ValueError:
            raise EditingError("manifest", "invalid artifact identity") from None
        path = self.work_root / "campaigns" / campaign_id / "editing" / video_id / output_variant_id / manifest_hash / "manifest.json"
        return _safe_path(self.work_root, path)

    def resolve(self, request: EditResolveRequest, *, persist: bool = True) -> EditManifestV2:
        request = EditResolveRequest.model_validate(request.model_dump(mode="json", by_alias=True))
        profile = self.get_profile(request.profile_ref.profile_id, request.profile_ref.version)
        manifest = resolve_manifest(profile, request)
        source = self._asset(request.source, "source")
        try:
            probe = probe_media(source, timeout_seconds=30)
        except (ProbeError, OSError, ValueError, subprocess.SubprocessError):
            raise EditingError("source", "valid MP4 required") from None
        if (source.suffix.lower() != ".mp4" or "mp4" not in probe.format_name.split(",")
                or probe.video.codec != "h264" or probe.audio is None or probe.audio.codec != "aac"):
            raise EditingError("source", "H.264/AAC MP4 required")
        if not math.isfinite(probe.duration_sec) or abs(probe.duration_sec - request.source.duration_sec) > .01:
            raise EditingError("source.durationSec", "source duration mismatch")
        for field, style in (("headline", manifest.headline), ("captions", manifest.captions)):
            if style.font:
                self._font(style.font, field + ".font")
        if manifest.music.asset:
            duration = self._audio_duration(manifest.music.asset)
            end = manifest.music.trim_end_sec or duration
            if end > duration or manifest.music.trim_start_sec >= end:
                raise EditingError("music.trimEndSec", "trim exceeds audio duration")
            if manifest.music.enabled and max(manifest.music.fade_in_sec, manifest.music.fade_out_sec) > end - manifest.music.trim_start_sec:
                raise EditingError("music.fadeOutSec", "fade exceeds music segment")
        if persist:
            path = self._manifest_path(manifest.campaign_id, manifest.video_id, manifest.output_variant_id, manifest.manifest_hash)
            _publish(self.work_root, path, manifest.model_dump(mode="json", by_alias=True))
        return manifest

    def get_manifest(self, campaign_id: str, video_id: str, output_variant_id: str, manifest_hash: str) -> EditManifestV2:
        path = self._manifest_path(campaign_id, video_id, output_variant_id, manifest_hash)
        try:
            manifest = EditManifestV2.model_validate(_read(self.work_root, path))
            verify_manifest_hash(manifest)
            if (manifest.campaign_id, manifest.video_id, manifest.output_variant_id, manifest.manifest_hash) != (campaign_id, video_id, output_variant_id, manifest_hash):
                raise EditingError("manifest", "stored identity mismatch")
            return manifest
        except ValidationError:
            raise EditingError("manifest", "invalid stored manifest v2") from None
