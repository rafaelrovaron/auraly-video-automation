from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.qc_domain import QcRequest, QcTarget
from auraly_pipeline.editing.render_service import RenderService
from auraly_pipeline.editing.render_runtime import detect_runtime
from tests.render_helpers import make_render_plan, publish_test_plan, refresh_plan

if TYPE_CHECKING:
    from auraly_pipeline.editing.qc_service import QcService


@pytest.fixture
def qc_case(tmp_path: Path) -> tuple[QcService, QcRequest, EditBatchPlan]:
    from auraly_pipeline.editing.qc_service import QcService
    plan = make_render_plan(tmp_path / "project")
    renderer = RenderService(project_root=tmp_path / "project", work_root=tmp_path / "project" / "work")
    publish_test_plan(plan, renderer.work_root)
    rendered = renderer.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert not rendered.has_failures
    request = QcRequest(campaign_id=plan.campaign_id, video_id=plan.video_id, plan_hash=plan.plan_hash,
        outputs=[QcTarget(output_variant_id=o.output_variant_id, render_key=o.render_key) for o in rendered.outputs])
    return QcService(project_root=tmp_path / "project", work_root=tmp_path / "project" / "work"), request, plan


def get_first(service: QcService, request: QcRequest, key: str):  # type: ignore[no-untyped-def]
    target = request.outputs[0]
    return service.get_report(request.campaign_id, request.video_id, request.plan_hash,
                              target.output_variant_id, target.render_key, key)


def test_admission_invalid_selection_writes_nothing(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, _ = qc_case
    request.outputs[0].output_variant_id = "foreign"
    monkeypatch.setattr(qc_service, "detect_runtime", lambda: pytest.fail("invalid admission reached runtime"))
    with pytest.raises(EditingError):
        service.run(request)
    assert not list(service.work_root.glob("campaigns/*/editing/qc/*"))


def test_historical_master_is_selected_without_render(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, _ = qc_case
    current = detect_runtime().model_copy(update={"fingerprint": "a" * 64})
    monkeypatch.setattr(qc_service, "detect_runtime", lambda: current)
    monkeypatch.setattr(RenderService, "render", lambda *a, **kw: pytest.fail("QC regenerated media"))
    result = service.run(request)
    assert [o.status for o in result.outputs] == ["passed", "passed", "passed"]
    assert result.outputs[0].qc_key is not None
    report = get_first(service, request, result.outputs[0].qc_key)
    assert report.runtime.fingerprint == "a" * 64 and report.render_key == request.outputs[0].render_key
    assert report.human_review_required is True


def test_missing_runtime_returns_all_errors_in_order(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, _ = qc_case

    def missing():  # type: ignore[no-untyped-def]
        raise EditingError("runtime", "private-secret-path")

    monkeypatch.setattr(qc_service, "detect_runtime", missing)
    result = service.run(request)
    assert [o.status for o in result.outputs] == ["error", "error", "error"]
    assert [o.output_variant_id for o in result.outputs] == [o.output_variant_id for o in request.outputs]
    assert "private" not in result.model_dump_json()
    assert not list(service.work_root.glob("campaigns/*/editing/qc/*/*/*/report.json"))


def test_variant_failure_does_not_stop_batch(qc_case: tuple[QcService, QcRequest, EditBatchPlan]) -> None:
    service, request, plan = qc_case
    root = service.work_root / "campaigns" / plan.campaign_id / "editing" / "renders"
    master = next((root / request.outputs[0].output_variant_id).glob("*/*.mp4"))
    master.write_bytes(b"changed")
    result = service.run(request)
    assert [o.status for o in result.outputs] == ["error", "passed", "passed"]
    assert result.has_failures


def test_exact_report_reuses_without_measurement(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, _ = qc_case
    first = service.run(request)
    path = first.outputs[0].path
    assert path is not None
    before = (service.work_root / path).read_bytes()
    for name in ("check_qc_integrity", "measure_qc_audio", "check_qc_text"):
        monkeypatch.setattr(qc_service, name, lambda *a, **kw: pytest.fail("cache repeated analysis"))
    second = service.run(request)
    assert all(o.reused for o in second.outputs)
    assert second.outputs[0].qc_key == first.outputs[0].qc_key
    assert (service.work_root / path).read_bytes() == before


def test_changed_runtime_or_consumer_plan_gets_new_key(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, plan = qc_case
    first = service.run(request)
    plan.max_outputs += 1
    refresh_plan(plan)
    publish_test_plan(plan, service.work_root)
    request.plan_hash = plan.plan_hash
    second = service.run(request)
    assert second.outputs[0].qc_key != first.outputs[0].qc_key and not second.outputs[0].reused
    runtime = detect_runtime().model_copy(update={"fingerprint": "a" * 64})
    monkeypatch.setattr(qc_service, "detect_runtime", lambda: runtime)
    third = service.run(request)
    assert third.outputs[0].qc_key != second.outputs[0].qc_key and not third.outputs[0].reused
    assert first.outputs[0].path is not None and (service.work_root / first.outputs[0].path).is_file()


@pytest.mark.parametrize("mutation", ["master", "receipt", "report", "disabled_text"])
def test_modified_master_receipt_and_corrupt_report_never_reuse_passed(
        qc_case: tuple[QcService, QcRequest, EditBatchPlan], mutation: str) -> None:
    service, request, _ = qc_case
    first = service.run(request)
    assert first.outputs[0].qc_key is not None and first.outputs[0].path is not None
    report = get_first(service, request, first.outputs[0].qc_key)
    master = service.work_root / report.master_path
    if mutation == "master":
        master.write_bytes(b"altered private media")
    elif mutation == "receipt":
        receipt = master.with_name("render.json")
        data = json.loads(receipt.read_bytes())
        data["inputs"]["source"] = "0" * 64
        receipt.write_text(json.dumps(data))
    else:
        path = service.work_root / first.outputs[0].path
        data = json.loads(path.read_bytes())
        if mutation == "disabled_text":
            data["checks"][2]["status"] = "not_applicable"
        else:
            data["audio"]["integratedLoudnessLufs"] = -40
        path.write_text(json.dumps(data))
    result = service.run(request)
    assert result.outputs[0].status == "error" and not result.outputs[0].reused
    with pytest.raises(EditingError):
        get_first(service, request, first.outputs[0].qc_key)


def test_input_change_during_analysis_prevents_publication(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, plan = qc_case
    original = qc_service.measure_qc_audio

    def change(path: Path, **kwargs):  # type: ignore[no-untyped-def]
        result = original(path, **kwargs)
        (service.project_root / "font.ttf").write_bytes(b"changed-font")
        return result

    monkeypatch.setattr(qc_service, "measure_qc_audio", change)
    result = service.run(request)
    assert all(o.status == "error" for o in result.outputs)
    assert not list(service.work_root.glob(f"campaigns/{plan.campaign_id}/editing/qc/*/*/*/report.json"))


def test_operational_error_is_not_cached_and_can_be_retried(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, _ = qc_case
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise EditingError("runtime", "unavailable")
        patch.setattr(qc_service, "measure_qc_audio", fail)
        assert all(o.status == "error" for o in service.run(request).outputs)
    retry = service.run(request)
    assert all(o.status == "passed" and not o.reused for o in retry.outputs)


def test_get_report_needs_no_ffmpeg_and_rejects_stale_identity(qc_case: tuple[QcService, QcRequest, EditBatchPlan],
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_service
    service, request, _ = qc_case
    result = service.run(request)
    assert result.outputs[0].qc_key is not None
    for name in ("detect_runtime", "check_qc_integrity", "measure_qc_audio", "check_qc_text"):
        monkeypatch.setattr(qc_service, name, lambda *a, **kw: pytest.fail("GET invoked FFmpeg"))
    report = get_first(service, request, result.outputs[0].qc_key)
    assert report.status == "passed"
    (service.work_root / report.master_path).write_bytes(b"changed")
    with pytest.raises(EditingError):
        get_first(service, request, result.outputs[0].qc_key)


@pytest.mark.parametrize("failure", ["probe_json", "text_raster"])
def test_failed_analysis_is_not_cached_and_retries_after_repair(
        qc_case: tuple[QcService, QcRequest, EditBatchPlan],
        monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    import subprocess
    from auraly_pipeline.editing import render_media, render_text
    from auraly_pipeline.probe import ProbeError

    service, request, plan = qc_case
    request.outputs = request.outputs[:1]
    with monkeypatch.context() as patch:
        if failure == "probe_json":
            def invalid_probe(*args: object, **kwargs: object) -> None:
                raise ProbeError("private probe detail") from json.JSONDecodeError("bad JSON", "", 0)
            patch.setattr(render_media, "probe_media", invalid_probe)
        else:
            patch.setattr(render_text, "invoke_ffmpeg", lambda *a, **kw:
                          subprocess.CompletedProcess(a, 0, b"unreadable PNG", b"fontselect: local font"))
        failed = service.run(request)
    assert failed.outputs[0].status == "error"
    assert failed.outputs[0].error is not None
    assert failed.outputs[0].error.code == "runtime_unavailable"
    assert failed.outputs[0].qc_key is None
    assert not list(service.work_root.glob(f"campaigns/{plan.campaign_id}/editing/qc/*/*/*/report.json"))
    repaired = service.run(request)
    assert repaired.outputs[0].status == "passed" and not repaired.outputs[0].reused


def test_integrity_block_skips_dependents(qc_case: tuple[QcService, QcRequest, EditBatchPlan]) -> None:
    import hashlib
    service, request, _ = qc_case
    root = service.work_root / "campaigns" / request.campaign_id / "editing" / "renders"
    master = next((root / request.outputs[0].output_variant_id).glob("*/*.mp4"))
    master.write_bytes(b"synthetic corrupt master")
    receipt = master.with_name("render.json")
    data = json.loads(receipt.read_bytes())
    data.update(sha256=hashlib.sha256(master.read_bytes()).hexdigest(), sizeBytes=master.stat().st_size)
    receipt.write_text(json.dumps(data))
    result = service.run(request)
    assert result.outputs[0].status == "blocked" and result.outputs[0].qc_key is not None
    report = get_first(service, request, result.outputs[0].qc_key)
    assert [c.status for c in report.checks] == ["blocked", "skipped", "skipped"]
    assert report.audio is None


def test_long_paths_and_symlinks_follow_existing_platform_rules(qc_case: tuple[QcService, QcRequest, EditBatchPlan]) -> None:
    import os
    service, request, _ = qc_case
    result = service.run(request)
    assert result.outputs[0].path is not None
    path = service.work_root / result.outputs[0].path
    assert len(str(path)) > 260 and path.is_file()
    report = json.loads(path.read_bytes())
    master = service.work_root / report["masterPath"]
    master.unlink()
    private = service.project_root / "private.mp4"
    private.write_bytes(b"private bytes")
    try:
        master.symlink_to(private)
    except OSError:
        assert os.name == "nt" and not master.exists()
    assert service.run(request).outputs[0].status == "error"
