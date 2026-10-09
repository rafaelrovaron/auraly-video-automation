from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest
import pysubs2  # type: ignore[import-untyped]

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.batch_domain import CaptionInput
from auraly_pipeline.editing.render_runtime import detect_runtime, run_ffmpeg
from auraly_pipeline.editing.render_text import write_ass
from tests.render_helpers import make_render_plan


def frame(path: Path, at: float = .1) -> Image.Image:
    data = run_ffmpeg(["-v", "error", "-f", "lavfi", "-i",
        "color=c=black@0:s=1080x1920:r=30:d=2,format=rgba",
        "-vf", f"ass=filename={path.name}:fontsdir=fonts:alpha=1",
        "-ss", str(at), "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], cwd=path.parent)
    return Image.open(BytesIO(data)).convert("RGBA")


def test_measurement_clipping_remains_fit_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from auraly_pipeline.editing import render_text
    png = BytesIO()
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(png, format="PNG")
    monkeypatch.setattr(render_text, "invoke_ffmpeg", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, png.getvalue(), b"fontselect: local font"))
    doc = pysubs2.SSAFile()
    doc.info.update(PlayResX="4", PlayResY="4")
    with pytest.raises(EditingError) as caught:
        render_text.measure_ass(doc.to_string("ass"), staging=tmp_path, runtime=detect_runtime())
    assert caught.value.field == "text.fit"


def script(tmp_path: Path, **changes: object) -> Path:
    plan = make_render_plan(tmp_path / "project")
    manifest = plan.outputs[0].manifest
    for key, value in changes.items():
        setattr(manifest.headline, key, value)
    stage = tmp_path / "stage"
    stage.mkdir()
    return write_ass(manifest, plan.caption_input,
                     font_paths={"headline": tmp_path / "project/font.ttf"},
                     staging=stage, runtime=detect_runtime())


def test_literal_ass_text(tmp_path: Path) -> None:
    path = script(tmp_path, text=r"{\pos(1,2)}\N")
    bbox = frame(path).getchannel("A").getbbox()
    assert bbox is not None
    assert bbox[0] > 54 and bbox[1] >= 190  # no injected position or newline
    assert bbox[2] - bbox[0] > 250  # the complete literal string, not suppressed braces
    assert bbox[3] - bbox[1] < 100


@pytest.mark.parametrize("anchor,y", [("top", .1), ("center", .5), ("bottom", .9)])
def test_fit_and_safe_zones(tmp_path: Path, anchor: str, y: float) -> None:
    path = script(tmp_path, text="A simple headline", anchor=anchor, y=y,
                  background_enabled=True, background_padding_px=10,
                  stroke_width_px=2, shadow_enabled=True, shadow_offset_x=-4,
                  shadow_offset_y=5, line_height=1.2)
    bbox = frame(path).getchannel("A").getbbox()
    assert bbox is not None
    assert 54 <= bbox[0] < bbox[2] <= 1026
    assert 96 <= bbox[1] < bbox[3] <= 1824


@pytest.mark.parametrize("fit", ["error", "wrap"])
def test_overflow_fails_without_truncation(tmp_path: Path, fit: str) -> None:
    with pytest.raises(EditingError, match="fit"):
        script(tmp_path, text="This headline cannot fit " * 10, font_size_px=150,
               max_lines=1, fit_policy=fit)


def test_shrink_makes_long_text_fit(tmp_path: Path) -> None:
    path = script(tmp_path, text="A longer headline with more words", font_size_px=100,
                  max_lines=1, fit_policy="shrink")
    bbox = frame(path).getchannel("A").getbbox()
    assert bbox is not None and bbox[2] - bbox[0] <= 972


def test_caption_intervals_use_saved_cues(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path / "project")
    manifest = plan.outputs[0].manifest
    manifest.headline.enabled = False
    manifest.captions.enabled = True
    manifest.captions.font = manifest.headline.font
    payload = plan.caption_input.model_dump(mode="json", by_alias=True)
    payload.update({"timingStatus": "provided", "timingRef": {"path": "timing.json", "sha256": "a" * 64},
                    "origin": "manual", "acceptedBy": "tester", "timebase": "source_mp4",
                    "cues": [{"startSec": .5, "endSec": 1.0, "tokenStart": 0,
                              "tokenEnd": len(plan.caption_input.text.split()),
                              "text": " ".join(plan.caption_input.text.split())}]})
    caption = CaptionInput.model_validate(payload)
    stage = tmp_path / "stage"
    stage.mkdir()
    path = write_ass(manifest, caption, font_paths={"captions": tmp_path / "project/font.ttf"},
                     staging=stage, runtime=detect_runtime())
    events = pysubs2.load(str(path)).events
    # Fonts can wrap differently; preserve all cue text and its saved interval.
    assert " ".join(" ".join(event.plaintext for event in events).split()) == "Visual hook Body words. Click now!"
    assert all((event.start, event.end) == (500, 1000) for event in events)
    assert frame(path, .1).getchannel("A").getbbox() is None
    assert frame(path, .7).getchannel("A").getbbox() is not None
    assert frame(path, 1.2).getchannel("A").getbbox() is None


def test_font_selection_has_no_silent_fallback(tmp_path: Path) -> None:
    with pytest.raises(EditingError, match="font"):
        script(tmp_path, text="Missing glyph \U0010ffff")


def test_shrink_continues_past_measurement_canvas_limit(tmp_path: Path) -> None:
    path = script(tmp_path, text="W" * 100, font_size_px=100, max_lines=1, fit_policy="shrink")
    bbox = frame(path).getchannel("A").getbbox()
    assert bbox is not None and bbox[2] - bbox[0] <= 972
