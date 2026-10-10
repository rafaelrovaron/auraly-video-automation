from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
import pytest
from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.editing.qc_domain import QcReport
from auraly_pipeline.editing.review_domain import HumanReviewRequest
from auraly_pipeline.editing.review_service import HumanReviewService
from tests.review_helpers import review_case as review_case

Case = tuple[HumanReviewService, HumanReviewRequest, QcReport]


def roots(service: HumanReviewService) -> list[str]:
    return ["--project-root", str(service.project_root), "--work-root", str(service.work_root)]


def submit(service: HumanReviewService, request: HumanReviewRequest):  # type: ignore[no-untyped-def]
    path = service.project_root / "review-request.json"
    path.write_text(request.model_dump_json(by_alias=True), encoding="utf-8")
    return CliRunner().invoke(app, ["edit", "review", "--request", str(path), *roots(service)])


def get_args(service: HumanReviewService, request: HumanReviewRequest, key: str) -> list[str]:
    return ["edit", "review-get", "--campaign-id", request.campaign_id, "--video-id", request.video_id,
        "--plan-hash", request.plan_hash, "--output-variant-id", request.output_variant_id,
        "--render-key", request.render_key, "--review-key", key, *roots(service)]


def test_cli_records_and_gets_approval(review_case: Case) -> None:
    service, request, _ = review_case
    first = submit(service, request)
    assert first.exit_code == 0, first.output
    payload = json.loads(first.stdout)
    assert payload["decision"] == "approved" and payload["watchedAndListened"] is True
    repeat = submit(service, request)
    assert repeat.exit_code == 0 and json.loads(repeat.stdout) == payload
    lookup = CliRunner().invoke(app, get_args(service, request, payload["reviewKey"]))
    assert lookup.exit_code == 0 and json.loads(lookup.stdout) == payload


def test_cli_rejection_is_success(review_case: Case) -> None:
    service, request, _ = review_case
    rejection = HumanReviewRequest.model_validate(request.model_dump() | {
        "decision": "rejected", "comment": "Needs revision", "watched_and_listened": False})
    result = submit(service, rejection)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["decision"] == "rejected"
    lookup = CliRunner().invoke(app, get_args(service, rejection, payload["reviewKey"]))
    assert lookup.exit_code == 0 and json.loads(lookup.stdout)["decision"] == "rejected"


def test_cli_conflicts_and_stale_evidence_fail_safely(review_case: Case) -> None:
    service, request, report = review_case
    first = submit(service, request)
    assert first.exit_code == 0
    key = json.loads(first.stdout)["reviewKey"]
    other = HumanReviewRequest.model_validate(request.model_dump() | {
        "reviewed_by": "private-reviewer-marker", "comment": "private-comment-marker"})
    conflict = submit(service, other)
    (service.work_root / report.master_path).write_bytes(b"changed")
    stale = CliRunner().invoke(app, get_args(service, request, key))
    for result in (conflict, stale):
        assert result.exit_code == 1 and result.stdout == ""
        assert "Traceback" not in result.output and "private-" not in result.output
        assert str(service.project_root) not in result.output and str(service.work_root) not in result.output


@pytest.mark.parametrize("updates", [
    {"watchedAndListened": False}, {"watchedAndListened": "true"},
    {"reviewedBy": " "}, {"decision": "rejected", "comment": " "},
    {"campaignId": "../private-path-marker"}, {"private-comment-marker": "private-value"},
    {"reviewedAt": "2026-10-10T12:00:00Z"},
])
def test_cli_validation_does_not_leak_input(tmp_path: Path, updates: dict[str, Any]) -> None:
    payload = {"campaignId": "campaign-one", "videoId": "video-one", "planHash": "a" * 64,
        "outputVariantId": "variant-a", "renderKey": "b" * 64, "qcKey": "c" * 64,
        "decision": "approved", "reviewedBy": "private-reviewer-marker",
        "comment": "private-comment-marker", "watchedAndListened": True} | updates
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = CliRunner().invoke(app, ["edit", "review", "--request", str(path),
        "--project-root", str(tmp_path), "--work-root", str(tmp_path / "work")])
    assert result.exit_code == 1 and result.stdout == ""
    assert "private-" not in result.output and "Traceback" not in result.output
    assert str(tmp_path) not in result.output


def test_review_example_validates_against_schema() -> None:
    schema = json.loads(Path("schemas/review-request.schema.json").read_bytes())
    payload = json.loads(Path("examples/review-request.json").read_bytes())
    Draft202012Validator(schema).validate(payload)
    assert HumanReviewRequest.model_validate(payload).watched_and_listened is True
