from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

from auraly_pipeline.copy_parser import CopyFormatError, parse_copy
from auraly_pipeline.models import EditManifest
from auraly_pipeline.paths import UnsafePathError, create_new_workdir, slugify_reel_id
from auraly_pipeline.probe import ProbeError, probe_media


Character = Literal["susan-smith", "soul-constellation"]

_TEMPLATES: dict[str, str] = {
    "susan-smith": "susan-hard-truth-v1",
    "soul-constellation": "soul-constellation-v1",
}


class IngestError(RuntimeError):
    """Raised when a source Reel cannot be safely ingested."""


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ingest_reel(
    video: Path,
    copy: Path,
    character: Character,
    work_root: Path,
    reel_id: str | None = None,
) -> Path:
    video = video.resolve()
    copy = copy.resolve()
    if not video.is_file():
        raise IngestError(f"video does not exist: {video}")
    if video.suffix.casefold() != ".mp4":
        raise IngestError("HeyGen source video must be an MP4")
    if not copy.is_file():
        raise IngestError(f"copy does not exist: {copy}")
    if character not in _TEMPLATES:
        raise IngestError(f"unsupported character: {character}")

    try:
        parsed_copy = parse_copy(copy.read_text(encoding="utf-8-sig"))
        probe = probe_media(video)
    except (CopyFormatError, ProbeError, UnicodeError, OSError) as exc:
        raise IngestError(str(exc)) from exc
    if not probe.has_audio:
        raise IngestError("HeyGen source video must contain an audio stream")

    safe_reel_id = slugify_reel_id(reel_id or f"{character}-{video.stem}")
    try:
        reel_dir = create_new_workdir(work_root.resolve(), safe_reel_id)
    except UnsafePathError as exc:
        raise IngestError(str(exc)) from exc

    try:
        source_dir = reel_dir / "source"
        manifest_dir = reel_dir / "manifest"
        source_dir.mkdir()
        manifest_dir.mkdir()
        shutil.copy2(video, source_dir / "heygen.mp4")
        shutil.copy2(copy, source_dir / "copy.md")

        duration = probe.duration_sec
        manifest = EditManifest.model_validate(
            {
                "schemaVersion": "1.0",
                "project": {
                    "reelId": safe_reel_id,
                    "character": character,
                    "template": _TEMPLATES[character],
                },
                "source": {
                    "video": "source/heygen.mp4",
                    "copy": "source/copy.md",
                    "durationSec": duration,
                },
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "headline": {
                    "text": parsed_copy.headline,
                    "start": 0,
                    "end": min(3.2, duration),
                    "spoken": False,
                },
                "render": {
                    "width": 1080,
                    "height": 1920,
                    "fps": 30,
                    "format": "mp4",
                    "codec": "h264",
                },
                "review": {"status": "draft"},
            }
        )
        _write_json(reel_dir / "probe.json", probe.model_dump(by_alias=True, mode="json"))
        _write_json(
            manifest_dir / "edit.json",
            manifest.model_dump(by_alias=True, mode="json"),
        )
    except Exception:
        shutil.rmtree(reel_dir, ignore_errors=True)
        raise

    return reel_dir
