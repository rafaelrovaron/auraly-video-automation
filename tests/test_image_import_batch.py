from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from auraly_pipeline.flow.artifacts import (
    FlowArtifactConflictError,
    FlowArtifactInvalidError,
    inspect_flow_artifact,
    inspect_image_artifact,
    publish_image_artifact_exclusive,
    resolve_trusted_image_path,
)


def _write_image(path: Path, image_format: str = "PNG", *, size: tuple[int, int] = (360, 640)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(20, 40, 80)).save(path, format=image_format)
    return path


@pytest.mark.parametrize(
    ("suffix", "image_format", "expected"),
    [(".png", "PNG", "png"), (".jpg", "JPEG", "jpeg"), (".webp", "WEBP", "webp")],
)
def test_generic_inspection_accepts_supported_portrait_below_2k(
    tmp_path: Path, suffix: str, image_format: str, expected: str
) -> None:
    source = _write_image(tmp_path / f"portrait{suffix}", image_format)

    assert inspect_image_artifact(source).format == expected
    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(source)


def test_generic_inspection_rejects_extension_that_disagrees_with_bytes(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "wrong.png", "JPEG")

    with pytest.raises(FlowArtifactInvalidError):
        inspect_image_artifact(source)


def test_trusted_path_rejects_link_that_escapes_source_root(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = _write_image(tmp_path / "outside.png")
    link = source_root / "linked.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("links are unavailable")

    with pytest.raises(FlowArtifactInvalidError):
        resolve_trusted_image_path(link, trusted_root=source_root)


def test_generic_publication_recovers_match_and_preserves_conflict(tmp_path: Path) -> None:
    root = tmp_path / "work"
    final = root / "images" / "candidate.png"
    staging = final.parent / ".staging" / "candidate.part"
    _write_image(staging)
    expected = staging.read_bytes()

    first = publish_image_artifact_exclusive(staging, final, trusted_root=root)
    assert final.read_bytes() == expected

    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(expected)
    recovered = publish_image_artifact_exclusive(staging, final, trusted_root=root)
    assert recovered == first

    _write_image(staging, size=(361, 640))
    before = final.read_bytes()
    with pytest.raises(FlowArtifactConflictError):
        publish_image_artifact_exclusive(staging, final, trusted_root=root)
    assert final.read_bytes() == before
