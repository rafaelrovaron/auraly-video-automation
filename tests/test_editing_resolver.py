from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from auraly_pipeline.editing.domain import EditProfile, EditResolveRequest, EditingError
from auraly_pipeline.editing.resolver import profile_hash, resolve_manifest, verify_manifest_hash
from tests.editing_helpers import profile_data, request_data


def test_precedence_and_field_provenance() -> None:
    profile = EditProfile.model_validate(profile_data())
    data = request_data(profile_hash(profile))
    for layer, size in [("campaign", 64), ("video", 68), ("outputVariant", 72)]:
        data[layer] = {"headline": {"fontSizePx": size}}
    result = resolve_manifest(profile, EditResolveRequest.model_validate(data))
    assert result.headline.font_size_px == 72
    assert result.provenance["headline.fontSizePx"] == "outputVariant"
    assert result.provenance["headline.color"] == "profile"
    assert result.provenance["headline.text"] == "input"
    assert result.provenance["headline.endSec"] == "source"
    assert result.headline.end_sec == 7.224


def test_false_zero_and_nullable_overrides() -> None:
    profile = EditProfile.model_validate(profile_data())
    data = request_data(profile_hash(profile))
    data["video"] = {"music": {"enabled": False, "volumeDb": 0, "asset": None}}
    result = resolve_manifest(profile, EditResolveRequest.model_validate(data))
    assert result.music.volume_db == 0
    assert result.music.asset is None
    assert result.provenance["music.enabled"] == "video"
    data["video"] = {"headline": {"fontSizePx": None}}
    with pytest.raises(ValidationError) as exc:
        EditResolveRequest.model_validate(data)
    assert exc.value.errors()[0]["loc"] == ("video", "headline", "fontSizePx")


def test_roundtrip_keeps_omitted_fields_absent() -> None:
    profile = EditProfile.model_validate(profile_data())
    data = request_data(profile_hash(profile))
    data["video"] = {"headline": {"fontSizePx": 72}}
    request = EditResolveRequest.model_validate(data)
    serialized = request.model_dump_json(by_alias=True)
    assert json.loads(serialized)["video"]["headline"] == {"fontSizePx": 72}
    copy = EditResolveRequest.model_validate_json(serialized)
    assert resolve_manifest(profile, copy).manifest_hash == resolve_manifest(profile, request).manifest_hash


def test_replay_and_key_order_are_deterministic() -> None:
    profile = EditProfile.model_validate(profile_data())
    data = request_data(profile_hash(profile))
    first = resolve_manifest(profile, EditResolveRequest.model_validate(data))
    second = resolve_manifest(profile, EditResolveRequest.model_validate(dict(reversed(list(data.items())))))
    assert first.model_dump_json() == second.model_dump_json()
    verify_manifest_hash(first)
    first.headline.text = "Modified"
    with pytest.raises(EditingError):
        verify_manifest_hash(first)


def test_headline_only_changes_manifest_not_inputs() -> None:
    profile = EditProfile.model_validate(profile_data())
    data = request_data(profile_hash(profile))
    data["voiceRef"] = {"id": "voice-1", "hash": "c" * 64}
    data["copyRef"] = {"id": "copy-1", "hash": "d" * 64}
    first = resolve_manifest(profile, EditResolveRequest.model_validate(data))
    data["outputVariant"] = {"headline": {"text": "Headline B"}}
    second = resolve_manifest(profile, EditResolveRequest.model_validate(data))
    assert first.manifest_hash != second.manifest_hash
    assert second.source == first.source
    assert second.voice_ref == first.voice_ref
    assert second.copy_ref == first.copy_ref
    assert second.provenance["headline.text"] == "outputVariant"


def test_wrong_profile_hash_fails() -> None:
    with pytest.raises(EditingError) as exc:
        resolve_manifest(EditProfile.model_validate(profile_data()), EditResolveRequest.model_validate(request_data()))
    assert exc.value.field == "profileRef"


def test_invalid_final_interval_reports_origin() -> None:
    profile = EditProfile.model_validate(profile_data())
    data = request_data(profile_hash(profile))
    data["video"] = {"headline": {"endSec": 10}}
    with pytest.raises(EditingError) as exc:
        resolve_manifest(profile, EditResolveRequest.model_validate(data))
    assert exc.value.field == "headline.endSec"
    assert exc.value.layer == "video"


def test_enabled_text_requires_local_font() -> None:
    profile = EditProfile.model_validate(profile_data())
    data = request_data(profile_hash(profile))
    data["video"] = {"headline": {"enabled": True}}
    with pytest.raises(EditingError) as exc:
        resolve_manifest(profile, EditResolveRequest.model_validate(data))
    assert exc.value.field == "headline.font"
