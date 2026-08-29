"""Real-file security tests for Flow download artifact handling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from auraly_pipeline.flow.artifacts import (
    FlowArtifactConflictError,
    FlowArtifactInvalidError,
    allocate_flow_staging_path,
    inspect_flow_artifact,
    publish_flow_artifact_exclusive,
    resolve_flow_final_path,
)
from PIL import Image


SCENE_ID = "00000000-0000-4000-8000-000000000001"


def _write_image(path: Path, image_format: str, *, axis: int = 2048) -> Path:
    image = Image.new("RGB", (axis, 1), color=(16, 32, 64))
    image.save(path, format=image_format)
    return path


def _paths(root: Path, *, image_format: str = "png") -> tuple[Path, Path]:
    staging = allocate_flow_staging_path(
        work_root=root,
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        generation_number=1,
        candidate_index=0,
    )
    final = resolve_flow_final_path(
        work_root=root,
        campaign_id="campaign-1",
        scene_variant_id=SCENE_ID,
        generation_number=1,
        candidate_index=0,
        image_format=image_format,
    )
    return staging, final


def _valid_staging_and_final(root: Path) -> tuple[Path, Path]:
    staging, final = _paths(root)
    _write_image(staging, "PNG")
    return staging, final


def test_candidate_staging_and_final_paths_are_canonical_and_distinct(tmp_path: Path) -> None:
    staging, final = _paths(tmp_path)

    assert final.relative_to(tmp_path.resolve()).as_posix().endswith(
        "generation-0001/candidate-0000.png"
    )
    assert staging.parent == final.parent / ".staging"
    assert staging.suffix == ".part"
    assert staging != final
    assert staging.is_file()


@pytest.mark.parametrize("candidate_index", [-1, 2])
def test_candidate_paths_reject_indexes_outside_two_selected_slots(
    tmp_path: Path, candidate_index: int
) -> None:
    with pytest.raises(FlowArtifactInvalidError):
        allocate_flow_staging_path(
            work_root=tmp_path,
            campaign_id="campaign-1",
            scene_variant_id=SCENE_ID,
            generation_number=1,
            candidate_index=candidate_index,
        )


@pytest.mark.parametrize("unsafe_identifier", ["../escape", "/absolute", r"C:\\escape"])
def test_candidate_paths_reject_traversal_and_absolute_identifiers(
    tmp_path: Path, unsafe_identifier: str
) -> None:
    with pytest.raises(FlowArtifactInvalidError):
        resolve_flow_final_path(
            work_root=tmp_path,
            campaign_id=unsafe_identifier,
            scene_variant_id=SCENE_ID,
            generation_number=1,
            candidate_index=0,
            image_format="png",
        )


def test_candidate_paths_reject_a_symlinked_parent_that_escapes_work_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    campaign = tmp_path / "campaigns" / "campaign-1"
    campaign.mkdir(parents=True)
    try:
        (campaign / "images").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable in this environment: {exc}")

    with pytest.raises(FlowArtifactInvalidError):
        resolve_flow_final_path(
            work_root=tmp_path,
            campaign_id="campaign-1",
            scene_variant_id=SCENE_ID,
            generation_number=1,
            candidate_index=0,
            image_format="png",
        )


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows filesystem feature")
def test_candidate_paths_reject_a_junctioned_parent_that_escapes_work_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    campaign = tmp_path / "campaigns" / "campaign-1"
    campaign.mkdir(parents=True)
    junction = campaign / "images"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("junctions unavailable in this environment")

    with pytest.raises(FlowArtifactInvalidError):
        resolve_flow_final_path(
            work_root=tmp_path,
            campaign_id="campaign-1",
            scene_variant_id=SCENE_ID,
            generation_number=1,
            candidate_index=0,
            image_format="png",
        )


def test_publish_rejects_staging_or_final_outside_trusted_root(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    outside = tmp_path.parent / "outside.png"
    _write_image(outside, "PNG")

    with pytest.raises(FlowArtifactInvalidError):
        publish_flow_artifact_exclusive(staging, outside, trusted_root=tmp_path)
    with pytest.raises(FlowArtifactInvalidError):
        publish_flow_artifact_exclusive(outside, final, trusted_root=tmp_path)


def test_exclusive_publish_never_overwrites_existing_final(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"existing")

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == b"existing"
    assert staging.exists()


def test_exclusive_publish_hard_links_then_removes_staging(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    staged_bytes = staging.read_bytes()

    facts = publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == staged_bytes
    assert facts.sha256 == hashlib.sha256(staged_bytes).hexdigest()
    assert not staging.exists()


def test_publish_rejects_a_final_suffix_that_does_not_match_validated_bytes(tmp_path: Path) -> None:
    staging, final = _paths(tmp_path, image_format="png")
    _write_image(staging, "JPEG")

    with pytest.raises(FlowArtifactInvalidError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert not final.exists()
    assert staging.exists()


def test_matching_crash_residue_recovers_without_changing_final_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    import auraly_pipeline.flow.artifacts as artifacts

    original_unlink = artifacts.os.unlink

    def fail_staging_unlink(path: str | os.PathLike[str]) -> None:
        if Path(path) == staging:
            raise OSError("injected interruption")
        original_unlink(path)

    monkeypatch.setattr(artifacts.os, "unlink", fail_staging_unlink)
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)
    monkeypatch.setattr(artifacts.os, "unlink", original_unlink)

    assert staging.samefile(final)
    before = final.read_bytes()
    facts = publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)
    assert final.read_bytes() == before
    assert not staging.exists()
    assert facts.sha256 == hashlib.sha256(before).hexdigest()


def test_mismatched_crash_residue_blocks_and_preserves_existing_final(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    _write_image(final, "PNG", axis=2049)
    before = final.read_bytes()

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == before
    assert staging.exists()


@pytest.mark.parametrize(
    ("suffix", "image_format", "expected_format"),
    [(".png", "PNG", "png"), (".jpg", "JPEG", "jpeg"), (".webp", "WEBP", "webp")],
)
def test_inspect_accepts_supported_decodable_2k_artifact(
    tmp_path: Path, suffix: str, image_format: str, expected_format: str
) -> None:
    fixture = _write_image(tmp_path / f"2k{suffix}", image_format)

    facts = inspect_flow_artifact(fixture)

    assert facts.format == expected_format
    assert max(facts.width, facts.height) >= 2048
    assert facts.size_bytes == fixture.stat().st_size
    assert facts.sha256 == hashlib.sha256(fixture.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("suffix", "image_format"),
    [(".png", "PNG"), (".jpg", "JPEG"), (".webp", "WEBP")],
)
def test_inspect_rejects_1k_artifact(tmp_path: Path, suffix: str, image_format: str) -> None:
    fixture = _write_image(tmp_path / f"1k{suffix}", image_format, axis=1024)

    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("empty.part", b""),
        ("partial.png", b"\x89PNG\r\n\x1a\n"),
        ("truncated.jpg", b"\xff\xd8\xff\xe0"),
        ("bad-segment.jpg", b"\xff\xd8\xff\xe0\x00\xff"),
        ("bad-riff.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
        ("unsupported.gif", b"GIF89a"),
        ("unsupported.bmp", b"BM"),
    ],
)
def test_inspect_rejects_malformed_or_unsupported_artifacts(
    tmp_path: Path, name: str, payload: bytes
) -> None:
    fixture = tmp_path / name
    fixture.write_bytes(payload)

    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture)


def test_inspect_rejects_recognized_extension_with_mismatched_signature(tmp_path: Path) -> None:
    fixture = _write_image(tmp_path / "wrong.png", "JPEG")

    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture)


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".webp"])
def test_inspect_rejects_trailing_polyglot_payload(tmp_path: Path, suffix: str) -> None:
    image_format = {".png": "PNG", ".jpg": "JPEG", ".webp": "WEBP"}[suffix]
    fixture = _write_image(tmp_path / f"polyglot{suffix}", image_format)
    fixture.write_bytes(fixture.read_bytes() + b"MZ executable marker")

    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture)


def test_inspect_rejects_an_oversized_file_before_decoding(tmp_path: Path) -> None:
    fixture = tmp_path / "oversized.part"
    with fixture.open("wb") as stream:
        stream.truncate(100_000_001)

    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture)
