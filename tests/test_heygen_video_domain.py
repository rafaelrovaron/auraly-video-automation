from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig, VideoPlanItem, video_logical_key


def video_item(**changes: object) -> VideoPlanItem:
    data: dict[str, object] = dict(
        campaign_id="campaign-one",
        scene_variant_id="20000000-0000-4000-8000-000000000000",
        image_candidate_id="30000000-0000-4000-8000-000000000000",
        voice_master_id="40000000-0000-4000-8000-000000000000",
        account_ref="fake-account",
        image_sha256="a" * 64,
        audio_sha256="b" * 64,
        image_asset_id="image-one",
        audio_asset_id="audio-one",
        duration_seconds=1,
        config=HeyGenVideoConfig(),
        schema_fingerprint="c" * 64,
    )
    data.update(changes)
    return VideoPlanItem.model_validate(data)


def test_config_defaults_and_closed_schema() -> None:
    config = HeyGenVideoConfig()
    assert config.concurrency == 2
    assert (config.poll_initial_seconds, config.poll_max_seconds, config.poll_timeout_seconds) == (
        10,
        60,
        1800,
    )
    for invalid in [
        {"resolution": "4k"},
        {"concurrency": 3},
        {"unknown": True},
        {"poll_timeout_seconds": 0},
    ]:
        with pytest.raises(ValidationError):
            HeyGenVideoConfig.model_validate(invalid)
    schema = Path("schemas/heygen-video-config.schema.json")
    assert json.loads(schema.read_text()) == HeyGenVideoConfig.model_json_schema()


def test_material_identity() -> None:
    item = video_item()
    assert video_logical_key(item) == video_logical_key(
        video_item(config=HeyGenVideoConfig(poll_initial_seconds=20))
    )
    assert video_logical_key(item) != video_logical_key(
        video_item(scene_variant_id="20000000-0000-4000-8000-000000000001")
    )
    assert video_logical_key(item) != video_logical_key(
        video_item(config=HeyGenVideoConfig(motion_prompt="Smile"))
    )
    invalid_items: list[dict[str, object]] = [{"duration_seconds": 0}, {"account_ref": "https://secret.example/token"}]
    for invalid in invalid_items:
        with pytest.raises(ValidationError):
            video_item(**invalid)
