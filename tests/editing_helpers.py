from __future__ import annotations

from typing import Any
from pathlib import Path
import hashlib
import subprocess


def profile_data() -> dict[str, Any]:
    return {
        "profileId": "plain", "name": "Plain", "version": 1,
        "createdAt": "2026-10-01T00:00:00Z", "defaults": {},
    }


def request_data(profile_sha: str = "a" * 64) -> dict[str, Any]:
    return {
        "profileRef": {"profileId": "plain", "version": 1, "hash": profile_sha},
        "campaignId": "campaign-1", "videoId": "video-1", "outputVariantId": "a",
        "source": {"id": "source-1", "path": "source.mp4", "sha256": "b" * 64,
                   "durationSec": 7.224},
        "headlineText": "Headline A",
    }


def make_source(root: Path) -> Path:
    path = root / "source.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264",
        "-c:a", "aac", "-shortest", str(path),
    ], check=True, capture_output=True, timeout=30)
    return path


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
