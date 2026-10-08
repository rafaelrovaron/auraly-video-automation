from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_domain import (
    RenderReceipt, RenderRuntime, render_key, validate_supported,
)
from auraly_pipeline.editing.render_runtime import detect_runtime, run_ffmpeg
from auraly_pipeline.editing.resolver import content_hash
from tests.render_helpers import make_render_plan


def test_render_key_changes_with_output_or_runtime() -> None:
    runtime = RenderRuntime(fingerprint="a" * 64, ffmpeg_version="test",
                            libass_version="test", encoding={"crf": "18"})
    key = render_key("b" * 64, runtime)
    assert len(key) == 64
    assert key == content_hash({"outputHash": "b" * 64, "rendererVersion": "1.0",
                                "runtimeFingerprint": "a" * 64})
    assert render_key("c" * 64, runtime) != key
    assert render_key("b" * 64, runtime.model_copy(update={"fingerprint": "d" * 64})) != key


@pytest.mark.parametrize("section,field,value,error", [
    ("output", "width", 720, "output"),
    ("output", "fps", 25, "output"),
    ("headline", "font_weight", 500, "headline.fontWeight"),
    ("captions", "enabled", True, "captionInput"),
])
def test_supported_limits(tmp_path: Path, section: str, field: str, value: object, error: str) -> None:
    plan = make_render_plan(tmp_path)
    manifest = plan.outputs[0].manifest
    setattr(getattr(manifest, section), field, value)
    with pytest.raises(EditingError) as exc:
        validate_supported(manifest, plan.caption_input)
    assert exc.value.field == error


def test_disabled_captions_do_not_require_timing_or_supported_highlight(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    manifest = plan.outputs[0].manifest
    manifest.captions.highlight_enabled = True
    manifest.captions.font_weight = 500
    validate_supported(manifest, plan.caption_input)


@pytest.mark.parametrize("path", ["/outside.mp4", "C:/outside.mp4", "../outside.mp4", "a\\b.mp4"])
def test_receipt_relative_paths(path: str) -> None:
    # Minimal invalid payload must specifically reject the supplied path too.
    with pytest.raises(ValidationError) as exc:
        RenderReceipt.model_validate({"path": path})
    assert any(e["loc"] == ("path",) for e in exc.value.errors())


def test_runtime_capability_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("C:/private/token-secret")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(EditingError) as exc:
        detect_runtime()
    assert exc.value.field == "runtime"
    assert "private" not in str(exc.value) and "token" not in str(exc.value)


def test_runtime_probes_real_local_ffmpeg() -> None:
    runtime = detect_runtime()
    assert len(runtime.fingerprint) == 64 and runtime.libass_version
    assert runtime.encoding["crf"] == "18"
    result = run_ffmpeg(["-f", "lavfi", "-i", "color=s=16x16:d=0.1", "-f", "null", "-"])
    assert result == b""


def test_render_schema_export_keeps_existing_editing_contract(tmp_path: Path) -> None:
    from auraly_pipeline.editing.schema import export_editing_schemas, export_render_schemas
    assert len(export_editing_schemas(tmp_path)) == 6
    assert {p.name for p in export_render_schemas(tmp_path)} == {
        "render-receipt.schema.json", "render-batch-result.schema.json",
    }
    for path in export_render_schemas(tmp_path):
        assert json.loads(path.read_text())["additionalProperties"] is False
