"""Validated, workspace-contained publication of Flow image downloads."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import struct
import warnings
from pathlib import Path, PureWindowsPath
from dataclasses import dataclass
from typing import BinaryIO, Callable
from uuid import uuid4

from PIL import Image, UnidentifiedImageError


_MAX_ARTIFACT_BYTES = 100_000_000
_MAX_ARTIFACT_PIXELS = 100_000_000
_HASH_CHUNK_SIZE = 1024 * 1024
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_FORMAT_SUFFIXES = {"png": ".png", "jpeg": ".jpeg", "webp": ".webp"}
_PILLOW_FORMATS = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}
_EXTENSION_FORMATS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}

# Private deterministic race seam. Production leaves it unset.
_before_flow_artifact_link: Callable[[], None] | None = None
_after_flow_artifact_revalidation_before_link: Callable[[], None] | None = None
_after_flow_artifact_cleanup_revalidation: Callable[[], None] | None = None


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


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class _ArtifactSnapshot:
    facts: FlowArtifactFacts
    identity: _FileIdentity


@dataclass(frozen=True)
class _StagingCleanupBinding:
    staging_name: str
    parent_identity: _FileIdentity
    artifact_identity: _FileIdentity
    descriptor: int
    parent_descriptor: int
    windows_delete_handle: bool


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
    return _inspect_artifact(path).facts


def publish_flow_artifact_exclusive(
    staging_path: Path,
    final_path: Path,
    *,
    trusted_root: Path,
) -> FlowArtifactFacts:
    """Hard-link a staged artifact into place without an overwrite or copy fallback."""
    root = _canonical_root(trusted_root)
    root_identity = _directory_identity(root)
    staging = _contained_path(staging_path, root)
    final = _contained_path(final_path, root)
    expected_staging = final.parent / ".staging"
    if staging.parent != expected_staging or staging.suffix != ".part":
        raise FlowArtifactInvalidError("staging path is outside the candidate staging directory")
    _make_directory(final.parent, root)
    _contained_path(staging, root)
    _contained_path(final, root)
    staged = _inspect_artifact(staging, root=root, root_identity=root_identity)
    if final.suffix.lower() != _FORMAT_SUFFIXES[staged.facts.format]:
        raise FlowArtifactInvalidError("final artifact suffix does not match staged bytes")

    try:
        if _before_flow_artifact_link is not None:
            _before_flow_artifact_link()
        _assert_bound_artifact(staging, root, root_identity, staged.identity)
        _assert_root_identity(root, root_identity)
        _contained_path(final, root)
        if _after_flow_artifact_revalidation_before_link is not None:
            _after_flow_artifact_revalidation_before_link()
        os.link(staging, final)
    except FileExistsError:
        return _recover_matching_final(staging, final, staged, root, root_identity)
    except FlowArtifactInvalidError as exc:
        raise FlowArtifactConflictError("Flow artifact changed before publication") from exc
    except OSError as exc:
        raise FlowArtifactConflictError("exclusive Flow artifact publication failed") from exc

    try:
        _assert_bound_artifact(staging, root, root_identity, staged.identity)
        _assert_bound_artifact(final, root, root_identity, staged.identity)
        final = _contained_path(final, root)
        final_snapshot = _inspect_artifact(
            final,
            root=root,
            root_identity=root_identity,
            expected_identity=staged.identity,
        )
        if final_snapshot.facts != staged.facts:
            raise FlowArtifactConflictError("published artifact differs from staging evidence")
        _sync_file_and_directory(final)
        _assert_bound_artifact(final, root, root_identity, staged.identity)
        _assert_bound_artifact(staging, root, root_identity, staged.identity)
        cleanup = _bind_staging_cleanup(staging, root, root_identity, staged.identity)
        _run_cleanup_race_hook(cleanup)
        _finalize_bound_staging_cleanup(cleanup, staging.parent)
        _assert_root_identity(root, root_identity)
        _contained_path(final, root)
        _sync_directory(final.parent)
    except (FlowArtifactConflictError, FlowArtifactInvalidError) as exc:
        if isinstance(exc, FlowArtifactConflictError):
            raise
        raise FlowArtifactConflictError("Flow artifact changed during publication") from exc
    except OSError as exc:
        raise FlowArtifactConflictError("Flow artifact publication could not be finalized") from exc
    return staged.facts


def _recover_matching_final(
    staging: Path,
    final: Path,
    staged: _ArtifactSnapshot,
    root: Path,
    root_identity: _FileIdentity,
) -> FlowArtifactFacts:
    try:
        _assert_bound_artifact(staging, root, root_identity, staged.identity)
        final_snapshot = _inspect_artifact(
            final,
            root=root,
            root_identity=root_identity,
            expected_identity=staged.identity,
        )
    except FlowArtifactInvalidError as exc:
        raise FlowArtifactConflictError("existing final artifact is invalid") from exc
    if final_snapshot.facts != staged.facts:
        raise FlowArtifactConflictError("existing final artifact conflicts with staging evidence")
    try:
        _sync_file_and_directory(final)
        _assert_bound_artifact(final, root, root_identity, staged.identity)
        _assert_bound_artifact(staging, root, root_identity, staged.identity)
        cleanup = _bind_staging_cleanup(staging, root, root_identity, staged.identity)
        _run_cleanup_race_hook(cleanup)
        _finalize_bound_staging_cleanup(cleanup, staging.parent)
        _assert_root_identity(root, root_identity)
        _contained_path(final, root)
        _sync_directory(final.parent)
    except FlowArtifactInvalidError as exc:
        raise FlowArtifactConflictError("matching artifact residue changed during recovery") from exc
    except OSError as exc:
        raise FlowArtifactConflictError("matching artifact residue could not be finalized") from exc
    return final_snapshot.facts


def _inspect_artifact(
    path: Path,
    *,
    root: Path | None = None,
    root_identity: _FileIdentity | None = None,
    expected_identity: _FileIdentity | None = None,
) -> _ArtifactSnapshot:
    if root is not None:
        if root_identity is None:
            raise AssertionError("root identity is required for bound inspection")
        _assert_root_identity(root, root_identity)
        path = _contained_path(path, root)
    initial_stat = _regular_file_stat(path)
    initial_identity = _identity_from_stat(initial_stat)
    if expected_identity is not None and initial_identity != expected_identity:
        raise FlowArtifactInvalidError("artifact identity changed")
    size_bytes = initial_stat.st_size
    if size_bytes <= 0 or size_bytes > _MAX_ARTIFACT_BYTES:
        raise FlowArtifactInvalidError("artifact size is outside the permitted range")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to open artifact") from exc
    try:
        opened_stat = os.fstat(descriptor)
        opened_identity = _identity_from_stat(opened_stat)
        if opened_identity != initial_identity or not stat.S_ISREG(opened_stat.st_mode):
            raise FlowArtifactInvalidError("artifact identity changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            facts = _inspect_open_stream(stream, path, size_bytes)
        final_stat = _regular_file_stat(path)
        if (
            _identity_from_stat(final_stat) != opened_identity
            or final_stat.st_size != size_bytes
            or os.fstat(descriptor).st_size != size_bytes
        ):
            raise FlowArtifactInvalidError("artifact changed during inspection")
        if root is not None and root_identity is not None:
            _assert_root_identity(root, root_identity)
            _contained_path(path, root)
        return _ArtifactSnapshot(facts=facts, identity=opened_identity)
    except OSError as exc:
        raise FlowArtifactInvalidError("artifact inspection failed") from exc
    finally:
        os.close(descriptor)


def _inspect_open_stream(stream: BinaryIO, path: Path, size_bytes: int) -> FlowArtifactFacts:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            old_max_pixels = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = _MAX_ARTIFACT_PIXELS
            try:
                stream.seek(0)
                with Image.open(stream) as image:
                    image.verify()
                    pillow_format = image.format
                stream.seek(0)
                with Image.open(stream) as image:
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
    _validate_container(stream, image_format, size_bytes)
    digest = _sha256_stream(stream)
    return FlowArtifactFacts(
        format=image_format,
        width=width,
        height=height,
        size_bytes=size_bytes,
        sha256=digest,
    )


def _regular_file_stat(path: Path) -> os.stat_result:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to stat artifact") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise FlowArtifactInvalidError("artifact is not a regular file")
    return metadata


def _identity_from_stat(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(device=metadata.st_dev, inode=metadata.st_ino)


def _directory_identity(path: Path) -> _FileIdentity:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to stat trusted Flow work root") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise FlowArtifactInvalidError("trusted Flow work root is not a directory")
    return _identity_from_stat(metadata)


def _assert_root_identity(root: Path, expected: _FileIdentity) -> None:
    if _directory_identity(root) != expected or _path_is_link_or_junction(root):
        raise FlowArtifactInvalidError("trusted Flow work root changed")


def _assert_bound_artifact(
    path: Path, root: Path, root_identity: _FileIdentity, expected: _FileIdentity
) -> None:
    _assert_root_identity(root, root_identity)
    path = _contained_path(path, root)
    if _identity_from_stat(_regular_file_stat(path)) != expected:
        raise FlowArtifactInvalidError("artifact identity changed")


def _bind_staging_cleanup(
    staging: Path, root: Path, root_identity: _FileIdentity, expected: _FileIdentity
) -> _StagingCleanupBinding:
    _assert_bound_artifact(staging, root, root_identity, expected)
    parent = staging.parent
    parent_identity = _directory_identity(parent)
    if os.name == "nt":
        parent_descriptor = _open_windows_parent_lock(parent, parent_identity)
        try:
            descriptor = _open_windows_delete_handle(staging, expected)
        except BaseException:
            os.close(parent_descriptor)
            raise
        return _StagingCleanupBinding(
            staging_name=staging.name,
            parent_identity=parent_identity,
            artifact_identity=expected,
            descriptor=descriptor,
            parent_descriptor=parent_descriptor,
            windows_delete_handle=True,
        )
    if os.unlink not in os.supports_dir_fd:
        raise FlowArtifactInvalidError("platform cannot bind staging cleanup to a directory handle")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to bind staging directory for cleanup") from exc
    try:
        if _identity_from_stat(os.fstat(descriptor)) != parent_identity:
            raise FlowArtifactInvalidError("staging directory changed during cleanup binding")
        metadata = os.stat(staging.name, dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or _identity_from_stat(metadata) != expected:
            raise FlowArtifactInvalidError("staging artifact changed during cleanup binding")
    except BaseException:
        os.close(descriptor)
        raise
    return _StagingCleanupBinding(
        staging_name=staging.name,
        parent_identity=parent_identity,
        artifact_identity=expected,
        descriptor=descriptor,
        parent_descriptor=descriptor,
        windows_delete_handle=False,
    )


def _delete_bound_staging(binding: _StagingCleanupBinding) -> None:
    try:
        if binding.windows_delete_handle:
            _delete_windows_handle(binding.descriptor)
        else:
            if _identity_from_stat(os.fstat(binding.descriptor)) != binding.parent_identity:
                raise FlowArtifactInvalidError("staging directory changed before cleanup")
            quarantine_name = _move_posix_staging_to_quarantine(binding)
            os.unlink(quarantine_name, dir_fd=binding.descriptor)
    except OSError as exc:
        raise FlowArtifactInvalidError("unable to remove bound staging artifact") from exc
    finally:
        _close_cleanup_binding(binding)


def _move_posix_staging_to_quarantine(binding: _StagingCleanupBinding) -> str:
    """Atomically move the expected entry before any POSIX unlink by name."""
    for _ in range(16):
        quarantine_name = f".cleanup-{uuid4().hex}.part"
        try:
            _rename_posix_noreplace(
                binding.descriptor,
                binding.staging_name,
                binding.descriptor,
                quarantine_name,
            )
        except FileExistsError:
            continue
        metadata = os.stat(quarantine_name, dir_fd=binding.descriptor, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or _identity_from_stat(metadata) != binding.artifact_identity:
            raise FlowArtifactInvalidError("staging artifact changed during cleanup")
        return quarantine_name
    raise FlowArtifactInvalidError("unable to reserve staging cleanup quarantine")


def _rename_posix_noreplace(source_dir_fd: int, source_name: str, target_dir_fd: int, target_name: str) -> None:
    """Use POSIX's platform-specific no-replace rename or fail closed."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise FlowArtifactInvalidError("platform cannot atomically quarantine staging cleanup") from exc
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(
        source_dir_fd,
        os.fsencode(source_name),
        target_dir_fd,
        os.fsencode(target_name),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, "cleanup quarantine already exists", target_name)
    raise OSError(error, "unable to atomically quarantine staging cleanup", source_name)


def _assert_cleanup_parent_current(parent: Path, expected: _FileIdentity) -> None:
    if _path_is_link_or_junction(parent) or _directory_identity(parent) != expected:
        raise FlowArtifactInvalidError("staging directory changed before cleanup")


def _finalize_bound_staging_cleanup(binding: _StagingCleanupBinding, parent: Path) -> None:
    try:
        _assert_cleanup_parent_current(parent, binding.parent_identity)
    except BaseException:
        _close_cleanup_binding(binding)
        raise
    _delete_bound_staging(binding)


def _run_cleanup_race_hook(binding: _StagingCleanupBinding) -> None:
    try:
        if _after_flow_artifact_cleanup_revalidation is not None:
            _after_flow_artifact_cleanup_revalidation()
    except BaseException:
        _close_cleanup_binding(binding)
        raise


def _close_cleanup_binding(binding: _StagingCleanupBinding) -> None:
    try:
        os.close(binding.descriptor)
    finally:
        if binding.parent_descriptor != binding.descriptor:
            os.close(binding.parent_descriptor)


def _open_windows_parent_lock(parent: Path, expected: _FileIdentity) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(parent),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002,  # read/write sharing, deliberately no delete sharing
        None,
        3,  # OPEN_EXISTING
        0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise FlowArtifactInvalidError("unable to bind staging directory for cleanup")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError as exc:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise FlowArtifactInvalidError("unable to bind staging directory for cleanup") from exc
    if _identity_from_stat(os.fstat(descriptor)) != expected:
        os.close(descriptor)
        raise FlowArtifactInvalidError("staging directory changed during cleanup binding")
    return descriptor


def _open_windows_delete_handle(staging: Path, expected: _FileIdentity) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(staging),
        0x00010000,  # DELETE
        0x00000001 | 0x00000002 | 0x00000004,  # read/write/delete sharing
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise FlowArtifactInvalidError("unable to bind staging artifact for cleanup")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError as exc:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise FlowArtifactInvalidError("unable to bind staging artifact for cleanup") from exc
    if _identity_from_stat(os.fstat(descriptor)) != expected:
        os.close(descriptor)
        raise FlowArtifactInvalidError("staging artifact changed during cleanup binding")
    return descriptor


def _delete_windows_handle(descriptor: int) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOL)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    disposition = _FileDispositionInfo(True)
    if not set_information(
        msvcrt.get_osfhandle(descriptor),
        4,  # FileDispositionInfo
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle failed")


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


def _validate_container(stream: BinaryIO, image_format: str, size_bytes: int) -> None:
    try:
        stream.seek(0)
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
    data = stream.read(size_bytes)
    if len(data) != size_bytes or size_bytes < 4 or data[:2] != b"\xff\xd8":
        raise ValueError
    offset = 2
    in_scan = False
    while offset < len(data):
        if in_scan:
            marker_offset = data.find(b"\xff", offset)
            if marker_offset < 0 or marker_offset + 1 >= len(data):
                raise ValueError
            marker = data[marker_offset + 1]
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                offset = marker_offset + 2
                continue
            if marker == 0xD9:
                if marker_offset + 2 != len(data):
                    raise ValueError
                return
            offset = marker_offset
            in_scan = False
            continue
        if data[offset] != 0xFF:
            raise ValueError
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise ValueError
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            if offset != len(data):
                raise ValueError
            return
        if marker == 0x00 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        if offset + 2 > len(data):
            raise ValueError
        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            raise ValueError
        offset += segment_length
        if marker == 0xDA:
            in_scan = True
    raise ValueError


def _validate_webp_container(stream: BinaryIO, size_bytes: int) -> None:
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP":
        raise ValueError
    declared_size = struct.unpack("<I", header[4:8])[0]
    if declared_size + 8 != size_bytes:
        raise ValueError


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    try:
        stream.seek(0)
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
