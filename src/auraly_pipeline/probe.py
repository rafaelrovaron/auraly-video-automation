from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import Field, computed_field

from auraly_pipeline.models import ContractModel


class ProbeError(RuntimeError):
    """Raised when media metadata cannot be read safely."""


class VideoProbe(ContractModel):
    codec: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    nominal_fps: float = Field(gt=0)
    is_vfr: bool
    rotation: int = 0


class AudioProbe(ContractModel):
    codec: str
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)


class MediaProbe(ContractModel):
    format_name: str
    duration_sec: float = Field(gt=0)
    size_bytes: int = Field(ge=0)
    video: VideoProbe
    audio: AudioProbe | None = None
    warnings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_audio(self) -> bool:
        return self.audio is not None


def _rate(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ProbeError(f"Invalid frame rate from ffprobe: {value}") from exc


def _rotation(stream: dict[str, Any]) -> int:
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        return int(float(tags["rotate"]))
    for item in stream.get("side_data_list") or []:
        if "rotation" in item:
            return int(float(item["rotation"]))
    return 0


def parse_ffprobe_payload(payload: dict[str, Any]) -> MediaProbe:
    streams = payload.get("streams") or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ProbeError("No video stream found")

    format_data = payload.get("format") or {}
    try:
        duration = float(format_data.get("duration") or video_stream.get("duration") or 0)
    except (TypeError, ValueError) as exc:
        raise ProbeError("Invalid media duration") from exc
    if duration <= 0:
        raise ProbeError("Media duration must be greater than zero")

    average_fps = _rate(video_stream.get("avg_frame_rate"))
    nominal_fps = _rate(video_stream.get("r_frame_rate"))
    fps = average_fps or nominal_fps
    if fps <= 0:
        raise ProbeError("Video frame rate must be greater than zero")

    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    audio = None
    if audio_stream is not None:
        audio = AudioProbe(
            codec=str(audio_stream.get("codec_name") or "unknown"),
            sample_rate=int(audio_stream.get("sample_rate") or 0),
            channels=int(audio_stream.get("channels") or 0),
        )

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    is_vfr = bool(average_fps and nominal_fps and abs(average_fps - nominal_fps) > 0.001)
    warnings: list[str] = []
    if audio is None:
        warnings.append("media has no audio stream")
    if width < 540 or height < 960:
        warnings.append("video resolution is below the minimum preview resolution")
    if width > height:
        warnings.append("video is horizontal and requires an explicit crop plan")
    if is_vfr:
        warnings.append("variable frame rate detected; normalize to CFR before editing")

    return MediaProbe(
        format_name=str(format_data.get("format_name") or "unknown"),
        duration_sec=duration,
        size_bytes=int(format_data.get("size") or 0),
        video=VideoProbe(
            codec=str(video_stream.get("codec_name") or "unknown"),
            width=width,
            height=height,
            fps=fps,
            nominal_fps=nominal_fps or fps,
            is_vfr=is_vfr,
            rotation=_rotation(video_stream),
        ),
        audio=audio,
        warnings=warnings,
    )


def probe_media(path: Path, ffprobe_bin: str = "ffprobe") -> MediaProbe:
    if not path.is_file():
        raise ProbeError(f"Media file does not exist: {path}")
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ProbeError(f"ffprobe executable not found: {ffprobe_bin}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown ffprobe error"
        raise ProbeError(f"ffprobe failed for {path}: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc
    return parse_ffprobe_payload(payload)
