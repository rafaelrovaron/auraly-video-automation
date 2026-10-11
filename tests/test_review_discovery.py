from __future__ import annotations

import json
from pathlib import Path

import pytest

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.qc_domain import QcReport, qc_key
from auraly_pipeline.editing.review_domain import HumanReviewRequest
from auraly_pipeline.editing.review_service import HumanReviewService
from tests.review_helpers import qc_path, review_case as review_case, review_path

Case = tuple[HumanReviewService, HumanReviewRequest, QcReport]


def target(request: HumanReviewRequest) -> tuple[str, str, str, str, str]:
    return (request.campaign_id, request.video_id, request.plan_hash,
            request.output_variant_id, request.render_key)


def test_discovery_returns_verified_original(review_case: Case) -> None:
    service, request, report = review_case
    assert service.qc.list_reports(*target(request)) == [report]
    assert service.find_review(*target(request)) is None
    record = service.record(request)
    assert service.find_review(*target(request)) == record


def test_foreign_plan_qc_not_adopted(review_case: Case) -> None:
    service, request, report = review_case
    payload = report.model_dump(mode="json", by_alias=True, exclude_computed_fields=True)
    payload["planHash"] = "a" * 64
    key = qc_key(plan_hash="a" * 64, output_variant_id=request.output_variant_id,
                 render_key=request.render_key, master_sha256=report.master_sha256,
                 receipt_sha256=report.receipt_sha256, qc_version=report.qc_version,
                 policy_hash=report.policy_hash, runtime_fingerprint=report.runtime.fingerprint)
    payload["qcKey"] = key
    QcReport.model_validate(payload)
    path = qc_path(service, request).parent.parent / key / "report.json"
    path.parent.mkdir()
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert service.qc.list_reports(*target(request)) == [report]


@pytest.mark.parametrize("kind", ["corrupt_qc", "corrupt_review", "missing_review_file", "bad_key"])
def test_corrupt_or_broken_link_is_not_absence(review_case: Case, kind: str) -> None:
    service, request, _ = review_case
    record = service.record(request)
    path = review_path(service, request, record.review_key)
    if kind == "corrupt_qc":
        qc_path(service, request).write_bytes(b"invalid")
    elif kind == "corrupt_review":
        path.write_bytes(b"invalid")
    elif kind == "missing_review_file":
        path.unlink()
    else:
        qc_path(service, request).parent.rename(qc_path(service, request).parent.with_name("bad-key"))
    with pytest.raises(EditingError):
        if kind in {"corrupt_qc", "bad_key"}:
            service.qc.list_reports(*target(request))
        else:
            service.find_review(*target(request))


def test_broken_link_is_not_absence(review_case: Case, tmp_path: Path) -> None:
    service, request, _ = review_case
    record = service.record(request)
    path = review_path(service, request, record.review_key)
    path.unlink()
    try:
        path.symlink_to(tmp_path / "absent.json")
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(EditingError):
        service.find_review(*target(request))


@pytest.mark.parametrize("kind", ["master", "receipt", "qc_bytes"])
def test_current_review_stale_evidence_errors(review_case: Case, kind: str) -> None:
    service, request, report = review_case
    service.record(request)
    if kind == "qc_bytes":
        path = qc_path(service, request)
    elif kind == "master":
        path = service.work_root / report.master_path
    else:
        path = (service.work_root / report.master_path).with_name("render.json")
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(EditingError):
        service.find_review(*target(request))


def test_discovery_never_runs_ffmpeg(review_case: Case, monkeypatch: pytest.MonkeyPatch) -> None:
    service, request, report = review_case
    record = service.record(request)
    before = {p: p.read_bytes() for p in service.work_root.rglob("*.json")}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("discovery must not run media processing")

    monkeypatch.setattr("auraly_pipeline.editing.qc_service.detect_runtime", forbidden)
    monkeypatch.setattr("auraly_pipeline.editing.qc_service.check_qc_integrity", forbidden)
    assert service.qc.list_reports(*target(request)) == [report]
    assert service.find_review(*target(request)) == record
    assert {p: p.read_bytes() for p in service.work_root.rglob("*.json")} == before


def test_absent_qc_directory_is_empty_without_writes(review_case: Case) -> None:
    service, request, _ = review_case
    path = qc_path(service, request)
    path.unlink()
    path.parent.rmdir()
    path.parent.parent.rmdir()
    assert service.qc.list_reports(*target(request)) == []
    assert service.find_review(*target(request)) is None
    assert not path.parent.parent.exists()
