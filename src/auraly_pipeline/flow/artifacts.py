"""Validated, workspace-contained publication of Flow image downloads."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import struct
import warnings
from dataclasses import dataclass
from typing import BinaryIO
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from PIL import Image, UnidentifiedImageError


_MAX_ARTIFACT_BYTES = 100_000_000
_MAX_ARTIFACT_PIXELS = 100_000_000
_HASH_CHUNK_SIZE = 1024 * 1024
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_FORMAT_SUFFIXES = {"png": ".png", "jpeg": ".jpeg", "webp": ".webp"}
_PILLOW_FORMATS = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}
_EXTENSION_FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}


class FlowArtifactInvalidError(RuntimeError):
    """Raised when an artifact or its workspace path cannot be trusted."""


class FlowArtifactConflictError(RuntimeError):
    """Raised when exclusive publication cannot preserve existing evidence."""


@dataclass(frozen=True)
class FlowArtifactFacts:
    """The immutable facts recorded for one validated Flow image artifact."""

    format: str
    width: int
    height: int
    size_bytes: int
    sha256: str


def allocate_flow_staging_path(
    *,
    work_root: Path,
    campaign_id: str,
    scene_variant_id: str,
    generation_number: int,
    candidate_index: int,
) -> Path:
    """Reserve one neutral staging file beside its canonical candidate directory."""
    candidate_dir = _candidate_directory(
        work_root=work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        generation_number=generation_number,
        candidate_index=candidate_index,
    )
    staging_dir = candidate_dir / ".staging"
    _make_directory(staging_dir, _canonical_root(work_root))
    while True:
        staging = staging_dir / f"{uuid4().hex}.part"
        try:
            with staging.open("xb"):
                pass
        except FileExistsError:
            continue
        except OSError as exc:
            raise FlowArtifactInvalidError("unable to allocate Flow staging artifact") from exc
        return _contained_path(staging, _canonical_root(work_root))


def resolve_flow_final_path(
    *,
    work_root: Path,
    campaign_id: str,
    scene_variant_id: str,
    generation_number: int,
    candidate_index: int,
    image_format: str,
) -> Path:
    """Return the normalized, canonical destination for a validated candidate."""
    try:
        suffix = _FORMAT_SUFFIXES[image_format]
    except KeyError as exc:
        raise FlowArtifactInvalidError("unsupported Flow image format") from exc
    candidate_dir = _candidate_directory(
        work_root=work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        generation_number=generation_number,
        candidate_index=candidate_index,
    )
    return _contained_path(candidate_dir / f"candidate-{candidate_index:04d}{suffix}", _canonical_root(work_root))


def inspect_flow_artifact(path: Path) -> FlowArtifactFacts:
    """Fully decode a bounded image and return facts only for valid 2K artifacts."""
    if _path_is_link_or_junction(path) or not path.is_file():
        raise FlowArtifactInvalidError("artifact is not a regular file")
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to stat artifact") from exc
    if size_bytes <= 0 or size_bytes > _MAX_ARTIFACT_BYTES:
        raise FlowArtifactInvalidError("artifact size is outside the permitted range")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            old_max_pixels = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = _MAX_ARTIFACT_PIXELS
            try:
                with Image.open(path) as image:
                    image.verify()
                    pillow_format = image.format
                with Image.open(path) as image:
                    image.load()
                    width, height = image.size
                    if image.format != pillow_format:
                        raise FlowArtifactInvalidError("artifact format changed while decoding")
            finally:
                Image.MAX_IMAGE_PIXELS = old_max_pixels
    except (Image.DecompressionBombError, OSError, SyntaxError, UnidentifiedImageError) as exc:
        raise FlowArtifactInvalidError("artifact does not fully decode") from exc
    except Image.DecompressionBombWarning as exc:
        raise FlowArtifactInvalidError("artifact exceeds the pixel limit") from exc

    try:
        image_format = _PILLOW_FORMATS[pillow_format or ""]
    except KeyError as exc:
        raise FlowArtifactInvalidError("artifact format is unsupported") from exc
    if width <= 0 or height <= 0 or max(width, height) < 2048:
        raise FlowArtifactInvalidError("artifact does not meet the 2K requirement")
    _validate_extension(path, image_format)
    _validate_container(path, image_format, size_bytes)
    digest = _sha256(path)
    try:
        final_size = path.stat().st_size
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to re-stat artifact") from exc
    if final_size != size_bytes:
        raise FlowArtifactInvalidError("artifact changed during inspection")
    return FlowArtifactFacts(
        format=image_format,
        width=width,
        height=height,
        size_bytes=size_bytes,
        sha256=digest,
    )


def publish_flow_artifact_exclusive(
    staging_path: Path,
    final_path: Path,
    *,
    trusted_root: Path,
) -> FlowArtifactFacts:
    """Hard-link a staged artifact into place without an overwrite or copy fallback."""
    root = _canonical_root(trusted_root)
    staging = _contained_path(staging_path, root)
    final = _contained_path(final_path, root)
    expected_staging = final.parent / ".staging"
    if staging.parent != expected_staging or staging.suffix != ".part":
        raise FlowArtifactInvalidError("staging path is outside the candidate staging directory")
    _make_directory(final.parent, root)
    _contained_path(staging, root)
    _contained_path(final, root)
    staged_facts = inspect_flow_artifact(staging)
    if final.suffix.lower() != _FORMAT_SUFFIXES[staged_facts.format]:
        raise FlowArtifactInvalidError("final artifact suffix does not match staged bytes")

    try:
        os.link(staging, final)
    except FileExistsError:
        return _recover_matching_final(staging, final, staged_facts)
    except OSError as exc:
        raise FlowArtifactConflictError("exclusive Flow artifact publication failed") from exc

    try:
        final_facts = inspect_flow_artifact(final)
        if final_facts != staged_facts:
            raise FlowArtifactConflictError("published artifact differs from staging evidence")
        _sync_file_and_directory(final)
        os.unlink(staging)
        _sync_directory(final.parent)
    except FlowArtifactConflictError:
        raise
    except OSError as exc:
        raise FlowArtifactConflictError("Flow artifact publication could not be finalized") from exc
    return staged_facts


def _recover_matching_final(
    staging: Path, final: Path, staged_facts: FlowArtifactFacts
) -> FlowArtifactFacts:
    try:
        final_facts = inspect_flow_artifact(final)
    except FlowArtifactInvalidError as exc:
        raise FlowArtifactConflictError("existing final artifact is invalid") from exc
    if final_facts != staged_facts:
        raise FlowArtifactConflictError("existing final artifact conflicts with staging evidence")
    try:
        os.unlink(staging)
        _sync_directory(final.parent)
    except OSError as exc:
        raise FlowArtifactConflictError("matching artifact residue could not be finalized") from exc
    return final_facts


def _candidate_directory(
    *,
    work_root: Path,
    campaign_id: str,
    scene_variant_id: str,
    generation_number: int,
    candidate_index: int,
) -> Path:
    _validate_identifier(campaign_id)
    _validate_identifier(scene_variant_id)
    if isinstance(generation_number, bool) or generation_number < 1:
        raise FlowArtifactInvalidError("generation number must be positive")
    if isinstance(candidate_index, bool) or candidate_index not in {0, 1}:
        raise FlowArtifactInvalidError("candidate index must select one of two Flow slots")
    root = _canonical_root(work_root)
    candidate_dir = root / "campaigns" / campaign_id / "images" / scene_variant_id / (
        f"generation-{generation_number:04d}"
    )
    _make_directory(candidate_dir, root)
    return _contained_path(candidate_dir, root)


def _validate_identifier(value: str) -> None:
    windows = PureWindowsPath(value)
    if (
        not value
        or "\x00" in value
        or Path(value).is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or not _SAFE_IDENTIFIER.fullmatch(value)
    ):
        raise FlowArtifactInvalidError("Flow artifact identifier is unsafe")


def _canonical_root(work_root: Path) -> Path:
    try:
        if _path_is_link_or_junction(work_root):
            raise FlowArtifactInvalidError("trusted Flow work root cannot be a link")
        root = work_root.resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FlowArtifactInvalidError("trusted Flow work root is unavailable") from exc
    return root


def _make_directory(path: Path, root: Path) -> None:
    _contained_path(path, root)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to create Flow artifact directory") from exc
    _contained_path(path, root)


def _contained_path(path: Path, root: Path) -> Path:
    if any(part == ".." for part in path.parts):
        raise FlowArtifactInvalidError("Flow artifact path contains traversal")
    try:
        lexical = path if path.is_absolute() else root / path
        lexical_relative = lexical.relative_to(root)
    except ValueError as exc:
        raise FlowArtifactInvalidError("Flow artifact path escapes the trusted root") from exc
    cursor = root
    for component in lexical_relative.parts:
        cursor /= component
        if cursor.exists() or cursor.is_symlink():
            if _path_is_link_or_junction(cursor):
                raise FlowArtifactInvalidError("Flow artifact path contains a link or junction")
    try:
        resolved = lexical.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise FlowArtifactInvalidError("Flow artifact path escapes the trusted root") from exc
    cursor = root
    for component in relative.parts:
        cursor /= component
        if cursor.exists() or cursor.is_symlink():
            if _path_is_link_or_junction(cursor):
                raise FlowArtifactInvalidError("Flow artifact path contains a link or junction")
    return resolved


def _path_is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _validate_extension(path: Path, image_format: str) -> None:
    expected = _EXTENSION_FORMATS.get(path.suffix.lower())
    if expected is not None and expected != image_format:
        raise FlowArtifactInvalidError("artifact extension does not match bytes")
    if expected is None and path.suffix.lower() not in {"", ".part"}:
        raise FlowArtifactInvalidError("artifact extension is unsupported")


def _validate_container(path: Path, image_format: str, size_bytes: int) -> None:
    try:
        with path.open("rb") as stream:
            if image_format == "png":
                _validate_png_container(stream, size_bytes)
            elif image_format == "jpeg":
                _validate_jpeg_container(stream, size_bytes)
            else:
                _validate_webp_container(stream, size_bytes)
    except (OSError, ValueError, struct.error) as exc:
        raise FlowArtifactInvalidError("artifact container is malformed") from exc


def _validate_png_container(stream: BinaryIO, size_bytes: int) -> None:
    if stream.read(8) != b"\x89PNG\r\n\x1a\n":
        raise ValueError
    offset = 8
    while offset < size_bytes:
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError
        length = struct.unpack(">I", header[:4])[0]
        offset += 8
        if length > size_bytes - offset - 4:
            raise ValueError
        stream.seek(length, os.SEEK_CUR)
        crc = stream.read(4)
        if len(crc) != 4:
            raise ValueError
        offset += length + 4
        if header[4:] == b"IEND":
            if length != 0 or offset != size_bytes:
                raise ValueError
            return
    raise ValueError


def _validate_jpeg_container(stream: BinaryIO, size_bytes: int) -> None:
    if size_bytes < 4 or stream.read(2) != b"\xff\xd8":
        raise ValueError
    stream.seek(-2, os.SEEK_END)
    if stream.read(2) != b"\xff\xd9":
        raise ValueError


def _validate_webp_container(stream: BinaryIO, size_bytes: int) -> None:
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP":
        raise ValueError
    declared_size = struct.unpack("<I", header[4:8])[0]
    if declared_size + 8 != size_bytes:
        raise ValueError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to hash artifact") from exc
    return digest.hexdigest()


def _sync_file_and_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDWR)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
            raise
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EPERM, errno.EACCES}:
            raise
