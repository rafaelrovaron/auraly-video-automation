from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from auraly_pipeline.campaigns.domain import CampaignCreate, CopyMasterContent


def valid_campaign_data() -> dict:
    return {
        "campaignId": "eight-of-cups-pilot",
        "character": "soul-constellation",
        "proofObject": "Eight of Cups tarot card",
        "voicePreset": "michael-c-vincent-v1",
        "editPreset": "soul-constellation-v1",
        "budget": {"currency": "USD", "limitCents": 0},
        "config": {"targetVariants": 3},
        "copyMaster": {
            "sourceText": "Original canonical source",
            "headline": "HE RETURNS WHEN YOU WALK AWAY",
            "hook": "You stopped chasing him.",
            "body": "That changed the energy between you.",
            "cta": "Take the one-minute reading.",
            "approvalState": "approved",
            "approvedBy": "rafael",
        },
        "sceneVariants": [
            {
                "variantId": "laundromat",
                "location": "24-hour laundromat",
                "timeAtmosphere": "After midnight",
                "action": "Walk away from the machines",
                "prompt": "Create the approved laundromat scene.",
            },
            {
                "variantId": "restaurant",
                "location": "Empty restaurant",
                "timeAtmosphere": "Closing time",
                "action": "Leave the table",
                "prompt": "Create the approved restaurant scene.",
            },
            {
                "variantId": "metro",
                "location": "Metro station",
                "timeAtmosphere": "Last train",
                "action": "Step onto the train",
                "prompt": "Create the approved metro scene.",
            },
        ],
    }


def test_copy_master_spoken_text_excludes_visual_headline() -> None:
    content = CopyMasterContent(
        source_text="Original canonical source",
        headline="HE RETURNS WHEN YOU WALK AWAY",
        hook="You stopped chasing him.",
        body="That changed the energy between you.",
        cta="Take the one-minute reading.",
    )

    assert content.spoken_text == (
        "You stopped chasing him.\n\n"
        "That changed the energy between you.\n\n"
        "Take the one-minute reading."
    )
    assert content.headline not in content.spoken_text


def test_copy_master_sha256_covers_canonical_content() -> None:
    content = CopyMasterContent(
        source_text="Original canonical source",
        headline="VISUAL ONLY",
        hook="Hook.",
        body="Body.",
        cta="CTA.",
    )
    canonical = json.dumps(
        {
            "body": "Body.",
            "cta": "CTA.",
            "headline": "VISUAL ONLY",
            "hook": "Hook.",
            "sourceText": "Original canonical source",
            "spokenText": "Hook.\n\nBody.\n\nCTA.",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert content.sha256 == hashlib.sha256(canonical).hexdigest()


def test_campaign_create_accepts_three_independent_scene_variants() -> None:
    campaign = CampaignCreate.model_validate(valid_campaign_data())

    assert campaign.campaign_id == "eight-of-cups-pilot"
    assert [variant.variant_id for variant in campaign.scene_variants] == [
        "laundromat",
        "restaurant",
        "metro",
    ]
    assert all(variant.status == "not_started" for variant in campaign.scene_variants)


def test_campaign_rejects_duplicate_scene_variant_ids() -> None:
    data = deepcopy(valid_campaign_data())
    data["sceneVariants"][2]["variantId"] = "laundromat"

    with pytest.raises(ValidationError, match="scene variant IDs must be unique"):
        CampaignCreate.model_validate(data)


def test_approved_copy_master_requires_approver() -> None:
    data = deepcopy(valid_campaign_data())
    data["copyMaster"].pop("approvedBy")

    with pytest.raises(ValidationError, match="approved copy master requires approved_by"):
        CampaignCreate.model_validate(data)


def test_campaign_rejects_unapproved_copy_master() -> None:
    data = deepcopy(valid_campaign_data())
    data["copyMaster"]["approvalState"] = "draft"
    data["copyMaster"].pop("approvedBy")

    with pytest.raises(ValidationError):
        CampaignCreate.model_validate(data)


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "oauthToken",
        "apiToken",
        "sessionCookie",
        "signedUrl",
        "credential",
        "privateKey",
        "auth",
    ],
)
def test_campaign_metadata_rejects_secret_bearing_keys(sensitive_key: str) -> None:
    data = deepcopy(valid_campaign_data())
    data["config"] = {"browser": {sensitive_key: "do-not-store"}}

    with pytest.raises(ValidationError, match="config contains a forbidden sensitive key"):
        CampaignCreate.model_validate(data)


@pytest.mark.parametrize(
    "signed_url",
    [
        "https://storage.example/object?" + "X-Amz-" + "Signature=SENSITIVE",
        "https://storage.example/object?" + "X-Goog-" + "Signature=SENSITIVE",
        "https://storage.example/object?sv=1&" + "sig=SENSITIVE",
    ],
)
def test_campaign_metadata_rejects_signed_url_values(signed_url: str) -> None:
    data = deepcopy(valid_campaign_data())
    data["config"] = {"notes": signed_url}

    with pytest.raises(ValidationError, match="config contains forbidden sensitive data"):
        CampaignCreate.model_validate(data)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_campaign_metadata_rejects_non_finite_numbers(non_finite: float) -> None:
    data = deepcopy(valid_campaign_data())
    data["budget"] = {"limit": non_finite}

    with pytest.raises(ValidationError, match="budget contains a non-finite number"):
        CampaignCreate.model_validate(data)


@pytest.mark.parametrize("campaign_id", ["Uppercase", "contains space", "../escape", ""])
def test_campaign_rejects_invalid_campaign_id(campaign_id: str) -> None:
    data = deepcopy(valid_campaign_data())
    data["campaignId"] = campaign_id

    with pytest.raises(ValidationError):
        CampaignCreate.model_validate(data)


def test_campaign_requires_at_least_three_scene_variants() -> None:
    data = deepcopy(valid_campaign_data())
    data["sceneVariants"] = data["sceneVariants"][:2]

    with pytest.raises(ValidationError):
        CampaignCreate.model_validate(data)


def test_campaign_rejects_missing_copy_master_fields() -> None:
    data = deepcopy(valid_campaign_data())
    data["copyMaster"].pop("cta")

    with pytest.raises(ValidationError):
        CampaignCreate.model_validate(data)


@pytest.mark.parametrize("field", ["budget", "config"])
def test_campaign_requires_budget_and_config(field: str) -> None:
    data = deepcopy(valid_campaign_data())
    data.pop(field)

    with pytest.raises(ValidationError):
        CampaignCreate.model_validate(data)


def test_campaign_requires_three_distinct_locations() -> None:
    data = deepcopy(valid_campaign_data())
    data["sceneVariants"][2]["location"] = " 24-HOUR   LAUNDROMAT "

    with pytest.raises(ValidationError, match="scene variant locations must be distinct"):
        CampaignCreate.model_validate(data)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("campaign", "proofObject"),
        ("copyMaster", "hook"),
        ("sceneVariant", "location"),
    ],
)
def test_campaign_rejects_whitespace_only_required_text(section: str, field: str) -> None:
    data = deepcopy(valid_campaign_data())
    if section == "campaign":
        data[field] = "   "
    elif section == "copyMaster":
        data["copyMaster"][field] = "   "
    else:
        data["sceneVariants"][0][field] = "   "

    with pytest.raises(ValidationError):
        CampaignCreate.model_validate(data)
