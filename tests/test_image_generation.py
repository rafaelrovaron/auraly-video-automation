from __future__ import annotations

import json
from pathlib import Path

import pytest

from auraly_pipeline.image_generation import (
    ImageGenerationError,
    finalize_generation,
    prepare_generation,
    record_download_baseline,
    resolve_project_path,
    unique_path,
    validate_job,
    validate_reference_image,
    wait_for_download,
    write_failure_result,
)


def _png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image-payload")
    return path


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "Auraly"
    reference = _png(root / "03 Avatars" / "character-blueprint.png")
    job = root / "pipeline" / "work" / "existing-job"
    for name in ("source", "manifest", "inspection"):
        (job / name).mkdir(parents=True, exist_ok=True)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    return root, reference, downloads


def _prepare(project: tuple[Path, Path, Path], **overrides):
    root, reference, downloads = project
    kwargs = {
        "job_name": "existing-job",
        "reference_image_path": "03 Avatars/character-blueprint.png",
        "prompt": "Line one\nLine two — preserve exactly.",
        "output_filename": "scene.png",
        "project_root": root,
        "downloads_dir": downloads,
    }
    kwargs.update(overrides)
    return prepare_generation(**kwargs)


def test_resolve_project_relative_path(project: tuple[Path, Path, Path]) -> None:
    root, reference, _ = project
    assert resolve_project_path("03 Avatars/character-blueprint.png", root) == reference.resolve()


def test_resolve_absolute_windows_path(project: tuple[Path, Path, Path]) -> None:
    root, reference, _ = project
    assert validate_reference_image(str(reference.resolve()), root) == reference.resolve()


def test_missing_reference_image_is_rejected(project: tuple[Path, Path, Path]) -> None:
    root, _, _ = project
    with pytest.raises(ImageGenerationError, match="does not exist") as caught:
        validate_reference_image("03 Avatars/missing.png", root)
    assert caught.value.step == "validate_reference_image"


def test_unsupported_reference_image_is_rejected(project: tuple[Path, Path, Path]) -> None:
    root, _, _ = project
    unsupported = root / "03 Avatars" / "blueprint.txt"
    unsupported.write_text("not an image", encoding="utf-8")
    with pytest.raises(ImageGenerationError, match="unsupported reference image extension"):
        validate_reference_image(unsupported, root)


def test_reference_must_remain_in_avatar_library(project: tuple[Path, Path, Path]) -> None:
    root, _, _ = project
    outside = _png(root / "pipeline" / "work" / "existing-job" / "source" / "wrong.png")
    with pytest.raises(ImageGenerationError, match="must be inside"):
        validate_reference_image(outside, root)


def test_nonexistent_job_is_rejected(project: tuple[Path, Path, Path]) -> None:
    root, _, _ = project
    with pytest.raises(ImageGenerationError, match="does not exist") as caught:
        validate_job("typo-job", root)
    assert caught.value.step == "validate_job"


def test_job_name_cannot_traverse(project: tuple[Path, Path, Path]) -> None:
    root, _, _ = project
    with pytest.raises(ImageGenerationError, match="invalid job_name"):
        validate_job("../existing-job", root)


def test_output_directory_and_path_construction(project: tuple[Path, Path, Path]) -> None:
    root, _, _ = project
    context, context_path = _prepare(project)
    expected = root / "pipeline" / "work" / "existing-job" / "source" / "scene.png"
    assert Path(context.source_dir) == expected.parent.resolve()
    assert Path(context.output_path) == expected.resolve()
    assert Path(context.context_path) == context_path
    assert context.prompt == "Line one\nLine two — preserve exactly."


def test_timestamp_filename_when_output_omitted(project: tuple[Path, Path, Path]) -> None:
    context, _ = _prepare(project, output_filename=None)
    assert Path(context.output_path).name.startswith("ai_image_")
    assert Path(context.output_path).suffix == ".png"
    assert context.output_filename_was_explicit is False


def test_filename_collision_starts_at_002(project: tuple[Path, Path, Path]) -> None:
    root, _, _ = project
    source = root / "pipeline" / "work" / "existing-job" / "source"
    _png(source / "scene.png")
    assert unique_path(source / "scene.png").name == "scene_002.png"
    _png(source / "scene_002.png")
    context, _ = _prepare(project)
    assert Path(context.output_path).name == "scene_003.png"


def test_download_detection_uses_before_after_inventory(
    project: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, downloads = project
    old = _png(downloads / "old.png")
    _, context_path = _prepare(project)
    baseline = record_download_baseline(context_path)
    assert str(old.resolve()) in baseline.download_baseline
    generated = _png(downloads / "generated.png")
    monkeypatch.setattr("auraly_pipeline.image_generation.time.sleep", lambda _: None)
    assert wait_for_download(context_path, timeout_seconds=1) == generated.resolve()


def test_finalize_moves_image_and_writes_manifest(project: tuple[Path, Path, Path]) -> None:
    root, _, downloads = project
    context, context_path = _prepare(project)
    downloaded = _png(downloads / "generated.png")
    result = finalize_generation(context_path, downloaded)

    saved = Path(result.saved_file_path or "")
    manifest_path = Path(result.manifest_path or "")
    assert result.success is True
    assert saved == Path(context.output_path)
    assert saved.is_file()
    assert not downloaded.exists()
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["provider"] == "google_ai_studio"
    assert manifest["skill"] == "generate_google_ai_studio_image"
    assert manifest["jobName"] == "existing-job"
    assert manifest["prompt"] == "Line one\nLine two — preserve exactly."
    assert manifest["referenceImage"] == "03 Avatars/character-blueprint.png"
    assert manifest["outputFile"] == saved.relative_to(root).as_posix()
    assert manifest["status"] == "success"


def test_explicit_extension_must_match_download(project: tuple[Path, Path, Path]) -> None:
    _, _, downloads = project
    _, context_path = _prepare(project, output_filename="scene.jpg")
    downloaded = _png(downloads / "generated.png")
    with pytest.raises(ImageGenerationError, match="does not match"):
        finalize_generation(context_path, downloaded)


def test_failure_result_is_structured_and_persisted(project: tuple[Path, Path, Path]) -> None:
    _, context_path = _prepare(project)
    result = write_failure_result(
        context_path,
        failed_step="upload_reference_image",
        error="Reference image upload did not complete.",
    )
    assert result.success is False
    assert result.failed_step == "upload_reference_image"
    assert result.saved_file_path is None
    assert Path(result.diagnostic_path or "").is_file()
