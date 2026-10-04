from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from auraly_pipeline.images.import_batch import ImageImportError, ImageImportItem, ImageImportService
from tests.test_image_import_batch import _campaign


@pytest.fixture
def prepared(tmp_path: Path) -> Iterator[tuple[ImageImportService, str, Path, list[ImageImportItem]]]:
    database = tmp_path / "campaign.db"
    campaign_id, variants = _campaign(database)
    directory = tmp_path / "work" / "manual inbox"
    service = ImageImportService(database, work_root=tmp_path / "work")
    service.prepare_directory(campaign_id, directory)
    items = [ImageImportItem(variant_id=variant, path=f"images/{variant}.png") for variant in variants]
    try:
        yield service, campaign_id, directory, items
    finally:
        service.close()


def test_publish_preserves_template_without_inspecting_images_and_replays(
    prepared: tuple[ImageImportService, str, Path, list[ImageImportItem]],
) -> None:
    service, campaign_id, directory, items = prepared
    assert callable(getattr(service, "publish_manifest", None)), "publication capability missing"
    template = directory / "image-import.json"
    original = template.read_bytes()
    result = service.publish_manifest(campaign_id, directory, items)
    assert template.read_bytes() == original
    assert result.manifest_sha256 == hashlib.sha256(result.manifest_path.read_bytes()).hexdigest()
    assert result.manifest_path.name == f"image-import-{result.manifest_sha256}.json"
    payload = json.loads(result.manifest_path.read_bytes())
    assert payload["schemaVersion"] == "1.0"
    assert payload["campaignId"] == campaign_id
    assert payload["approveImported"] is False and payload["approvedBy"] is None
    assert [row["variantId"] for row in payload["items"]] == sorted(item.variant_id for item in items)
    assert not result.manifest_path.read_bytes().endswith(b"\n")
    assert service.publish_manifest(campaign_id, directory, list(reversed(items))) == result
    assert len(list(directory.glob("image-import-*.json"))) == 1
    assert list((directory / "images").iterdir()) == []


def test_published_unicode_sources_import_and_preserve_provenance(
    prepared: tuple[ImageImportService, str, Path, list[ImageImportItem]],
) -> None:
    service, campaign_id, directory, original = prepared
    items = [ImageImportItem(variant_id=item.variant_id, path=f"images/visão {index}.png") for index, item in enumerate(original)]
    for index, item in enumerate(items):
        Image.new("RGB", (360, 640), (index * 40, 60, 90)).save(directory / item.path)
    published = service.publish_manifest(campaign_id, directory, items)
    result = service.execute(service.plan(published.manifest_path))
    assert result.created == len(items)


@pytest.mark.parametrize("coverage", ["missing", "duplicate", "unknown"])
def test_publish_requires_exact_scene_coverage(
    prepared: tuple[ImageImportService, str, Path, list[ImageImportItem]], coverage: str,
) -> None:
    service, campaign_id, directory, items = prepared
    assert callable(getattr(service, "publish_manifest", None)), "publication capability missing"
    bad = items[:-1] if coverage == "missing" else items + [
        items[0] if coverage == "duplicate" else ImageImportItem(variant_id="unknown", path="images/a.png")
    ]
    with pytest.raises(ImageImportError):
        service.publish_manifest(campaign_id, directory, bad)
    assert list(directory.glob("image-import-*.json")) == []


def test_publish_rejects_template_of_another_campaign_and_outside_work_root(
    prepared: tuple[ImageImportService, str, Path, list[ImageImportItem]], tmp_path: Path,
) -> None:
    service, campaign_id, directory, items = prepared
    assert callable(getattr(service, "publish_manifest", None)), "publication capability missing"
    template = directory / "image-import.json"
    payload = json.loads(template.read_bytes())
    payload["campaignId"] = "another-campaign"
    template.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ImageImportError):
        service.publish_manifest(campaign_id, directory, items)
    outside = tmp_path / "outside"
    service.prepare_directory(campaign_id, outside)
    with pytest.raises(ImageImportError):
        service.publish_manifest(campaign_id, outside, items)
    assert list(outside.glob("image-import-*.json")) == []


def test_publish_conflict_preserves_existing_file_and_cleans_staging(
    prepared: tuple[ImageImportService, str, Path, list[ImageImportItem]],
) -> None:
    service, campaign_id, directory, items = prepared
    assert callable(getattr(service, "publish_manifest", None)), "publication capability missing"
    result = service.publish_manifest(campaign_id, directory, items)
    result.manifest_path.write_bytes(b"conflicting evidence")
    with pytest.raises(ImageImportError):
        service.publish_manifest(campaign_id, directory, items)
    assert result.manifest_path.read_bytes() == b"conflicting evidence"
    assert not list(directory.glob("*.tmp"))


def test_publish_normalizes_windows_bars_and_preserves_unicode(
    prepared: tuple[ImageImportService, str, Path, list[ImageImportItem]],
) -> None:
    service, campaign_id, directory, items = prepared
    assert callable(getattr(service, "publish_manifest", None)), "publication capability missing"
    items[0] = ImageImportItem(variant_id=items[0].variant_id, path="images\\ação final.png")
    result = service.publish_manifest(campaign_id, directory, items)
    assert next(row.path for row in result.items if row.variant_id == items[0].variant_id) == "images/ação final.png"


@pytest.mark.parametrize("path", ["../a.png", "C:/a.png", "C:a.png", "//server/share/a.png", "/a.png"])
def test_manifest_rejects_absolute_and_traversal_paths(path: str) -> None:
    with pytest.raises(ValueError):
        ImageImportItem(variant_id="scene", path=path)


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_publish_rejects_linked_source_parent(
    prepared: tuple[ImageImportService, str, Path, list[ImageImportItem]], tmp_path: Path, link_kind: str,
) -> None:
    service, campaign_id, directory, items = prepared
    assert callable(getattr(service, "publish_manifest", None)), "publication capability missing"
    outside = tmp_path / "external images"
    outside.mkdir()
    linked = directory / "images" / "escape"
    if link_kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction only")
        link_text, target_text = str(linked).replace("'", "''"), str(outside).replace("'", "''")
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"New-Item -ItemType Junction -Path '{link_text}' -Target '{target_text}'"],
                       check=True, capture_output=True)
    else:
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink privileges unavailable")
    try:
        bad = [ImageImportItem(variant_id=item.variant_id, path=f"images/escape/{item.variant_id}.png") for item in items]
        with pytest.raises(ImageImportError):
            service.publish_manifest(campaign_id, directory, bad)
    finally:
        if link_kind == "junction":
            linked.rmdir()  # Remove the link only, never recursively follow its target.
        else:
            linked.unlink()
    assert outside.is_dir() and list(outside.iterdir()) == []
