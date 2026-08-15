from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images.domain import ImageGenerateRequest
from auraly_pipeline.images.handler import deterministic_png_bytes
from auraly_pipeline.images.service import ImageService
from auraly_pipeline.jobs.service import JobService
from tests.test_campaign_domain import valid_campaign_data


def _campaign(database: Path) -> tuple[str, str]:
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    return campaign.campaign_id, campaign.scene_variants[0].scene_variant_id


def _submit_generation(
    database: Path,
    work_root: Path,
    *,
    campaign_id: str,
    scene_variant_id: str,
    key: str,
) -> tuple[str, int]:
    images = ImageService.for_database(database, work_root=work_root)
    submitted = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign_id,
            scene_variant_id=scene_variant_id,
            idempotency_key=key,
            prompt_snapshot="A moonlit studio",
        )
    )
    images.close()
    return submitted.generation.image_generation_id, submitted.generation.generation_number


def _run_image_job(database: Path, work_root: Path) -> None:
    jobs = JobService.for_database(database, work_root=work_root)
    completed = jobs.worker_once("image-handler")
    assert completed is not None
    assert completed.status == "completed"
    jobs.close()


def _png_details(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    idat = bytearray()
    dimensions: tuple[int, int] | None = None
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        chunk_crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
        assert zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == chunk_crc
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            assert (bit_depth, color_type, compression, filtering, interlace) == (8, 2, 0, 0, 0)
            dimensions = (width, height)
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            assert offset == len(data)
            break
    assert dimensions is not None
    width, height = dimensions
    decoded = zlib.decompress(idat)
    assert len(decoded) == height * (1 + width * 3)
    assert all(decoded[row * (1 + width * 3)] == 0 for row in range(height))
    return dimensions


def _link_directory_outside_work_root(path: Path, outside_root: Path) -> None:
    symlink_error = ""
    try:
        path.symlink_to(outside_root, target_is_directory=True)
        return
    except OSError as exc:
        if os.name != "nt":
            raise
        symlink_error = str(exc)
    junction = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(path), str(outside_root)],
        capture_output=True,
        check=False,
    )
    if junction.returncode != 0:
        pytest.skip(
            "Windows cannot create the required directory symlink or junction: "
            f"{symlink_error}; {junction.stderr.decode(errors='replace')}"
        )


def test_fake_handler_creates_exactly_two_valid_distinct_png_candidates(tmp_path: Path) -> None:
    database = tmp_path / "images.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    generation_id, _generation_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-handler-candidates",
    )

    _run_image_job(database, work_root)

    images = ImageService.for_database(database, work_root=work_root)
    candidates = images.list_candidates(generation_id)
    assert [candidate.candidate_index for candidate in candidates] == [0, 1]
    artifacts = [(work_root / candidate.source_path).read_bytes() for candidate in candidates]
    assert [_png_details(artifact) for artifact in artifacts]
    assert artifacts[0] != artifacts[1]
    assert artifacts == [
        deterministic_png_bytes(generation_id, 0),
        deterministic_png_bytes(generation_id, 1),
    ]
    images.close()


def test_fake_handler_persists_actual_sha_dimensions_format_and_size(tmp_path: Path) -> None:
    database = tmp_path / "facts.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    generation_id, _generation_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-handler-facts",
    )

    _run_image_job(database, work_root)

    images = ImageService.for_database(database, work_root=work_root)
    for candidate in images.list_candidates(generation_id):
        artifact = (work_root / candidate.source_path).read_bytes()
        assert candidate.sha256 == hashlib.sha256(artifact).hexdigest()
        assert (candidate.width, candidate.height) == _png_details(artifact)
        assert candidate.format == "png"
        assert candidate.size_bytes == len(artifact)
    images.close()


def test_fake_handler_uses_generation_scoped_non_overwriting_paths(tmp_path: Path) -> None:
    database = tmp_path / "paths.db"
    work_root = tmp_path / "work"
    campaign_id, scene_variant_id = _campaign(database)
    first_id, first_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-handler-first",
    )
    _run_image_job(database, work_root)
    second_id, second_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-handler-second",
    )
    _run_image_job(database, work_root)

    images = ImageService.for_database(database, work_root=work_root)
    first_paths = {candidate.source_path for candidate in images.list_candidates(first_id)}
    second_paths = {candidate.source_path for candidate in images.list_candidates(second_id)}
    assert first_paths.isdisjoint(second_paths)
    assert all(f"generation-{first_number:04d}" in path for path in first_paths)
    assert all(f"generation-{second_number:04d}" in path for path in second_paths)
    images.close()

    third_id, third_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-handler-conflict",
    )
    conflict = (
        work_root
        / "campaigns"
        / campaign_id
        / "images"
        / scene_variant_id
        / f"generation-{third_number:04d}"
        / "candidate-0000.png"
    )
    conflict.parent.mkdir(parents=True)
    conflict.write_bytes(b"conflicting artifact")

    jobs = JobService.for_database(database, work_root=work_root)
    failed = jobs.worker_once("image-handler")
    assert failed is not None
    assert failed.status == "failed"
    jobs.close()
    images = ImageService.for_database(database, work_root=work_root)
    assert images.list_candidates(third_id) == []
    assert conflict.read_bytes() == b"conflicting artifact"
    images.close()


def test_fake_handler_rejects_symlinked_generation_path_outside_configured_work_root(
    tmp_path: Path,
) -> None:
    database = tmp_path / "symlink.db"
    work_root = tmp_path / "work"
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    campaign_id, scene_variant_id = _campaign(database)
    generation_id, generation_number = _submit_generation(
        database,
        work_root,
        campaign_id=campaign_id,
        scene_variant_id=scene_variant_id,
        key="image-handler-symlink",
    )
    generation_path = (
        work_root
        / "campaigns"
        / campaign_id
        / "images"
        / scene_variant_id
        / f"generation-{generation_number:04d}"
    )
    generation_path.parent.mkdir(parents=True)
    _link_directory_outside_work_root(generation_path, outside_root)

    jobs = JobService.for_database(database, work_root=work_root)
    failed = jobs.worker_once("image-handler")
    assert failed is not None
    assert failed.status == "failed"
    jobs.close()
    assert not (outside_root / "candidate-0000.png").exists()
    images = ImageService.for_database(database, work_root=work_root)
    assert images.list_candidates(generation_id) == []
    images.close()
