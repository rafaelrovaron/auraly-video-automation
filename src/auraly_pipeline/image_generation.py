from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import Field

from auraly_pipeline.config_paths import (
    DEFAULT_PROJECT_ROOT as DEFAULT_PROJECT_ROOT,
    WORK_ROOT_RELATIVE as WORK_ROOT_RELATIVE,
    configured_project_root,
    configured_work_root,
)
from auraly_pipeline.models import ContractModel

LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = configured_project_root()
AVATARS_ROOT_RELATIVE = Path("03 Avatars")
GOOGLE_FLOW_URL = "https://labs.google/fx/tools/flow"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_RETRY_COUNT = 2
DEFAULT_DOWNLOADS_DIR = Path.home() / "Downloads"
SUPPORTED_REFERENCE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
SUPPORTED_OUTPUT_EXTENSIONS = SUPPORTED_REFERENCE_EXTENSIONS
PARTIAL_DOWNLOAD_EXTENSIONS = frozenset({".crdownload", ".part", ".tmp"})


PUBLIC_IMAGE_ERRORS = {
    "load_context": "Generation context is invalid or outside the approved job layout.",
    "read_prompt": "The image prompt could not be read.",
    "resolve_reference_image": "The reference image path is invalid.",
    "validate_reference_image": "The reference image is invalid or unavailable.",
    "validate_job": "The requested image-generation job is invalid or unavailable.",
    "download_generated_image": "The generated image download failed.",
    "move_output_to_job": "The generated image could not be saved to the approved job directory.",
    "write_failure_result": "The failure diagnostic could not be written safely.",
}
PUBLIC_FAILURE_STEPS = frozenset(
    {
        "provider_request",
        "launch_flow",
        "verify_flow_ui",
        "upload_reference_image",
        "generate_candidates",
        "download_2k",
        "capture_browser_trace",
        "download_generated_image",
        "move_output_to_job",
    }
)


def configured_downloads_dir(value: Path | None = None) -> Path:
    """Return the trusted downloads root from an explicit option or local configuration."""
    if value is not None:
        return value.resolve()
    configured = os.environ.get("AURALY_DOWNLOADS_DIR", "").strip()
    return Path(configured).resolve() if configured else DEFAULT_DOWNLOADS_DIR.resolve()


def public_image_step(exc: Exception) -> str:
    """Return a stable allowlisted step name for CLI responses."""
    if isinstance(exc, ImageGenerationError) and exc.step in PUBLIC_IMAGE_ERRORS:
        return exc.step
    return "image_generation"


def public_image_error(exc: Exception) -> str:
    """Return an allowlisted CLI-safe message without embedding exception details."""
    if isinstance(exc, ImageGenerationError):
        return PUBLIC_IMAGE_ERRORS.get(exc.step, "The image-generation operation failed safely.")
    return "The image-generation operation failed safely."


def canonical_failure_step(failed_step: str) -> str:
    """Map untrusted workflow-step text to a stable persisted identifier."""
    normalized = re.sub(r"[^a-z0-9_]+", "_", failed_step.casefold()).strip("_")
    return normalized if normalized in PUBLIC_FAILURE_STEPS else "unknown"


def public_failure_message(failed_step: str) -> str:
    """Return a diagnostic message derived only from an allowlisted workflow step."""
    canonical_step = canonical_failure_step(failed_step)
    if canonical_step == "unknown":
        return "Image generation failed during an approved workflow step."
    return f"Image generation failed during {canonical_step}."


class ImageGenerationError(RuntimeError):
    """Failure with a stable workflow step for structured reporting."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step


class DownloadEntry(ContractModel):
    size_bytes: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)


class GenerationContext(ContractModel):
    schema_version: Literal["1.1"] = "1.1"
    request_id: str
    provider: Literal["google_flow"] = "google_flow"
    executor: Literal["playwright_python"] = "playwright_python"
    required_output_resolution: Literal["2K"] = "2K"
    concurrency: Literal[1] = 1
    browser_runtime_status: Literal["not_implemented"] = "not_implemented"
    project_root: str
    job_name: str
    job_dir: str
    flow_url: str
    timeout_seconds: int = Field(gt=0)
    retry_count: int = Field(ge=0, le=5)
    reference_image_path: str
    reference_image: str
    prompt: str = Field(min_length=1)
    output_path: str
    output_file: str
    output_filename_was_explicit: bool
    source_dir: str
    manifest_dir: str
    inspection_dir: str
    downloads_dir: str
    context_path: str
    created_at: str
    download_baseline: dict[str, DownloadEntry] = Field(default_factory=dict)
    download_started_at_ns: int | None = None
    detected_download_path: str | None = None


class ImageGenerationManifest(ContractModel):
    schema_version: Literal["1.1"] = "1.1"
    provider: Literal["google_flow"] = "google_flow"
    executor: Literal["playwright_python"] = "playwright_python"
    required_output_resolution: Literal["2K"] = "2K"
    image_qc_status: Literal["not_implemented"] = "not_implemented"
    browser_runtime_status: Literal["not_implemented"] = "not_implemented"
    job_name: str
    reference_image: str
    prompt: str
    output_file: str
    generated_at: str
    status: Literal["download_ingested"] = "download_ingested"


class ImageGenerationResult(ContractModel):
    success: bool
    job_name: str
    provider: Literal["google_flow"] = "google_flow"
    browser_runtime_status: Literal["not_implemented"] = "not_implemented"
    reference_image_path: str | None = None
    prompt: str | None = None
    saved_file_path: str | None = None
    manifest_path: str | None = None
    timestamp: str
    failed_step: str | None = None
    error: str | None = None
    debug_screenshot: str | None = None
    diagnostic_path: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _now().isoformat()


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _validate_loaded_context(
    context: GenerationContext,
    context_path: Path,
    trusted_downloads_dir: Path,
    trusted_project_root: Path,
) -> GenerationContext:
    resolved_context = context_path.resolve()
    try:
        inspection_dir = resolved_context.parent
        job_dir = resolved_context.parents[2]
        work_dir = resolved_context.parents[3]
        pipeline_dir = resolved_context.parents[4]
        project_root = resolved_context.parents[5]
    except IndexError as exc:
        raise ImageGenerationError(
            "load_context", f"context path is outside the expected job layout: {resolved_context}"
        ) from exc

    if project_root.resolve() != trusted_project_root.resolve():
        raise ImageGenerationError(
            "load_context",
            "context project_root does not match the trusted local configuration",
        )

    if (
        inspection_dir.name != "google-flow"
        or inspection_dir.parent.name != "inspection"
        or work_dir.name != "work"
        or pipeline_dir.name != "pipeline"
        or not resolved_context.name.startswith("request_")
        or resolved_context.suffix.casefold() != ".json"
    ):
        raise ImageGenerationError(
            "load_context", f"context path is outside the expected job layout: {resolved_context}"
        )

    expected_paths = {
        "project_root": project_root,
        "job_dir": job_dir,
        "source_dir": job_dir / "source",
        "manifest_dir": job_dir / "manifest",
        "inspection_dir": inspection_dir,
        "context_path": resolved_context,
    }
    for field_name, expected in expected_paths.items():
        actual = Path(getattr(context, field_name)).resolve()
        if actual != expected.resolve():
            raise ImageGenerationError(
                "load_context",
                f"context field {field_name} does not match the canonical job layout",
            )

    resolved_job_dir = job_dir.resolve()
    for field_name in ("source_dir", "manifest_dir", "inspection_dir"):
        resolved_child = Path(getattr(context, field_name)).resolve()
        try:
            resolved_child.relative_to(resolved_job_dir)
        except ValueError as exc:
            raise ImageGenerationError(
                "load_context",
                f"context field {field_name} resolves outside the job directory",
            ) from exc

    output_path = Path(context.output_path).resolve()
    source_dir = expected_paths["source_dir"].resolve()
    try:
        output_path.relative_to(source_dir)
    except ValueError as exc:
        raise ImageGenerationError(
            "load_context", "context field output_path escapes the job source directory"
        ) from exc
    if output_path.parent != source_dir:
        raise ImageGenerationError(
            "load_context", "context field output_path must be directly inside the job source directory"
        )
    expected_output_file = output_path.relative_to(project_root.resolve()).as_posix()
    if context.output_file != expected_output_file:
        raise ImageGenerationError(
            "load_context", "context field output_file does not match output_path"
        )

    reference_path = Path(context.reference_image_path).resolve()
    avatars_root = (project_root / AVATARS_ROOT_RELATIVE).resolve()
    try:
        reference_path.relative_to(avatars_root)
    except ValueError as exc:
        raise ImageGenerationError(
            "load_context", "context field reference_image_path escapes the avatar library"
        ) from exc
    expected_reference = reference_path.relative_to(project_root.resolve()).as_posix()
    if context.reference_image != expected_reference:
        raise ImageGenerationError(
            "load_context", "context field reference_image does not match reference_image_path"
        )

    downloads_dir = Path(context.downloads_dir).resolve()
    if downloads_dir != trusted_downloads_dir.resolve():
        raise ImageGenerationError(
            "load_context",
            "context field downloads_dir does not match the trusted local configuration",
        )
    if context.detected_download_path:
        detected = Path(context.detected_download_path).resolve()
        try:
            detected.relative_to(downloads_dir)
        except ValueError as exc:
            raise ImageGenerationError(
                "load_context", "context field detected_download_path escapes downloads_dir"
            ) from exc
    return context


def _read_context(
    path: Path,
    downloads_dir: Path | None = None,
    project_root: Path | None = None,
) -> GenerationContext:
    try:
        resolved = path.resolve()
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        context = GenerationContext.model_validate(payload)
        return _validate_loaded_context(
            context,
            resolved,
            configured_downloads_dir(downloads_dir),
            configured_project_root(project_root),
        )
    except ImageGenerationError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ImageGenerationError("load_context", f"invalid generation context: {path}: {exc}") from exc


def _write_context(context: GenerationContext) -> Path:
    return _write_json(
        Path(context.context_path),
        context.model_dump(by_alias=True, mode="json"),
    )


def _is_windows_absolute(value: str) -> bool:
    return PureWindowsPath(value).is_absolute()


def resolve_project_path(value: str | Path, project_root: Path | None = None) -> Path:
    """Resolve a project-relative or absolute Windows path without requiring it to exist."""
    root = configured_project_root(project_root)
    raw = os.path.expandvars(os.path.expanduser(str(value).strip()))
    if not raw:
        raise ImageGenerationError("resolve_reference_image", "reference image path is empty")
    if _is_windows_absolute(raw) or Path(raw).is_absolute():
        return Path(raw).resolve()
    normalized = raw.replace("\\", "/")
    return (root / normalized).resolve()


def _require_within(path: Path, root: Path, step: str, label: str) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ImageGenerationError(step, f"{label} must be inside {root.resolve()}: {path}") from exc


def validate_job(job_name: str, project_root: Path | None = None) -> Path:
    LOGGER.info("[image-generation] Validating job")
    if not job_name or job_name in {".", ".."} or "/" in job_name or "\\" in job_name:
        raise ImageGenerationError("validate_job", f"invalid job_name: {job_name!r}")
    work_root = configured_work_root(project_root=project_root)
    job_dir = (work_root / job_name).resolve()
    _require_within(job_dir, work_root, "validate_job", "job")
    if not job_dir.is_dir():
        raise ImageGenerationError("validate_job", f"Auraly job does not exist: {job_dir}")
    return job_dir


def validate_reference_image(
    reference_image_path: str | Path,
    project_root: Path | None = None,
) -> Path:
    LOGGER.info("[image-generation] Resolving reference image")
    root = configured_project_root(project_root)
    path = resolve_project_path(reference_image_path, root)
    avatars_root = (root / AVATARS_ROOT_RELATIVE).resolve()
    _require_within(path, avatars_root, "validate_reference_image", "reference image")
    if not path.is_file():
        raise ImageGenerationError(
            "validate_reference_image", f"reference image does not exist: {path}"
        )
    if path.suffix.casefold() not in SUPPORTED_REFERENCE_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_REFERENCE_EXTENSIONS))
        raise ImageGenerationError(
            "validate_reference_image",
            f"unsupported reference image extension {path.suffix!r}; expected one of: {supported}",
        )
    LOGGER.info("[image-generation] Reference image found")
    return path


def _validate_output_filename(filename: str) -> str:
    if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ImageGenerationError("resolve_output", "output_filename must be a filename, not a path")
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_OUTPUT_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_OUTPUT_EXTENSIONS))
        raise ImageGenerationError(
            "resolve_output", f"unsupported output extension {suffix!r}; expected one of: {supported}"
        )
    return filename


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ImageGenerationError("resolve_output", f"could not allocate a unique filename for {path}")


def prepare_generation(
    *,
    job_name: str,
    reference_image_path: str | Path,
    prompt: str,
    output_filename: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retry_count: int = DEFAULT_RETRY_COUNT,
    project_root: Path | None = None,
    downloads_dir: Path | None = None,
) -> tuple[GenerationContext, Path]:
    if not prompt:
        raise ImageGenerationError("validate_prompt", "prompt is required and cannot be empty")

    if timeout_seconds <= 0:
        raise ImageGenerationError("validate_timeout", "timeout_seconds must be greater than zero")
    if not 0 <= retry_count <= 5:
        raise ImageGenerationError("validate_retry_count", "retry_count must be between 0 and 5")

    root = configured_project_root(project_root)
    job_dir = validate_job(job_name, root)
    reference = validate_reference_image(reference_image_path, root)

    source_dir = job_dir / "source"
    manifest_dir = job_dir / "manifest"
    inspection_dir = job_dir / "inspection" / "google-flow"
    source_dir.mkdir(exist_ok=True)
    manifest_dir.mkdir(exist_ok=True)
    inspection_dir.mkdir(parents=True, exist_ok=True)

    resolved_downloads = configured_downloads_dir(downloads_dir)
    if not resolved_downloads.is_dir():
        raise ImageGenerationError(
            "resolve_downloads", f"browser downloads directory does not exist: {resolved_downloads}"
        )

    created = _now()
    request_id = created.strftime("%Y%m%d_%H%M%S_%f")
    explicit = output_filename is not None
    filename = (
        _validate_output_filename(output_filename)
        if output_filename is not None
        else created.strftime("ai_image_%Y%m%d_%H%M%S.png")
    )
    output_path = unique_path(source_dir / filename)
    context_path = inspection_dir / f"request_{request_id}.json"
    reference_relative = reference.relative_to(root).as_posix()
    output_relative = output_path.relative_to(root).as_posix()

    context = GenerationContext(
        request_id=request_id,
        project_root=str(root),
        job_name=job_name,
        job_dir=str(job_dir),
        flow_url=GOOGLE_FLOW_URL,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        reference_image_path=str(reference),
        reference_image=reference_relative,
        prompt=prompt,
        output_path=str(output_path),
        output_file=output_relative,
        output_filename_was_explicit=explicit,
        source_dir=str(source_dir),
        manifest_dir=str(manifest_dir),
        inspection_dir=str(inspection_dir),
        downloads_dir=str(resolved_downloads),
        context_path=str(context_path),
        created_at=created.isoformat(),
    )
    _write_context(context)
    return context, context_path


def _download_inventory(downloads_dir: Path) -> dict[str, DownloadEntry]:
    inventory: dict[str, DownloadEntry] = {}
    for path in downloads_dir.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        inventory[str(path.resolve())] = DownloadEntry(
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
    return inventory


def record_download_baseline(
    context_path: Path,
    downloads_dir: Path | None = None,
    project_root: Path | None = None,
) -> GenerationContext:
    context = _read_context(context_path, downloads_dir, project_root)
    downloads_dir = Path(context.downloads_dir)
    if not downloads_dir.is_dir():
        raise ImageGenerationError(
            "download_generated_image", f"browser downloads directory does not exist: {downloads_dir}"
        )
    context.download_baseline = _download_inventory(downloads_dir)
    context.download_started_at_ns = time.time_ns()
    context.detected_download_path = None
    _write_context(context)
    return context


def _new_download_candidates(context: GenerationContext) -> list[Path]:
    downloads_dir = Path(context.downloads_dir)
    candidates: list[Path] = []
    threshold = (context.download_started_at_ns or 0) - 2_000_000_000
    for path in downloads_dir.iterdir():
        if not path.is_file() or path.suffix.casefold() in PARTIAL_DOWNLOAD_EXTENSIONS:
            continue
        if path.suffix.casefold() not in SUPPORTED_OUTPUT_EXTENSIONS:
            continue
        resolved = str(path.resolve())
        if resolved in context.download_baseline:
            continue
        try:
            if path.stat().st_mtime_ns < threshold:
                continue
        except OSError:
            continue
        candidates.append(path.resolve())
    return sorted(candidates, key=lambda item: item.name.casefold())


def wait_for_download(
    context_path: Path,
    timeout_seconds: int | None = None,
    downloads_dir: Path | None = None,
    project_root: Path | None = None,
) -> Path:
    context = _read_context(context_path, downloads_dir, project_root)
    if context.download_started_at_ns is None:
        raise ImageGenerationError(
            "download_generated_image", "download baseline was not recorded before clicking Download"
        )
    timeout = timeout_seconds or context.timeout_seconds
    deadline = time.monotonic() + timeout
    previous_size: int | None = None
    stable_observations = 0
    while time.monotonic() < deadline:
        candidates = _new_download_candidates(context)
        if len(candidates) > 1:
            rendered = ", ".join(str(path) for path in candidates)
            raise ImageGenerationError(
                "download_generated_image",
                f"ambiguous download: multiple new image files appeared: {rendered}",
            )
        if len(candidates) == 1:
            candidate = candidates[0]
            try:
                size = candidate.stat().st_size
            except OSError:
                size = 0
            if size > 0 and size == previous_size:
                stable_observations += 1
            else:
                stable_observations = 0
            previous_size = size
            if stable_observations >= 1:
                context.detected_download_path = str(candidate)
                _write_context(context)
                return candidate
        time.sleep(1)
    raise ImageGenerationError(
        "download_generated_image", f"no completed image download detected within {timeout} seconds"
    )


def _detect_image_format(path: Path) -> str | None:
    header = path.read_bytes()[:16]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"
    return None


def _manifest_path(manifest_dir: Path, generated_at: datetime) -> Path:
    filename = generated_at.strftime("image-generation-%Y%m%d-%H%M%S.json")
    return unique_path(manifest_dir / filename)


def finalize_generation(
    context_path: Path,
    downloaded_file: Path | None = None,
    downloads_dir: Path | None = None,
    project_root: Path | None = None,
) -> ImageGenerationResult:
    context = _read_context(context_path, downloads_dir, project_root)
    source = (downloaded_file or Path(context.detected_download_path or "")).resolve()
    downloads_dir = Path(context.downloads_dir).resolve()
    _require_within(
        source,
        downloads_dir,
        "move_output_to_job",
        "downloaded image",
    )
    if not source.is_file():
        raise ImageGenerationError("move_output_to_job", f"downloaded image does not exist: {source}")
    detected_suffix = _detect_image_format(source)
    if detected_suffix is None:
        raise ImageGenerationError(
            "move_output_to_job", f"downloaded file is not a supported PNG, JPEG, or WebP image: {source}"
        )

    destination = Path(context.output_path)
    requested_suffix = destination.suffix.casefold()
    normalized_detected = ".jpg" if detected_suffix == ".jpg" else detected_suffix
    normalized_requested = ".jpg" if requested_suffix in {".jpg", ".jpeg"} else requested_suffix
    if normalized_requested != normalized_detected:
        if context.output_filename_was_explicit:
            raise ImageGenerationError(
                "move_output_to_job",
                f"downloaded image format {detected_suffix} does not match requested output extension {requested_suffix}",
            )
        destination = destination.with_suffix(detected_suffix)
    destination = unique_path(destination)
    _require_within(destination.resolve(), Path(context.source_dir), "move_output_to_job", "output")

    LOGGER.info("[image-generation] Saving result to job")
    shutil.move(str(source), str(destination))
    if not destination.is_file():
        raise ImageGenerationError(
            "move_output_to_job", f"final image was not created at expected path: {destination}"
        )

    generated_at = _now()
    project_root = Path(context.project_root)
    output_relative = destination.relative_to(project_root).as_posix()
    manifest = ImageGenerationManifest(
        job_name=context.job_name,
        reference_image=context.reference_image,
        prompt=context.prompt,
        output_file=output_relative,
        generated_at=generated_at.isoformat(),
    )
    manifest_path = _manifest_path(Path(context.manifest_dir), generated_at)
    _write_json(manifest_path, manifest.model_dump(by_alias=True, mode="json"))
    LOGGER.info("[image-generation] Manifest written")
    LOGGER.info("[image-generation] Complete")
    return ImageGenerationResult(
        success=True,
        job_name=context.job_name,
        reference_image_path=context.reference_image_path,
        prompt=context.prompt,
        saved_file_path=str(destination),
        manifest_path=str(manifest_path),
        timestamp=generated_at.isoformat(),
    )


def failure_screenshot_path(
    context_path: Path,
    failed_step: str,
    downloads_dir: Path | None = None,
    project_root: Path | None = None,
) -> Path:
    context = _read_context(context_path, downloads_dir, project_root)
    safe_step = canonical_failure_step(failed_step)
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    return unique_path(Path(context.inspection_dir) / f"error_{stamp}_{safe_step}.png")


def write_failure_result(
    context_path: Path,
    *,
    failed_step: str,
    error: str,
    debug_screenshot: Path | None = None,
    downloads_dir: Path | None = None,
    project_root: Path | None = None,
) -> ImageGenerationResult:
    context = _read_context(context_path, downloads_dir, project_root)
    timestamp = _iso_now()
    canonical_step = canonical_failure_step(failed_step)
    screenshot = str(debug_screenshot.resolve()) if debug_screenshot else None
    if debug_screenshot:
        _require_within(
            debug_screenshot.resolve(),
            Path(context.inspection_dir),
            "write_failure_result",
            "debug screenshot",
        )
    sanitized_error = public_failure_message(canonical_step)
    result = ImageGenerationResult(
        success=False,
        job_name=context.job_name,
        reference_image_path=context.reference_image_path,
        prompt=context.prompt,
        timestamp=timestamp,
        failed_step=canonical_step,
        error=sanitized_error,
        debug_screenshot=screenshot,
    )
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    diagnostic_path = unique_path(
        Path(context.inspection_dir) / f"failure_{stamp}_{canonical_step}.json"
    )
    result.diagnostic_path = str(diagnostic_path)
    _write_json(diagnostic_path, result.model_dump(by_alias=True, mode="json"))
    LOGGER.error('[image-generation] FAILED step=%s error="%s"', canonical_step, sanitized_error)
    return result


def export_image_generation_schema(output: Path) -> Path:
    schema = ImageGenerationManifest.model_json_schema(by_alias=True, mode="validation")
    schema["$id"] = "https://auraly.local/schemas/image-generation.schema.v1.1.json"
    schema["title"] = "Auraly Google Flow Image Manifest v1.1"
    return _write_json(output, schema)
