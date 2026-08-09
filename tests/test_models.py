from copy import deepcopy

import pytest
from pydantic import ValidationError

from auraly_pipeline.models import EditManifest


def valid_manifest_data() -> dict:
    return {
        "schemaVersion": "1.0",
        "project": {
            "reelId": "susan-sign-001",
            "character": "susan-smith",
            "template": "susan-hard-truth-v1",
        },
        "source": {
            "video": "source/heygen.mp4",
            "copy": "source/copy.md",
            "durationSec": 12.5,
        },
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "headline": {
            "text": "DON'T YOU DARE CALL THIS A SIGN",
            "start": 0,
            "end": 3.2,
            "spoken": False,
        },
        "render": {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "format": "mp4",
            "codec": "h264",
        },
    }


def test_valid_manifest_uses_camel_case_contract() -> None:
    manifest = EditManifest.model_validate(valid_manifest_data())

    payload = manifest.model_dump(by_alias=True, mode="json")

    assert payload["schemaVersion"] == "1.0"
    assert payload["project"]["reelId"] == "susan-sign-001"
    assert payload["headline"]["spoken"] is False
    assert payload["cuts"] == []
    assert payload["broll"] == []


def test_headline_can_never_be_marked_as_spoken() -> None:
    data = deepcopy(valid_manifest_data())
    data["headline"]["spoken"] = True

    with pytest.raises(ValidationError, match="headline must remain visual-only"):
        EditManifest.model_validate(data)


def test_manifest_rejects_paths_outside_reel_workspace() -> None:
    data = deepcopy(valid_manifest_data())
    data["source"]["video"] = "../private.mp4"

    with pytest.raises(ValidationError, match="workspace-relative path"):
        EditManifest.model_validate(data)


def test_timeline_event_cannot_exceed_source_duration() -> None:
    data = deepcopy(valid_manifest_data())
    data["broll"] = [
        {
            "start": 10,
            "end": 13,
            "asset": "broll/proof.mp4",
            "license": "owned",
        }
    ]

    with pytest.raises(ValidationError, match="exceeds source duration"):
        EditManifest.model_validate(data)


def test_timeline_event_end_must_be_after_start() -> None:
    data = deepcopy(valid_manifest_data())
    data["punchIns"] = [{"start": 5, "end": 4, "scale": 1.08}]

    with pytest.raises(ValidationError, match="end must be after start"):
        EditManifest.model_validate(data)


def test_broll_requires_non_empty_license() -> None:
    data = deepcopy(valid_manifest_data())
    data["broll"] = [
        {"start": 2, "end": 4, "asset": "broll/proof.mp4", "license": ""}
    ]

    with pytest.raises(ValidationError, match="at least 1 character"):
        EditManifest.model_validate(data)


def test_rendered_manifest_requires_human_approval() -> None:
    data = deepcopy(valid_manifest_data())
    data["review"] = {"status": "rendered"}

    with pytest.raises(ValidationError, match="human approval"):
        EditManifest.model_validate(data)
