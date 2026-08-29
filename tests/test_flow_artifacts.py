"""Real-file security tests for Flow download artifact handling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from typing import Any

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


def _replace_directory_with_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            pytest.skip("junctions unavailable in this environment")
    else:
        link.symlink_to(target, target_is_directory=True)


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


@pytest.mark.skipif(os.name != "nt", reason="Windows has identity-bound delete-by-handle")
def test_exclusive_publish_hard_links_then_removes_staging(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    staged_bytes = staging.read_bytes()

    facts = publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == staged_bytes
    assert facts.sha256 == hashlib.sha256(staged_bytes).hexdigest()
    assert not staging.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX lacks identity-bound deletion")
def test_posix_publish_fails_closed_with_a_flushed_final_and_quarantined_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    staged_bytes = staging.read_bytes()
    import auraly_pipeline.flow.artifacts as artifacts

    events: list[str] = []
    original_sync = artifacts._sync_file_and_directory

    def observe_sync(path: Path) -> None:
        assert path == final
        events.append("sync-final")
        original_sync(path)

    monkeypatch.setattr(artifacts, "_sync_file_and_directory", observe_sync)

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    quarantines = list(staging.parent.glob(".cleanup-*.part"))
    assert events == ["sync-final"]
    assert final.read_bytes() == staged_bytes
    assert not staging.exists()
    assert len(quarantines) == 1
    assert quarantines[0].samefile(final)


def test_publish_rejects_a_final_suffix_that_does_not_match_validated_bytes(tmp_path: Path) -> None:
    staging, final = _paths(tmp_path, image_format="png")
    _write_image(staging, "JPEG")

    with pytest.raises(FlowArtifactInvalidError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert not final.exists()
    assert staging.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows has identity-bound delete-by-handle")
def test_matching_crash_residue_recovers_without_changing_final_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    import auraly_pipeline.flow.artifacts as artifacts

    original_delete = artifacts._delete_bound_staging

    def fail_staging_delete(binding: Any) -> None:
        artifacts._close_cleanup_binding(binding)
        raise OSError("injected interruption")

    monkeypatch.setattr(artifacts, "_delete_bound_staging", fail_staging_delete)
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)
    monkeypatch.setattr(artifacts, "_delete_bound_staging", original_delete)

    assert staging.samefile(final)
    before = final.read_bytes()
    facts = publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)
    assert final.read_bytes() == before
    assert not staging.exists()
    assert facts.sha256 == hashlib.sha256(before).hexdigest()


@pytest.mark.skipif(os.name != "nt", reason="Windows has identity-bound delete-by-handle")
def test_matching_residue_syncs_final_before_unlinking_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.link(staging, final)
    import auraly_pipeline.flow.artifacts as artifacts

    events: list[str] = []
    original_sync = artifacts._sync_file_and_directory
    original_delete = getattr(artifacts, "_delete_bound_staging", None)

    def observe_sync(path: Path) -> None:
        assert path == final
        events.append("sync-final")
        original_sync(path)

    def observe_delete(binding: object) -> None:
        assert events == ["sync-final"]
        events.append("unlink-staging")
        assert original_delete is not None
        original_delete(binding)

    monkeypatch.setattr(artifacts, "_sync_file_and_directory", observe_sync)
    monkeypatch.setattr(artifacts, "_delete_bound_staging", observe_delete, raising=False)
    publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert events == ["sync-final", "unlink-staging"]
    assert not staging.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX lacks identity-bound deletion")
def test_posix_matching_residue_syncs_final_before_failing_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.link(staging, final)
    before = final.read_bytes()
    import auraly_pipeline.flow.artifacts as artifacts

    events: list[str] = []
    original_sync = artifacts._sync_file_and_directory

    def observe_sync(path: Path) -> None:
        assert path == final
        events.append("sync-final")
        original_sync(path)

    monkeypatch.setattr(artifacts, "_sync_file_and_directory", observe_sync)

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    quarantines = list(staging.parent.glob(".cleanup-*.part"))
    assert events == ["sync-final"]
    assert final.read_bytes() == before
    assert not staging.exists()
    assert len(quarantines) == 1
    assert quarantines[0].samefile(final)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor semantics")
@pytest.mark.parametrize("recovery", [False, True])
def test_posix_cleanup_parent_substitution_fails_closed_and_preserves_staging_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery: bool
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    if recovery:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.link(staging, final)
    original_parent = final.parent
    preserved_parent = tmp_path / "preserved-generation"
    outside = tmp_path / "outside"
    outside.mkdir()
    import auraly_pipeline.flow.artifacts as artifacts
    def replace_parent_after_cleanup_revalidation() -> None:
        original_parent.rename(preserved_parent)
        outside_staging = outside / ".staging" / staging.name
        outside_staging.parent.mkdir(parents=True)
        shutil.copyfile(preserved_parent / ".staging" / staging.name, outside_staging)
        _replace_directory_with_link(original_parent, outside)
        assert artifacts._path_is_link_or_junction(original_parent)
        assert artifacts._directory_identity(preserved_parent / ".staging") != artifacts._directory_identity(
            outside / ".staging"
        )
        assert (preserved_parent / ".staging" / staging.name).exists()
        assert outside_staging.exists()

    monkeypatch.setattr(
        artifacts,
        "_after_flow_artifact_cleanup_revalidation",
        replace_parent_after_cleanup_revalidation,
        raising=False,
    )

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert (preserved_parent / ".staging" / staging.name).exists()
    assert (outside / ".staging" / staging.name).exists()
    assert not (outside / final.name).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor semantics")
def test_posix_cleanup_child_swap_preserves_replacement_after_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    replacement = staging.with_name("replacement.part")
    replacement_bytes = b"attacker-controlled replacement"
    replacement.write_bytes(replacement_bytes)
    import auraly_pipeline.flow.artifacts as artifacts

    def replace_child_after_cleanup_revalidation() -> None:
        os.replace(replacement, staging)

    monkeypatch.setattr(
        artifacts,
        "_after_flow_artifact_cleanup_revalidation",
        replace_child_after_cleanup_revalidation,
        raising=False,
    )

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.exists()
    assert any(path.read_bytes() == replacement_bytes for path in staging.parent.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor semantics")
def test_posix_cleanup_quarantine_swap_after_identity_check_never_deletes_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    replacement = staging.with_name("replacement.part")
    replacement_bytes = b"attacker-controlled quarantine replacement"
    replacement.write_bytes(replacement_bytes)
    preserved_validated = staging.with_name("validated-staging-residue.part")
    quarantine_names: list[str] = []
    import auraly_pipeline.flow.artifacts as artifacts

    def swap_quarantine_after_identity_check(descriptor: int, quarantine_name: str) -> None:
        os.rename(
            quarantine_name,
            preserved_validated.name,
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
        )
        os.rename(
            replacement.name,
            quarantine_name,
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
        )
        quarantine_names.append(quarantine_name)

    monkeypatch.setattr(
        artifacts,
        "_after_flow_artifact_cleanup_quarantine_identity_check",
        swap_quarantine_after_identity_check,
        raising=False,
    )

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert quarantine_names
    quarantine = staging.parent / quarantine_names[0]
    assert quarantine.read_bytes() == replacement_bytes
    assert preserved_validated.samefile(final)
    assert final.exists()


def test_posix_cleanup_model_never_name_unlinks_a_post_check_quarantine_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import auraly_pipeline.flow.artifacts as artifacts

    validated = object()
    replacement = object()
    namespace: dict[str, object] = {}
    quarantine_name = ".cleanup-model.part"
    parent_identity = artifacts._FileIdentity(device=101, inode=202)
    binding = artifacts._StagingCleanupBinding(
        staging_name="staging.part",
        parent_identity=parent_identity,
        artifact_identity=artifacts._FileIdentity(device=303, inode=404),
        descriptor=11,
        parent_descriptor=11,
        windows_delete_handle=False,
    )

    def move_then_swap_after_identity_check(_binding: object) -> str:
        namespace[quarantine_name] = validated
        namespace[quarantine_name] = replacement
        return quarantine_name

    def unlink_by_name(name: str, *, dir_fd: int) -> None:
        assert dir_fd == binding.descriptor
        del namespace[name]

    monkeypatch.setattr(
        artifacts.os,
        "fstat",
        lambda _descriptor: SimpleNamespace(
            st_dev=parent_identity.device,
            st_ino=parent_identity.inode,
        ),
    )
    monkeypatch.setattr(
        artifacts, "_move_posix_staging_to_quarantine", move_then_swap_after_identity_check
    )
    monkeypatch.setattr(artifacts.os, "unlink", unlink_by_name)
    monkeypatch.setattr(artifacts, "_close_cleanup_binding", lambda _binding: None)

    with pytest.raises(FlowArtifactInvalidError):
        artifacts._delete_bound_staging(binding)

    assert namespace[quarantine_name] is replacement


def test_crash_after_staging_before_link_preserves_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    import auraly_pipeline.flow.artifacts as artifacts

    def interrupt_before_link() -> None:
        raise OSError("injected interruption")

    monkeypatch.setattr(artifacts, "_before_flow_artifact_link", interrupt_before_link, raising=False)
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert staging.exists()
    assert not final.exists()


def test_staging_identity_replacement_before_link_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    replacement = staging.with_name("replacement.part")
    _write_image(replacement, "PNG", axis=2049)
    import auraly_pipeline.flow.artifacts as artifacts

    def replace_staging_before_link() -> None:
        os.replace(replacement, staging)

    monkeypatch.setattr(
        artifacts, "_before_flow_artifact_link", replace_staging_before_link, raising=False
    )
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert staging.exists()
    assert not final.exists()


def test_parent_link_substitution_before_link_is_detected_and_preserves_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    original_parent = final.parent
    preserved_parent = tmp_path / "preserved-generation"
    outside = tmp_path / "outside"
    outside.mkdir()
    import auraly_pipeline.flow.artifacts as artifacts

    def replace_parent_before_link() -> None:
        original_parent.rename(preserved_parent)
        outside_staging = outside / ".staging" / staging.name
        outside_staging.parent.mkdir(parents=True)
        shutil.copyfile(preserved_parent / ".staging" / staging.name, outside_staging)
        _replace_directory_with_link(original_parent, outside)

    monkeypatch.setattr(
        artifacts, "_before_flow_artifact_link", replace_parent_before_link, raising=False
    )
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert (preserved_parent / ".staging" / staging.name).exists()
    assert not (outside / final.name).exists()


def test_parent_link_substitution_after_revalidation_fails_closed_after_escaped_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    original_parent = final.parent
    preserved_parent = tmp_path / "preserved-generation"
    outside = tmp_path / "outside"
    outside.mkdir()
    import auraly_pipeline.flow.artifacts as artifacts

    def replace_parent_after_revalidation() -> None:
        original_parent.rename(preserved_parent)
        outside_staging = outside / ".staging" / staging.name
        outside_staging.parent.mkdir(parents=True)
        shutil.copyfile(preserved_parent / ".staging" / staging.name, outside_staging)
        _replace_directory_with_link(original_parent, outside)

    monkeypatch.setattr(
        artifacts,
        "_after_flow_artifact_revalidation_before_link",
        replace_parent_after_revalidation,
        raising=False,
    )
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert (preserved_parent / ".staging" / staging.name).exists()
    assert (outside / ".staging" / staging.name).exists()
    assert (outside / final.name).exists()


def test_crash_after_link_before_final_sync_preserves_both_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    import auraly_pipeline.flow.artifacts as artifacts

    def interrupt_final_sync(path: Path) -> None:
        assert path == final
        raise OSError("injected interruption")

    monkeypatch.setattr(artifacts, "_sync_file_and_directory", interrupt_final_sync)
    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert staging.samefile(final)
    assert staging.exists()
    assert final.exists()


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


def test_inspect_rejects_jpeg_polyglot_with_a_second_eoi_at_eof(tmp_path: Path) -> None:
    fixture = _write_image(tmp_path / "jpeg-polyglot.jpg", "JPEG")
    fixture.write_bytes(fixture.read_bytes() + b"MZ executable marker" + b"\xff\xd9")

    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture)


def test_inspect_rejects_an_oversized_file_before_decoding(tmp_path: Path) -> None:
    fixture = tmp_path / "oversized.part"
    with fixture.open("wb") as stream:
        stream.truncate(100_000_001)

    with pytest.raises(FlowArtifactInvalidError):
        inspect_flow_artifact(fixture)
