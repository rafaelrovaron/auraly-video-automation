from __future__ import annotations

from pathlib import Path
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.campaigns.persistence import create_sqlite_engine

from auraly_pipeline.flow.artifacts import (
    FlowArtifactConflictError,
    FlowArtifactInvalidError,
    inspect_flow_artifact,
    inspect_image_artifact,
    publish_image_artifact_exclusive,
    resolve_trusted_image_path,
)
from auraly_pipeline.images.import_batch import (
    ImageImportBatch,
    ImageImportError,
    ImageImportService,
    ImageImportValidationError,
)
from auraly_pipeline.images.domain import ImageCandidate
from auraly_pipeline.images.repository import ImageRepository
from tests.test_campaign_domain import valid_campaign_data


def _write_image(path: Path, image_format: str = "PNG", *, size: tuple[int, int] = (360, 640)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(20, 40, 80)).save(path, format=image_format)
    return path


def _campaign(database: Path) -> tuple[str, list[str]]:
    service = CampaignService.for_database(database)
    campaign = service.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    service.close()
    return campaign.campaign_id, [variant.variant_id for variant in campaign.scene_variants]


def _manifest(directory: Path, campaign_id: str, variant_ids: list[str]) -> Path:
    directory.mkdir(parents=True)
    items = []
    for index, variant_id in enumerate(variant_ids):
        suffix, image_format = [(".png", "PNG"), (".jpg", "JPEG"), (".webp", "WEBP")][index]
        relative = f"images/{variant_id}{suffix}"
        _write_image(directory / relative, image_format)
        items.append({"variantId": variant_id, "path": relative})
    manifest = directory / "image-import.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "campaignId": campaign_id,
                "approveImported": True,
                "approvedBy": "rafael",
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_manifest_contract_is_closed_versioned_and_requires_approval_actor() -> None:
    valid = {
        "schemaVersion": "1.0",
        "campaignId": "campaign-1",
        "approveImported": True,
        "approvedBy": "rafael",
        "items": [{"variantId": "scene-one", "path": "images/scene.png"}],
    }

    assert ImageImportBatch.model_validate(valid).items[0].variant_id == "scene-one"
    for patch in (
        {"schemaVersion": "2.0"},
        {"approvedBy": None},
        {"unexpected": True},
    ):
        with pytest.raises(ValidationError):
            ImageImportBatch.model_validate(valid | patch)
    with pytest.raises(ValidationError):
        ImageImportBatch.model_validate(valid | {"approveImported": False})


@pytest.mark.parametrize("path", ["", "../image.png", "/tmp/image.png", "C:\\image.png"])
def test_manifest_rejects_empty_absolute_or_traversing_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        ImageImportBatch.model_validate(
            {
                "schemaVersion": "1.0",
                "campaignId": "campaign-1",
                "approveImported": False,
                "approvedBy": None,
                "items": [{"variantId": "scene-one", "path": path}],
            }
        )


def test_prepare_directory_creates_sorted_template_and_refuses_overwrite(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, variants = _campaign(database)
    service = ImageImportService.for_database(database, work_root=tmp_path / "work")
    output = tmp_path / "imports" / campaign_id

    prepared = service.prepare_directory(campaign_id, output)

    payload = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
    assert prepared.variant_count == 3
    assert prepared.images_path.is_dir()
    assert [item["variantId"] for item in payload["items"]] == sorted(variants)
    assert {item["path"] for item in payload["items"]} == {""}
    with pytest.raises(ImageImportError):
        service.prepare_directory(campaign_id, output)
    service.close()


def test_plan_valid_batch_is_deterministic_and_has_no_side_effects(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, variants = _campaign(database)
    manifest = _manifest(tmp_path / "incoming", campaign_id, variants)
    work_root = tmp_path / "work"
    service = ImageImportService.for_database(database, work_root=work_root)

    plan = service.plan(manifest)

    assert [item.variant_id for item in plan.items] == sorted(variants)
    assert {item.action for item in plan.items} == {"create"}
    assert {item.format for item in plan.items} == {"png", "jpeg", "webp"}
    assert all(item.height > item.width for item in plan.items)
    assert not work_root.exists()
    service.close()


def test_plan_reports_coverage_duplicates_unknown_and_orientation_together(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, variants = _campaign(database)
    incoming = tmp_path / "incoming"
    manifest = _manifest(incoming, campaign_id, variants)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["items"] = [
        payload["items"][0],
        payload["items"][0],
        {"variantId": "unknown", "path": payload["items"][1]["path"]},
    ]
    _write_image(incoming / payload["items"][0]["path"], size=(640, 360))
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    service = ImageImportService.for_database(database, work_root=tmp_path / "work")

    with pytest.raises(ImageImportValidationError) as caught:
        service.plan(manifest)

    codes = {issue.code for issue in caught.value.issues}
    assert "image_import_variant_coverage_invalid" in codes
    assert "image_import_orientation_invalid" in codes
    assert "image_import_source_path_invalid" in codes
    assert not (tmp_path / "work").exists()
    service.close()


def test_plan_reports_invalid_media_separately_from_unsafe_path(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, variants = _campaign(database)
    manifest = _manifest(tmp_path / "incoming", campaign_id, variants)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    corrupt = Path(manifest.parent, payload["items"][0]["path"])
    corrupt.write_bytes(b"not an image")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    service = ImageImportService.for_database(database, work_root=tmp_path / "work")

    with pytest.raises(ImageImportValidationError) as caught:
        service.plan(manifest)

    assert "image_import_media_invalid" in {issue.code for issue in caught.value.issues}
    service.close()


def test_plan_reuses_same_manual_hash_and_blocks_different_approved_hash(tmp_path: Path) -> None:
    database = tmp_path / "auraly.db"
    campaign_id, variants = _campaign(database)
    manifest = _manifest(tmp_path / "incoming", campaign_id, variants)
    service = ImageImportService.for_database(database, work_root=tmp_path / "work")
    initial = service.plan(manifest)
    first = initial.items[0]
    now = datetime.now(UTC)
    engine = create_sqlite_engine(database)
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        ImageRepository.create_candidate_in_session(
            session,
            ImageCandidate(
                image_candidate_id=str(uuid4()),
                scene_variant_id=first.scene_variant_id,
                source_kind="manual_import",
                import_manifest_sha256=initial.manifest_sha256,
                import_source_path=first.source_relative_path,
                candidate_index=0,
                source_path=first.destination_path,
                sha256=first.sha256,
                width=first.width,
                height=first.height,
                size_bytes=first.size_bytes,
                format=first.format,
                review_status="pending_review",
                created_at=now,
                updated_at=now,
            ),
        )
        session.commit()

    reused = service.plan(manifest)
    assert next(item for item in reused.items if item.variant_id == first.variant_id).action == "reuse"

    conflicting = initial.items[1]
    with sessions() as session:
        ImageRepository.create_candidate_in_session(
            session,
            ImageCandidate(
                image_candidate_id=str(uuid4()),
                scene_variant_id=conflicting.scene_variant_id,
                source_kind="manual_import",
                import_manifest_sha256="f" * 64,
                import_source_path="images/old.png",
                candidate_index=0,
                source_path="campaigns/old.png",
                sha256="e" * 64,
                width=360,
                height=640,
                size_bytes=100,
                format="png",
                review_status="approved",
                approved_at=now,
                approved_by="rafael",
                created_at=now,
                updated_at=now,
            ),
        )
        session.commit()
    engine.dispose()

    with pytest.raises(ImageImportValidationError) as caught:
        service.plan(manifest)

    assert any(
        issue.code == "image_import_approved_candidate_conflict"
        and issue.variant_id == conflicting.variant_id
        for issue in caught.value.issues
    )
    service.close()


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
