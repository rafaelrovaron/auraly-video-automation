import json
from pathlib import Path

import pytest

from auraly_pipeline.ingest import IngestError, ingest_reel
from auraly_pipeline.probe import AudioProbe, MediaProbe, VideoProbe


COPY = """# Copy

## Headline para tela
**DON'T YOU DARE IGNORE THIS SIGN**

## Hook
This may be the sign you were waiting for.

## Body
Your pattern is trying to show you something.

## CTA
Take the one-minute reading now.
"""


def fake_probe() -> MediaProbe:
    return MediaProbe(
        formatName="mov,mp4,m4a,3gp,3g2,mj2",
        durationSec=10,
        sizeBytes=64,
        video=VideoProbe(
            codec="h264",
            width=1080,
            height=1920,
            fps=30,
            nominalFps=30,
            isVfr=False,
            rotation=0,
        ),
        audio=AudioProbe(codec="aac", sampleRate=48000, channels=2),
    )


def test_ingest_copies_sources_and_writes_valid_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "input.mp4"
    copy = tmp_path / "copy.md"
    video.write_bytes(b"original-video")
    copy.write_text(COPY, encoding="utf-8")
    work_root = tmp_path / "work"
    monkeypatch.setattr("auraly_pipeline.ingest.probe_media", lambda _: fake_probe())

    reel_dir = ingest_reel(
        video=video,
        copy=copy,
        character="susan-smith",
        work_root=work_root,
        reel_id="Susan Sign 001",
    )

    assert reel_dir == work_root / "susan-sign-001"
    assert (reel_dir / "source/heygen.mp4").read_bytes() == b"original-video"
    assert (reel_dir / "source/copy.md").read_text(encoding="utf-8") == COPY
    assert video.read_bytes() == b"original-video"
    assert copy.read_text(encoding="utf-8") == COPY

    manifest = json.loads((reel_dir / "manifest/edit.json").read_text(encoding="utf-8"))
    assert manifest["project"]["reelId"] == "susan-sign-001"
    assert manifest["project"]["template"] == "susan-hard-truth-v1"
    assert manifest["headline"]["spoken"] is False
    assert manifest["headline"]["text"] == "DON'T YOU DARE IGNORE THIS SIGN"
    assert manifest["source"]["video"] == "source/heygen.mp4"
    assert manifest["source"]["copy"] == "source/copy.md"
    assert (reel_dir / "probe.json").is_file()


def test_ingest_refuses_to_overwrite_existing_reel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "input.mp4"
    copy = tmp_path / "copy.md"
    video.write_bytes(b"video")
    copy.write_text(COPY, encoding="utf-8")
    monkeypatch.setattr("auraly_pipeline.ingest.probe_media", lambda _: fake_probe())

    ingest_reel(video, copy, "susan-smith", tmp_path / "work", "same-id")

    with pytest.raises(IngestError, match="already exists"):
        ingest_reel(video, copy, "susan-smith", tmp_path / "work", "same-id")
