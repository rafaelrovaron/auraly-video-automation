from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.qc_domain import QcPolicy
from auraly_pipeline.editing.render_runtime import detect_runtime, run_ffmpeg
from auraly_pipeline.editing.service import EditingService
from auraly_pipeline.probe import probe_media
from tests.render_helpers import make_render_plan


@pytest.mark.parametrize("loudness,peak,codes", [
    ("-30", "-1", []), ("-30.01", "-1", ["audio_low_loudness"]),
    ("-16", "-0.01", []), ("-16", "0", ["audio_peak_risk"]),
    ("-16", "0.01", ["audio_peak_risk"]), ("-inf", "-inf", ["audio_silent"]),
    ("-inf", "-80", ["audio_loudness_unmeasurable"]),
])
def test_audio_policy_uses_input_metrics(loudness: str, peak: str, codes: list[str],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_checks

    def measure(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "0:a:0" in args and args[-2:] == ["null", "-"]
        assert kwargs["timeout_sec"] == 120
        log = json.dumps({"input_i": loudness, "input_tp": peak,
                          "output_i": "-16", "output_tp": "-1.5"})
        return subprocess.CompletedProcess(args, 0, b"", log.encode())

    monkeypatch.setattr(qc_checks, "invoke_ffmpeg", measure)
    check, metrics = qc_checks.measure_qc_audio(Path("existing.mp4"), policy=QcPolicy())
    assert [f.code for f in check.findings] == codes
    assert check.status == ("blocked" if codes else "passed")
    assert metrics.integrated_loudness_lufs == (None if loudness == "-inf" else float(loudness))


@pytest.mark.parametrize("log", [b"not JSON", b'{}', b'{"input_i":"NaN","input_tp":"-1"}',
                                 b'{"input_i":"+inf","input_tp":"-1"}',
                                 b'{"input_i":"-16","input_tp":"-inf"}'])
def test_audio_invalid_measurement_errors(log: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_checks
    monkeypatch.setattr(qc_checks, "invoke_ffmpeg", lambda *a, **kw: subprocess.CompletedProcess(a, 0, b"", log))
    with pytest.raises(EditingError, match="audio"):
        qc_checks.measure_qc_audio(Path("existing.mp4"), policy=QcPolicy())


@pytest.mark.parametrize("filter_,code", [
    ("sine=frequency=997:sample_rate=48000:duration=3", None),
    ("sine=frequency=997:sample_rate=48000:duration=3,volume=0.01", "audio_low_loudness"),
    ("anullsrc=r=48000:cl=mono:d=3", "audio_silent"),
    ("aevalsrc=1.1*sin(2*PI*997*t):s=48000:d=3", "audio_peak_risk"),
])
def test_audio_real_aac_is_measured_without_changes(tmp_path: Path, filter_: str, code: str | None) -> None:
    from auraly_pipeline.editing.qc_checks import measure_qc_audio
    path = tmp_path / "audio.m4a"
    run_ffmpeg(["-v", "error", "-f", "lavfi", "-i", filter_, "-c:a", "aac", str(path)])
    before = path.read_bytes()
    check, metrics = measure_qc_audio(path, policy=QcPolicy())
    assert [f.code for f in check.findings] == ([] if code is None else [code])
    if code == "audio_peak_risk":
        assert metrics.true_peak_dbtp is not None and metrics.true_peak_dbtp >= 0
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_integrity_invalid_media_blocks_but_missing_runtime_and_timeout_error(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing.qc_checks import check_qc_integrity
    from auraly_pipeline.editing import render_media
    from auraly_pipeline.probe import ProbeError
    plan = make_render_plan(tmp_path)
    path = tmp_path / "source.mp4"
    receipt_probe = probe_media(path)
    # Correct identity but the source fixture has the wrong master dimensions.
    check, _ = check_qc_integrity(path, duration_sec=plan.source.duration_sec, receipt_probe=receipt_probe)
    assert check.status == "blocked"

    def missing(*args: object, **kwargs: object) -> None:
        raise ProbeError("private runtime detail") from FileNotFoundError()

    monkeypatch.setattr(render_media, "probe_media", missing)
    with pytest.raises(EditingError):
        check_qc_integrity(path, duration_sec=2, receipt_probe=receipt_probe)

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("ffprobe", 30)

    monkeypatch.setattr(render_media, "probe_media", timeout)
    with pytest.raises(EditingError):
        check_qc_integrity(path, duration_sec=2, receipt_probe=receipt_probe)


def test_disabled_text_needs_no_optional_assets(tmp_path: Path) -> None:
    from auraly_pipeline.editing.qc_checks import check_qc_text
    plan = make_render_plan(tmp_path)
    manifest = plan.outputs[0].manifest
    manifest.headline.enabled = False
    manifest.headline.font = None
    check = check_qc_text(manifest, plan.caption_input,
        editing=EditingService(project_root=tmp_path, work_root=tmp_path / "work"),
        staging=tmp_path / "absent-staging", runtime=detect_runtime())
    assert check.status == "not_applicable" and not (tmp_path / "absent-staging").exists()


def test_fit_and_timing_reuse_existing_renderer_rules(tmp_path: Path) -> None:
    from auraly_pipeline.editing.qc_checks import check_qc_text
    plan = make_render_plan(tmp_path)
    manifest = plan.outputs[0].manifest
    editing = EditingService(project_root=tmp_path, work_root=tmp_path / "work")
    stage = tmp_path / "stage"
    stage.mkdir()
    assert check_qc_text(manifest, plan.caption_input, editing=editing,
                         staging=stage, runtime=detect_runtime()).status == "passed"
    second = tmp_path / "stage-2"
    second.mkdir()
    manifest.headline.text = "X" * 100
    manifest.headline.fit_policy = "error"
    blocked = check_qc_text(manifest, plan.caption_input, editing=editing,
                            staging=second, runtime=detect_runtime())
    assert blocked.status == "blocked" and blocked.findings[0].code == "text_fit"
    manifest.captions.enabled = True
    manifest.captions.font = manifest.headline.font
    with pytest.raises(EditingError):
        check_qc_text(manifest, plan.caption_input, editing=editing,
                      staging=tmp_path / "stage-3", runtime=detect_runtime())


def test_text_runtime_failure_is_not_fit_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing import qc_checks
    plan = make_render_plan(tmp_path)

    def unavailable(*args: object, **kwargs: object) -> None:
        raise EditingError("runtime", "unavailable")

    monkeypatch.setattr(qc_checks, "write_ass", unavailable)
    with pytest.raises(EditingError, match="runtime"):
        qc_checks.check_qc_text(plan.outputs[0].manifest, plan.caption_input,
            editing=EditingService(project_root=tmp_path, work_root=tmp_path / "work"),
            staging=tmp_path, runtime=detect_runtime())
