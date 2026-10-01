from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from auraly_pipeline.editing.domain import EditManifestV2, EditOverrides, EditProfile, EditResolveRequest
from auraly_pipeline.models import EditManifest
from tests.editing_helpers import profile_data, request_data


@pytest.mark.parametrize("field", ["headlineText", "source", "campaignId"])
def test_profile_rejects_campaign_text_and_source(field: str) -> None:
    data = profile_data()
    data[field] = "not a style"
    with pytest.raises(ValidationError) as exc:
        EditProfile.model_validate(data)
    assert exc.value.errors()[0]["loc"] == (field,)


def test_override_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        EditOverrides.model_validate({"headline": {"spoken": True}})
    assert exc.value.errors()[0]["loc"] == ("headline", "spoken")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, None])
def test_contract_rejects_nonfinite_and_invalid_sizes(value: float | None) -> None:
    with pytest.raises(ValidationError):
        EditOverrides.model_validate({"headline": {"fontSizePx": value}})


@pytest.mark.parametrize("path", ["/tmp/a.mp4", "C:/a.mp4", "C:a.mp4", "../a", "a/../b", "a\\..\\b", "a\x00b", "https://example.com/a.mp4"])
def test_contract_rejects_unsafe_paths(path: str) -> None:
    data = request_data()
    data["source"]["path"] = path
    with pytest.raises(ValidationError):
        EditResolveRequest.model_validate(data)


def test_disabled_text_allows_no_font() -> None:
    profile = EditProfile.model_validate(profile_data())
    assert profile.defaults.headline.enabled is False
    assert profile.defaults.captions.font is None
    assert profile.defaults.music.enabled is False


@pytest.mark.parametrize("value", ["CON", "con", "lpt1", "..", "A", "a/b"])
def test_contract_rejects_unsafe_ids(value: str) -> None:
    data = profile_data()
    data["profileId"] = value
    with pytest.raises(ValidationError):
        EditProfile.model_validate(data)


def test_legacy_still_validates() -> None:
    manifest = EditManifest.model_validate(json.loads(Path("examples/susan.edit.json").read_text()))
    assert manifest.schema_version == "1.0"


@pytest.mark.parametrize("change", ["font", "interval", "music", "provenance", "overrides"])
def test_manifest_enforces_resolved_semantics(change: str) -> None:
    data = json.loads(Path("examples/edit-manifest.v2.json").read_text())
    if change == "font":
        data["headline"].update(enabled=True, font=None)
    elif change == "interval":
        data["headline"].update(startSec=3, endSec=2)
    elif change == "music":
        data["music"].update(enabled=True, asset=None)
        data["musicAccepted"] = False
    elif change == "provenance":
        data["provenance"] = {}
    else:
        data["overrides"] = {}
    with pytest.raises(ValidationError):
        EditManifestV2.model_validate(data)
