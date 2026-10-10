from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.qc_domain import QcReport
from auraly_pipeline.editing.review_domain import HumanReviewRequest
from auraly_pipeline.editing.review_service import HumanReviewService
from tests.review_helpers import lookup, qc_path, review_case as review_case, review_path

Case = tuple[HumanReviewService, HumanReviewRequest, QcReport]


def changed(request: HumanReviewRequest, **updates: Any) -> HumanReviewRequest:
    return HumanReviewRequest.model_validate(request.model_dump() | updates)


def test_record_approval_and_exact_repeat(review_case: Case) -> None:
    service, request, report = review_case
    master = service.work_root / report.master_path
    before = (master.read_bytes(), qc_path(service, request).read_bytes())
    first = service.record(request)
    path = review_path(service, request, first.review_key)
    stored = path.read_bytes()
    second = service.record(changed(request, reviewed_by=" Local reviewer ", comment=" "))
    assert first == second == lookup(service, request, first.review_key)
    assert first.decision == "approved" and first.qc_status == "passed"
    offset = first.reviewed_at.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    assert path.read_bytes() == stored
    assert (master.read_bytes(), qc_path(service, request).read_bytes()) == before
    assert json.loads(stored)["masterSha256"] == report.master_sha256


@pytest.mark.parametrize("blocked", [False, True])
def test_reject_passed_and_blocked_qc(review_case: Case, blocked: bool) -> None:
    service, request, _ = review_case
    if blocked:
        path = qc_path(service, request)
        payload = json.loads(path.read_bytes())
        payload["audio"]["integratedLoudnessLufs"] = -40
        payload["checks"][1].update(status="blocked", findings=[{
            "code": "audio_low_loudness", "field": "audio.integratedLoudnessLufs",
            "message": "audio volume is too low"}])
        payload["status"] = "blocked"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(EditingError):
            service.record(request)
    rejection = changed(request, decision="rejected", comment="Needs revision", watched_and_listened=False)
    record = service.record(rejection)
    assert record.decision == "rejected"
    assert record.qc_status == ("blocked" if blocked else "passed")
    assert lookup(service, rejection, record.review_key) == record


@pytest.mark.parametrize("failure", ["absent", "corrupt", "missing_master", "foreign"])
def test_invalid_admission_writes_nothing(review_case: Case, failure: str) -> None:
    service, request, report = review_case
    path = qc_path(service, request)
    if failure == "absent":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"bad JSON")
    elif failure == "missing_master":
        (service.work_root / report.master_path).unlink()
    else:
        request = changed(request, output_variant_id="foreign")
    with pytest.raises(EditingError):
        service.record(request)
    assert not list(service.work_root.glob("campaigns/*/editing/reviews/*/*/*/review.json"))


@pytest.mark.parametrize("change", [
    {"decision": "rejected", "comment": "Revise"}, {"reviewed_by": "Another reviewer"},
    {"comment": "A different comment"}, {"watched_and_listened": True},
])
def test_definitive_decision_conflicts(review_case: Case, change: dict[str, Any]) -> None:
    service, request, _ = review_case
    original = changed(request, decision="rejected", comment="Revise", watched_and_listened=False)
    first = service.record(original)
    path = review_path(service, request, first.review_key)
    before = path.read_bytes()
    # The decision-change case approves rather than repeating the rejection.
    if "decision" in change:
        change = {"decision": "approved", "watched_and_listened": True}
    with pytest.raises(EditingError):
        service.record(changed(original, **change))
    assert path.read_bytes() == before
    assert len(list(path.parent.parent.glob("*/review.json"))) == 1


def test_another_qc_key_cannot_create_second_decision(review_case: Case,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    from auraly_pipeline.editing.qc_domain import QcRequest, QcTarget
    from auraly_pipeline.editing.render_runtime import detect_runtime
    service, request, _ = review_case
    first = service.record(request)
    path = review_path(service, request, first.review_key)
    before = path.read_bytes()
    runtime = detect_runtime().model_copy(update={"fingerprint": "a" * 64})
    monkeypatch.setattr(qc_service, "detect_runtime", lambda: runtime)
    result = service.qc.run(QcRequest(campaign_id=request.campaign_id, video_id=request.video_id,
        plan_hash=request.plan_hash, outputs=[QcTarget(output_variant_id=request.output_variant_id,
                                                     render_key=request.render_key)]))
    assert result.outputs[0].status == "passed"
    assert result.outputs[0].qc_key != request.qc_key
    with pytest.raises(EditingError):
        service.record(changed(request, qc_key=result.outputs[0].qc_key))
    assert path.read_bytes() == before
    assert lookup(service, request, first.review_key) == first


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_requests_have_one_winner(review_case: Case, monkeypatch: pytest.MonkeyPatch,
                                            conflicting: bool) -> None:
    from auraly_pipeline.editing import review_service
    service, request, _ = review_case
    publisher = review_service.publish_editing_json
    barrier = Barrier(2)

    def publish(*args: Any, **kwargs: Any) -> None:
        barrier.wait(timeout=10)
        publisher(*args, **kwargs)

    monkeypatch.setattr(review_service, "publish_editing_json", publish)
    other = changed(request, reviewed_by="Second reviewer") if conflicting else request

    def record(item: HumanReviewRequest):  # type: ignore[no-untyped-def]
        try:
            return service.record(item)
        except EditingError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(record, [request, other]))
    winners = [r for r in results if not isinstance(r, EditingError)]
    assert len(winners) == (1 if conflicting else 2)
    assert len(list(service.work_root.glob("campaigns/*/editing/reviews/*/*/*/review.json"))) == 1
    if not conflicting:
        assert results[0] == results[1]
    assert lookup(service, request, winners[0].review_key) == winners[0]


@pytest.mark.parametrize("mutation", ["master", "receipt", "report", "record"])
def test_stale_or_corrupt_evidence_never_returns_approval(review_case: Case, mutation: str) -> None:
    service, request, report = review_case
    record = service.record(request)
    path = review_path(service, request, record.review_key)
    before = path.read_bytes()
    master = service.work_root / report.master_path
    target = {"master": master, "receipt": master.with_name("render.json"),
              "report": qc_path(service, request), "record": path}[mutation]
    target.write_bytes(target.read_bytes() + (b" " if mutation in {"report", "receipt"} else b"invalid"))
    with pytest.raises(EditingError):
        lookup(service, request, record.review_key)
    with pytest.raises(EditingError):
        service.record(request)
    assert path.read_bytes() == (before + b"invalid" if mutation == "record" else before)


@pytest.mark.parametrize("boundary", ["verification_hash", "pre_publication"])
@pytest.mark.parametrize("mutation", ["master", "receipt", "report"])
def test_changed_evidence_prevents_publication(review_case: Case, monkeypatch: pytest.MonkeyPatch,
                                             boundary: str, mutation: str) -> None:
    service, request, report = review_case
    original = service.qc.get_report
    calls = 0

    def get(*args: Any, **kwargs: Any) -> QcReport:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == (1 if boundary == "verification_hash" else 3):
            master = service.work_root / report.master_path
            target = {"master": master, "receipt": master.with_name("render.json"),
                      "report": qc_path(service, request)}[mutation]
            if mutation == "report":
                data = json.loads(target.read_bytes())
                data["checks"][2].update(status="blocked", findings=[{
                    "code": "text_unsafe", "field": "headline", "message": "Needs revision"}])
                data["status"] = "blocked"
                target.write_text(json.dumps(data), encoding="utf-8")
            else:
                target.write_bytes(target.read_bytes() + b" ")
        return result

    monkeypatch.setattr(service.qc, "get_report", get)
    with pytest.raises(EditingError):
        service.record(request)
    assert not list(service.work_root.glob("campaigns/*/editing/reviews/*/*/*/review.json"))


def test_record_and_get_never_analyze_or_render(review_case: Case, monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service, render_runtime
    from auraly_pipeline.editing.render_service import RenderService
    service, request, report = review_case
    master = service.work_root / report.master_path
    before = master.read_bytes(), qc_path(service, request).read_bytes()
    for name in ("detect_runtime", "check_qc_integrity", "measure_qc_audio", "check_qc_text"):
        monkeypatch.setattr(qc_service, name, lambda *a, **kw: pytest.fail("review invoked media processing"))
    monkeypatch.setattr(render_runtime, "detect_runtime", lambda: pytest.fail("review detected runtime"))
    monkeypatch.setattr(RenderService, "render", lambda *a, **kw: pytest.fail("review rendered"))
    first = service.record(request)
    assert lookup(service, request, first.review_key) == first
    assert (master.read_bytes(), qc_path(service, request).read_bytes()) == before


def test_report_byte_change_during_consultation_is_rejected(review_case: Case,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    service, request, _ = review_case
    first = service.record(request)
    original = service.qc.get_report
    calls = 0

    def get(*args: Any, **kwargs: Any) -> QcReport:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            path = qc_path(service, request)
            path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(service.qc, "get_report", get)
    with pytest.raises(EditingError):
        lookup(service, request, first.review_key)


@pytest.mark.parametrize("field,value", [("campaign_id", "../private"), ("video_id", "foreign"),
    ("plan_hash", "a" * 64), ("output_variant_id", "foreign"), ("render_key", "b" * 64),
    ("review_key", "../private")])
def test_get_rejects_foreign_or_unsafe_selectors(review_case: Case, field: str, value: str) -> None:
    service, request, _ = review_case
    record = service.record(request)
    selectors = {name: getattr(record, name) for name in (
        "campaign_id", "video_id", "plan_hash", "output_variant_id", "render_key", "review_key")}
    selectors[field] = value
    with pytest.raises(EditingError):
        service.get_review(**selectors)


def test_review_does_not_transfer_to_new_plan_or_variant(review_case: Case) -> None:
    from auraly_pipeline.editing.qc_domain import QcRequest, QcTarget
    from auraly_pipeline.editing.render_service import RenderService
    from auraly_pipeline.editing.resolver import content_hash
    from tests.render_helpers import publish_test_plan, refresh_plan
    service, request, _ = review_case
    first = service.record(request)
    plan = service.qc.batch.get_plan(request.campaign_id, request.video_id, request.plan_hash)
    extra = plan.outputs[0].model_copy(deep=True)
    extra.key = "z-new"
    extra.output_variant_id = "ab-" + content_hash({"campaignId": plan.campaign_id,
        "videoId": plan.video_id, "sourceId": plan.source.id,
        "sourceSha256": plan.source.sha256, "key": extra.key})[:60]
    extra.manifest.output_variant_id = extra.output_variant_id
    plan.outputs.append(extra)
    plan.output_count = 2
    refresh_plan(plan)
    publish_test_plan(plan, service.work_root)
    rendered = RenderService(project_root=service.project_root, work_root=service.work_root).render(
        plan.campaign_id, plan.video_id, plan.plan_hash)
    assert not rendered.has_failures and rendered.outputs[0].render_key == request.render_key
    qc = service.qc.run(QcRequest(campaign_id=plan.campaign_id, video_id=plan.video_id,
        plan_hash=plan.plan_hash, outputs=[QcTarget(output_variant_id=o.output_variant_id,
            render_key=o.render_key) for o in rendered.outputs]))
    keys = {first.review_key}
    for target in qc.outputs:
        next_request = changed(request, plan_hash=plan.plan_hash, output_variant_id=target.output_variant_id,
            render_key=target.render_key, qc_key=target.qc_key, decision="rejected", comment="New revision")
        with pytest.raises(EditingError):
            lookup(service, next_request, first.review_key)
        record = service.record(next_request)
        assert record.decision == "rejected" and record.review_key not in keys
        keys.add(record.review_key)
    assert len(keys) == 3 and lookup(service, request, first.review_key) == first


def test_long_paths_and_linked_roots_follow_platform_rules(review_case: Case, tmp_path: Path) -> None:
    import os
    service, request, _ = review_case
    first = service.record(request)
    path = review_path(service, request, first.review_key)
    assert len(str(path)) > 260 and path.is_file()
    link = tmp_path / "linked-project"
    try:
        link.symlink_to(service.project_root, target_is_directory=True)
    except OSError:
        assert os.name == "nt" and not link.exists()
    else:
        for roots in ((link, service.work_root), (service.project_root, link / "work")):
            with pytest.raises(EditingError):
                HumanReviewService(project_root=roots[0], work_root=roots[1])
