from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError


def request_data() -> dict[str, Any]:
    return {"campaignId": "campaign-one", "videoId": "video-one", "planHash": "a" * 64,
            "outputs": [{"outputVariantId": "headline-a", "renderKey": "b" * 64}]}


def report_data() -> dict[str, Any]:
    from auraly_pipeline.editing.qc_domain import QcPolicy, qc_key
    from auraly_pipeline.editing.resolver import content_hash

    policy = QcPolicy().model_dump(mode="json", by_alias=True)
    inputs = dict(plan_hash="a" * 64, output_variant_id="headline-a", render_key="b" * 64,
                  master_sha256="c" * 64, receipt_sha256="d" * 64, qc_version="1.0",
                  policy_hash=content_hash(policy), runtime_fingerprint="e" * 64)
    return {"campaignId": "campaign-one", "videoId": "video-one", "planHash": "a" * 64,
            "outputVariantId": "headline-a", "renderKey": "b" * 64,
            "manifestHash": "f" * 64, "outputHash": "1" * 64,
            "masterPath": "campaigns/campaign-one/master.mp4", "masterSha256": "c" * 64,
            "sizeBytes": 100, "receiptSha256": "d" * 64, "qcVersion": "1.0",
            "policy": policy, "policyHash": content_hash(policy), "qcKey": qc_key(**inputs),
            "runtime": {"fingerprint": "e" * 64, "ffmpegVersion": "test build",
                        "libassVersion": "test libass", "encoding": {}},
            "probe": {"formatName": "mp4", "durationSec": 3, "sizeBytes": 100,
                      "video": {"codec": "h264", "width": 1080, "height": 1920,
                                "fps": 30, "nominalFps": 30, "isVfr": False},
                      "audio": {"codec": "aac", "sampleRate": 48000, "channels": 2}},
            "checks": [{"name": "integrity", "status": "passed"},
                       {"name": "audio", "status": "passed"},
                       {"name": "text", "status": "not_applicable"}],
            "audio": {"integratedLoudnessLufs": -16, "truePeakDbtp": -1},
            "status": "passed", "humanReviewRequired": True}


@pytest.mark.parametrize("change", [
    {"outputs": []}, {"outputs": request_data()["outputs"] * 2},
    {"campaignId": "../private"}, {"videoId": "con"}, {"planHash": "bad"},
    {"sourcePath": "private.mp4"}, {"policy": {}},
])
def test_request_rejects_duplicates_paths_and_unknown_policy(change: dict[str, Any]) -> None:
    from auraly_pipeline.editing.qc_domain import QcRequest
    assert len(QcRequest.model_validate(request_data()).outputs) == 1
    with pytest.raises(ValidationError):
        QcRequest.model_validate({**request_data(), **change})


@pytest.mark.parametrize("change", [
    {"status": "blocked"}, {"humanReviewRequired": False}, {"qcKey": "0" * 64},
    {"audio": {"integratedLoudnessLufs": -31, "truePeakDbtp": -1}},
    {"audio": {"integratedLoudnessLufs": float("nan"), "truePeakDbtp": -1}},
    {"checks": [{"name": "integrity", "status": "passed"},
                {"name": "audio", "status": "passed"}, {"name": "text", "status": "skipped"}]},
])
def test_report_cannot_claim_passed_with_blocked_or_skipped_checks(change: dict[str, Any]) -> None:
    from auraly_pipeline.editing.qc_domain import QcReport
    assert QcReport.model_validate(report_data()).status == "passed"
    with pytest.raises(ValidationError):
        QcReport.model_validate({**report_data(), **change})


def test_report_key_covers_consumer_plan_receipt_policy_and_runtime() -> None:
    from auraly_pipeline.editing.qc_domain import qc_key
    values = dict(plan_hash="a" * 64, output_variant_id="headline-a", render_key="b" * 64,
                  master_sha256="c" * 64, receipt_sha256="d" * 64, qc_version="1.0",
                  policy_hash="e" * 64, runtime_fingerprint="f" * 64)
    original = qc_key(**values)
    for field in values:
        assert qc_key(**{**values, field: "2.0" if field == "qc_version" else "0" * 64}) != original


def test_batch_error_has_no_report_and_sets_has_failures() -> None:
    from auraly_pipeline.editing.qc_domain import QcBatchResult, QcOutputResult
    target = request_data()["outputs"][0]
    item = {**target, "status": "error", "error": {"code": "artifact_invalid", "field": "artifact",
                                                "message": "invalid local artifact"}}
    batch = QcBatchResult.model_validate({**request_data(), "outputs": [item]})
    assert batch.has_failures and batch.outputs[0].qc_key is None
    for invalid in ({**item, "reused": True}, {**item, "path": "report.json"},
                    {**target, "status": "passed"}):
        with pytest.raises(ValidationError):
            QcOutputResult.model_validate(invalid)


def test_qc_schemas_validate_contracts_and_have_no_drift(tmp_path: Path) -> None:
    from auraly_pipeline.editing.qc_domain import QcBatchResult, QcReport
    from auraly_pipeline.editing.schema import export_qc_schemas
    report = QcReport.model_validate(report_data())
    batch = QcBatchResult.model_validate({**request_data(), "outputs": [{
        **request_data()["outputs"][0], "status": "passed", "qcKey": report.qc_key,
        "path": "campaigns/campaign-one/report.json"}]})
    payloads = [request_data(), report.model_dump(mode="json", by_alias=True, exclude_computed_fields=True),
                batch.model_dump(mode="json", by_alias=True)]
    for path, payload in zip(export_qc_schemas(tmp_path), payloads, strict=True):
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
        assert path.read_bytes() == (Path("schemas") / path.name).read_bytes()
