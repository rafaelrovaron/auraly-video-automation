from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from jsonschema import Draft202012Validator
import pytest

from auraly_pipeline.editing.review_domain import HumanReviewRecord, HumanReviewRequest, review_key


def request_payload(**updates: Any) -> dict[str, Any]:
    return {"campaignId": "campaign-one", "videoId": "video-one", "planHash": "a" * 64,
            "outputVariantId": "variant-a", "renderKey": "b" * 64, "qcKey": "c" * 64,
            "decision": "approved", "reviewedBy": " Local reviewer ", "comment": " ",
            "watchedAndListened": True, **updates}


def record_payload(**updates: Any) -> dict[str, Any]:
    identity = {"campaignId": "campaign-one", "videoId": "video-one", "planHash": "a" * 64,
                "outputVariantId": "variant-a", "renderKey": "b" * 64,
                "masterSha256": "d" * 64, "receiptSha256": "e" * 64}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**request_payload(), "reviewedAt": "2026-10-10T12:00:00Z", "reviewKey": key,
            "masterPath": "campaigns/campaign-one/master.mp4", "masterSha256": "d" * 64,
            "sizeBytes": 1234, "receiptSha256": "e" * 64, "qcReportSha256": "f" * 64,
            "qcStatus": "passed", **updates}


@pytest.mark.parametrize("confirmation", [False, "true", 1, None])
def test_approval_requires_playback_confirmation(confirmation: object) -> None:
    with pytest.raises(ValidationError):
        HumanReviewRequest.model_validate(request_payload(watchedAndListened=confirmation))
    missing = request_payload()
    del missing["watchedAndListened"]
    with pytest.raises(ValidationError):
        HumanReviewRequest.model_validate(missing)


@pytest.mark.parametrize("comment", [None, "", " \n "])
def test_rejection_requires_comment(comment: str | None) -> None:
    with pytest.raises(ValidationError):
        HumanReviewRequest.model_validate(request_payload(decision="rejected", comment=comment))
    rejected = HumanReviewRequest.model_validate(request_payload(
        decision="rejected", comment=" Repair music ", watchedAndListened=False))
    assert rejected.comment == "Repair music" and rejected.watched_and_listened is False


def test_review_identity_and_normalization() -> None:
    result = HumanReviewRequest.model_validate(request_payload())
    assert result.reviewed_by == "Local reviewer" and result.comment is None
    assert result.model_dump(by_alias=True)["schemaVersion"] == "1.0"


@pytest.mark.parametrize("changes", [
    {"reviewedBy": " "}, {"campaignId": "../unsafe"}, {"videoId": "con"},
    {"outputVariantId": "A"}, {"planHash": "invalid"}, {"qcKey": "f" * 63},
    {"decision": "passed"}, {"schemaVersion": "2.0"}, {"extra": "not allowed"},
])
def test_request_rejects_invalid_identity(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HumanReviewRequest.model_validate(request_payload(**changes))


def test_record_requires_valid_approval_evidence() -> None:
    record = HumanReviewRecord.model_validate(record_payload())
    assert record.reviewed_at.utcoffset() is not None
    assert record.qc_status == "passed" and record.size_bytes == 1234


@pytest.mark.parametrize("changes", [
    {"qcStatus": "blocked"}, {"qcStatus": "error"}, {"reviewedAt": "2026-10-10T12:00:00"},
    {"masterPath": "../escape.mp4"}, {"masterPath": "C:/private/master.mp4"},
    {"reviewKey": "0" * 64}, {"sizeBytes": 0}, {"sizeBytes": True},
    {"qcReportSha256": "bad"}, {"masterSha256": "0" * 64},
])
def test_record_rejects_invalid_evidence(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HumanReviewRecord.model_validate(record_payload(**changes))


def test_review_key_uses_only_version_identity() -> None:
    args = dict(campaign_id="campaign-one", video_id="video-one", plan_hash="a" * 64,
                output_variant_id="variant-a", render_key="b" * 64,
                master_sha256="d" * 64, receipt_sha256="e" * 64)
    assert review_key(**args) == record_payload()["reviewKey"]
    assert review_key(**{**args, "plan_hash": "0" * 64}) != review_key(**args)
    original = HumanReviewRecord.model_validate(record_payload())
    another = HumanReviewRecord.model_validate(record_payload(
        decision="rejected", comment="Needs revision", qcKey="0" * 64,
        reviewedBy="Another reviewer", reviewedAt="2026-10-10T13:00:00Z"))
    assert another.review_key == original.review_key


def test_exported_review_schemas_validate_contracts(tmp_path: Path) -> None:
    from auraly_pipeline.editing.schema import export_review_schemas
    paths = export_review_schemas(tmp_path)
    assert [p.name for p in paths] == ["review-request.schema.json", "review-record.schema.json"]
    models: tuple[type[HumanReviewRequest], ...] = (HumanReviewRequest, HumanReviewRecord)
    for path, model, payload in zip(paths, models,
                                   (request_payload(), record_payload()), strict=True):
        schema = json.loads(path.read_bytes())
        Draft202012Validator(schema).validate(model.model_validate(payload).model_dump(mode="json", by_alias=True))
        assert schema["additionalProperties"] is False
