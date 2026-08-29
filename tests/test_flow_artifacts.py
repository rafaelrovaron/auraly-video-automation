"""Real-file security tests for Flow download artifact handling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
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


def test_exclusive_publish_hard_links_then_removes_staging(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    staged_bytes = staging.read_bytes()

    facts = publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == staged_bytes
    assert facts.sha256 == hashlib.sha256(staged_bytes).hexdigest()
    assert not staging.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission boundary")
def test_posix_staging_directory_is_repaired_to_private_owner_access(tmp_path: Path) -> None:
    staging, _ = _paths(tmp_path)
    staging.parent.chmod(0o755)

    second_staging, _ = _paths(tmp_path)

    metadata = second_staging.parent.stat()
    get_effective_uid = getattr(os, "geteuid", None)
    assert get_effective_uid is not None
    assert metadata.st_uid == get_effective_uid()
    assert stat.S_IMODE(metadata.st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission boundary")
@pytest.mark.parametrize("recovery", [False, True])
def test_posix_publish_repairs_existing_staging_permissions_before_cleanup(
    tmp_path: Path, recovery: bool
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    if recovery:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.link(staging, final)
    before = staging.read_bytes()
    staging.parent.chmod(0o755)

    facts = publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert facts.sha256 == hashlib.sha256(before).hexdigest()
    assert final.read_bytes() == before
    assert not staging.exists()
    assert stat.S_IMODE(final.parent.joinpath(".staging").stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission boundary")
def test_posix_publish_rejects_group_writable_staging_before_linking_final(
    tmp_path: Path,
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    staging.parent.chmod(0o770)

    with pytest.raises(FlowArtifactInvalidError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert staging.exists()
    assert not final.exists()


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


def test_retry_recovers_one_exact_legacy_cleanup_residue_without_changing_final(
    tmp_path: Path,
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.link(staging, final)
    cleanup = staging.with_name(f".cleanup-{'a' * 32}.part")
    staging.rename(cleanup)
    before = final.read_bytes()

    facts = publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == before
    assert facts.sha256 == hashlib.sha256(before).hexdigest()
    assert not staging.exists()
    assert not cleanup.exists()


def test_retry_preserves_mismatched_legacy_cleanup_residue(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.link(staging, final)
    staging.unlink()
    cleanup = staging.with_name(f".cleanup-{'b' * 32}.part")
    _write_image(cleanup, "PNG", axis=2049)
    before = final.read_bytes()
    cleanup_before = cleanup.read_bytes()

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == before
    assert cleanup.read_bytes() == cleanup_before
    assert not staging.exists()


def test_retry_preserves_ambiguous_exact_legacy_cleanup_residues(tmp_path: Path) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.link(staging, final)
    staging.unlink()
    cleanup_a = staging.with_name(f".cleanup-{'a' * 32}.part")
    cleanup_b = staging.with_name(f".cleanup-{'b' * 32}.part")
    os.link(final, cleanup_a)
    os.link(final, cleanup_b)
    before = final.read_bytes()

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert final.read_bytes() == before
    assert cleanup_a.samefile(final)
    assert cleanup_b.samefile(final)
    assert not staging.exists()


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


def test_posix_cleanup_model_removes_the_identity_validated_private_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import auraly_pipeline.flow.artifacts as artifacts

    events: list[str] = []
    parent_identity = artifacts._FileIdentity(device=101, inode=202)
    artifact_identity = artifacts._FileIdentity(device=303, inode=404)
    binding = artifacts._StagingCleanupBinding(
        staging_name="staging.part",
        parent_identity=parent_identity,
        artifact_identity=artifact_identity,
        descriptor=11,
        parent_descriptor=11,
        windows_delete_handle=False,
    )

    def validate_entry(actual_binding: object) -> None:
        assert actual_binding == binding
        events.append("identity-validated")

    def reject_quarantine(_binding: object) -> str:
        raise AssertionError("private staging cleanup must retain its recoverable name until unlink")

    monkeypatch.setattr(
        artifacts.os,
        "fstat",
        lambda _descriptor: SimpleNamespace(
            st_dev=parent_identity.device,
            st_ino=parent_identity.inode,
        ),
    )
    monkeypatch.setattr(
        artifacts,
        "_assert_posix_staging_entry_current",
        validate_entry,
        raising=False,
    )
    monkeypatch.setattr(
        artifacts,
        "_assert_private_posix_directory_metadata",
        lambda _metadata: events.append("private-parent"),
    )
    monkeypatch.setattr(
        artifacts,
        "_move_posix_staging_to_quarantine",
        reject_quarantine,
        raising=False,
    )
    monkeypatch.setattr(
        artifacts.os,
        "unlink",
        lambda name, *, dir_fd: events.append(f"unlink:{dir_fd}:{name}"),
    )
    monkeypatch.setattr(
        artifacts,
        "_close_cleanup_binding",
        lambda _binding: events.append("close"),
    )

    artifacts._delete_bound_staging(binding)

    assert events == [
        "private-parent",
        "identity-validated",
        "unlink:11:staging.part",
        "close",
    ]


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
    outside = tmp_path.parent / f"{tmp_path.name}-outside-parent"
    outside.mkdir()
    import auraly_pipeline.flow.artifacts as artifacts

    def replace_parent_after_cleanup_revalidation() -> None:
        original_parent.rename(preserved_parent)
        outside_staging = outside / ".staging" / staging.name
        outside_staging.parent.mkdir(parents=True)
        shutil.copyfile(preserved_parent / ".staging" / staging.name, outside_staging)
        _replace_directory_with_link(original_parent, outside)
        assert artifacts._path_is_link_or_junction(original_parent)
        assert artifacts._directory_identity(
            preserved_parent / ".staging"
        ) != artifacts._directory_identity(
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permission semantics")
@pytest.mark.parametrize("recovery", [False, True])
def test_posix_cleanup_permission_loss_after_binding_preserves_staging_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery: bool
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    if recovery:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.link(staging, final)
    import auraly_pipeline.flow.artifacts as artifacts

    def remove_private_boundary() -> None:
        staging.parent.chmod(0o777)

    monkeypatch.setattr(
        artifacts,
        "_after_flow_artifact_cleanup_revalidation",
        remove_private_boundary,
        raising=False,
    )

    with pytest.raises(FlowArtifactConflictError):
        publish_flow_artifact_exclusive(staging, final, trusted_root=tmp_path)

    assert staging.exists()
    assert final.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor semantics")
def test_posix_cleanup_child_swap_preserves_replacement_after_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, final = _valid_staging_and_final(tmp_path)
    replacement = staging.with_name("replacement.part")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-child.part"
    replacement_bytes = b"attacker-controlled replacement"
    outside.write_bytes(replacement_bytes)
    os.link(outside, replacement)
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
    assert outside.read_bytes() == replacement_bytes
    assert any(path.samefile(outside) for path in staging.parent.iterdir())


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
