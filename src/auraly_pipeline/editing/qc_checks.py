from __future__ import annotations

import json
import math
from pathlib import Path

from auraly_pipeline.editing.batch_domain import CaptionInput, CaptionTimingInput, ResolvedCaptionCue
from auraly_pipeline.editing.domain import EditManifestV2, EditingError
from auraly_pipeline.editing.qc_domain import QcAudio, QcCheck, QcFinding, QcPolicy, audio_findings
from auraly_pipeline.editing.render_domain import RenderRuntime, validate_supported
from auraly_pipeline.editing.render_media import MasterCheckError, check_master
from auraly_pipeline.editing.render_runtime import invoke_ffmpeg
from auraly_pipeline.editing.render_text import write_ass
from auraly_pipeline.editing.service import EditingService
from auraly_pipeline.probe import MediaProbe


def check_qc_integrity(path: Path, *, duration_sec: float,
                       receipt_probe: MediaProbe) -> tuple[QcCheck, MediaProbe | None]:
    probe: MediaProbe | None = None
    try:
        probe = check_master(path, duration_sec=duration_sec, full_decode=True)
    except MasterCheckError as exc:
        if exc.kind == "operational":
            raise EditingError("runtime", "master analysis unavailable or timed out") from None
        probe = exc.probe
        if probe is not None and probe != receipt_probe:
            raise EditingError("artifact", "master metadata differs from receipt") from None
        return QcCheck(name="integrity", status="blocked", findings=[QcFinding(
            code="master_invalid", field="output", message="master violates media integrity requirements")]), probe
    if probe != receipt_probe:
        raise EditingError("artifact", "master metadata differs from receipt")
    return QcCheck(name="integrity", status="passed"), probe


def measure_qc_audio(path: Path, *, policy: QcPolicy) -> tuple[QcCheck, QcAudio]:
    result = invoke_ffmpeg(["-v", "info", "-i", str(path), "-map", "0:a:0", "-vn", "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], timeout_sec=120)
    try:
        log = result.stderr.decode("utf-8", errors="replace")
        data = json.loads(log[log.rfind("{"):log.rfind("}") + 1])
        values: list[float | None] = []
        for name in ("input_i", "input_tp"):
            raw = data[name]
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
                raise ValueError()
            value = float(raw)
            if value == -math.inf:
                values.append(None)
            elif not math.isfinite(value):
                raise ValueError()
            else:
                values.append(value)
        audio = QcAudio(integrated_loudness_lufs=values[0], true_peak_dbtp=values[1])
        findings = audio_findings(audio, policy)
        return QcCheck(name="audio", status="blocked" if findings else "passed", findings=findings), audio
    except (ValueError, TypeError, KeyError, IndexError):
        raise EditingError("audio", "audio measurement failed") from None


def validate_qc_text_assets(manifest: EditManifestV2, caption: CaptionInput, *,
                            editing: EditingService) -> dict[str, Path]:
    fonts = {}
    for field, style in (("headline", manifest.headline), ("captions", manifest.captions)):
        if style.enabled:
            if style.font is None:
                raise EditingError(field + ".font", "local font required")
            fonts[field] = editing.validate_font(style.font, field + ".font")
    if manifest.captions.enabled:
        if caption.timing_ref is None:
            raise EditingError("timingRef", "accepted caption timing required")
        path = editing.validate_asset(caption.timing_ref, "timingRef")
        try:
            timing = CaptionTimingInput.model_validate_json(path.read_bytes())
            if (timing.source_sha256, timing.copy_master_id, timing.copy_hash,
                timing.processed_audio_sha256, timing.origin, timing.accepted_by, timing.timebase) != (
                    manifest.source.sha256, caption.copy_ref.id, caption.copy_ref.hash,
                    caption.voice_ref.hash, caption.origin, caption.accepted_by, caption.timebase):
                raise ValueError()
            tokens = caption.text.split()
            cues = [ResolvedCaptionCue(**cue.model_dump(), text=" ".join(tokens[cue.token_start:cue.token_end]))
                    for cue in timing.cues]
            if cues != caption.cues:
                raise ValueError()
        except (OSError, ValueError):
            raise EditingError("timingRef", "timing content does not match saved plan") from None
    return fonts


def check_qc_text(manifest: EditManifestV2, caption: CaptionInput, *, editing: EditingService,
                  staging: Path, runtime: RenderRuntime) -> QcCheck:
    if not manifest.headline.enabled and not manifest.captions.enabled:
        return QcCheck(name="text", status="not_applicable")
    validate_supported(manifest, caption)
    fonts = validate_qc_text_assets(manifest, caption, editing=editing)
    try:
        write_ass(manifest, caption, font_paths=fonts, staging=staging, runtime=runtime)
    except EditingError as exc:
        if exc.field != "text.fit":
            raise
        return QcCheck(name="text", status="blocked", findings=[QcFinding(
            code="text_fit", field="text.fit", message="text does not fit renderer safe zones")])
    return QcCheck(name="text", status="passed")
