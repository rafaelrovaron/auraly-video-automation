from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import TypedDict

from pydantic import Field

from auraly_pipeline.models import ContractModel


EDGE_TRIM_FILTER = (
    "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,"
    "areverse,"
    "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,"
    "areverse"
)
PROCESSING_FILTER = f"{EDGE_TRIM_FILTER},loudnorm=I=-16:TP=-1.5:LRA=11"
_SILENCE_START = re.compile(r"silence_start: ([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end: ([0-9.]+)")
_MP3_BITRATES = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}


def _require_complete_mp3(path: Path) -> None:
    data = path.read_bytes()
    start = 0
    if data.startswith(b"ID3") and len(data) >= 10:
        size_bytes = data[6:10]
        if any(value & 0x80 for value in size_bytes):
            raise AudioProcessingError(AudioProcessingError.public_message)
        start = 10 + sum(value << shift for value, shift in zip(size_bytes, (21, 14, 7, 0)))
    end = len(data) - 128 if len(data) >= 128 and data[-128:-125] == b"TAG" else len(data)
    position = start
    frames = 0
    declared_frames: int | None = None
    while position + 4 <= end:
        header = int.from_bytes(data[position : position + 4], "big")
        if header >> 21 != 0x7FF:
            raise AudioProcessingError(AudioProcessingError.public_message)
        version_bits = (header >> 19) & 0x3
        layer_bits = (header >> 17) & 0x3
        bitrate_index = (header >> 12) & 0xF
        sample_index = (header >> 10) & 0x3
        padding = (header >> 9) & 0x1
        if version_bits == 1 or layer_bits == 0 or bitrate_index in {0, 15} or sample_index == 3:
            raise AudioProcessingError(AudioProcessingError.public_message)
        version = 1 if version_bits == 3 else 2
        layer = 4 - layer_bits
        bitrates = _MP3_BITRATES[(version, layer)]
        bitrate = bitrates[bitrate_index] * 1000
        base_rate = (44100, 48000, 32000)[sample_index]
        sample_rate = (
            base_rate if version_bits == 3 else base_rate // (2 if version_bits == 2 else 4)
        )
        if layer == 1:
            frame_size = ((12 * bitrate // sample_rate) + padding) * 4
        else:
            coefficient = 144 if layer == 2 or version == 1 else 72
            frame_size = coefficient * bitrate // sample_rate + padding
        if frame_size <= 4 or position + frame_size > end:
            raise AudioProcessingError(AudioProcessingError.public_message)
        if frames == 0 and layer == 3:
            channel_mode = (header >> 6) & 0x3
            if version == 1:
                side_info_size = 17 if channel_mode == 3 else 32
            else:
                side_info_size = 9 if channel_mode == 3 else 17
            xing_offset = position + 4 + side_info_size
            marker = data[xing_offset : xing_offset + 4]
            if marker in {b"Xing", b"Info"} and xing_offset + 12 <= position + frame_size:
                flags = int.from_bytes(data[xing_offset + 4 : xing_offset + 8], "big")
                if flags & 0x1:
                    declared_frames = int.from_bytes(
                        data[xing_offset + 8 : xing_offset + 12], "big"
                    )
            vbri_offset = position + 4 + 32
            if (
                data[vbri_offset : vbri_offset + 4] == b"VBRI"
                and vbri_offset + 18 <= position + frame_size
            ):
                declared_frames = int.from_bytes(data[vbri_offset + 14 : vbri_offset + 18], "big")
        position += frame_size
        frames += 1
    if frames == 0 or position != end or declared_frames is None or frames != declared_frames + 1:
        raise AudioProcessingError(AudioProcessingError.public_message)


class AudioProcessingError(RuntimeError):
    public_message = "The Voice Master audio could not be processed safely."


class AudioProcessingReport(ContractModel):
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    processed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_size_bytes: int = Field(gt=0)
    raw_format: str
    duration_seconds: float = Field(gt=0)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    loudness_lufs: float
    true_peak_dbfs: float
    leading_silence_seconds: float = Field(ge=0)
    trailing_silence_seconds: float = Field(ge=0)
    long_internal_pauses: list[tuple[float, float]]
    ffmpeg_filter: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError) as exc:
        raise AudioProcessingError(AudioProcessingError.public_message) from exc


class _AudioProbeData(TypedDict):
    format: str
    duration: float
    sample_rate: int
    channels: int


def _probe(path: Path) -> _AudioProbeData:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if result.returncode:
        raise AudioProcessingError(AudioProcessingError.public_message)
    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        audio = next(item for item in streams if item.get("codec_type") == "audio")
        duration = float(payload["format"]["duration"])
        return {
            "format": str(payload["format"].get("format_name", "unknown")).split(",")[0],
            "duration": duration,
            "sample_rate": int(audio["sample_rate"]),
            "channels": int(audio["channels"]),
        }
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioProcessingError(AudioProcessingError.public_message) from exc


def _loudness(path: Path) -> tuple[float, float]:
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    if result.returncode:
        raise AudioProcessingError(AudioProcessingError.public_message)
    try:
        start = result.stderr.rfind("{")
        end = result.stderr.find("}", start)
        metrics = json.loads(result.stderr[start : end + 1])
        return float(metrics["input_i"]), float(metrics["input_tp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioProcessingError(AudioProcessingError.public_message) from exc


def _silence(path: Path, duration: float) -> tuple[float, float, list[tuple[float, float]]]:
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-45dB:d=0.5",
            "-f",
            "null",
            "-",
        ]
    )
    if result.returncode:
        raise AudioProcessingError(AudioProcessingError.public_message)
    starts = [float(value) for value in _SILENCE_START.findall(result.stderr)]
    ends = [float(value) for value in _SILENCE_END.findall(result.stderr)]
    intervals = list(zip(starts, ends, strict=False))
    leading = intervals[0][1] if intervals and intervals[0][0] <= 0.01 else 0.0
    trailing = (
        duration - intervals[-1][0] if intervals and intervals[-1][1] >= duration - 0.05 else 0.0
    )
    internal = [item for item in intervals if item[0] > 0.01 and item[1] < duration - 0.05]
    return round(max(0.0, leading), 6), round(max(0.0, trailing), 6), internal


def process_voice_audio(raw_path: Path, output_path: Path) -> AudioProcessingReport:
    if not raw_path.is_file() or raw_path.stat().st_size <= 0 or output_path.exists():
        raise AudioProcessingError(AudioProcessingError.public_message)
    raw_probe = _probe(raw_path)
    raw_decode = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-i",
            str(raw_path),
            "-f",
            "null",
            "-",
        ]
    )
    if raw_decode.returncode:
        raise AudioProcessingError(AudioProcessingError.public_message)
    if raw_probe["format"] == "mp3":
        _require_complete_mp3(raw_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.partial")
    reservation_fd: int | None = None
    try:
        reservation_fd = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AudioProcessingError(AudioProcessingError.public_message) from exc
    finally:
        if reservation_fd is not None:
            os.close(reservation_fd)
    if temporary.exists():
        temporary.unlink()
    result = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(raw_path),
            "-af",
            PROCESSING_FILTER,
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            "-map_metadata",
            "-1",
            "-f",
            "wav",
            "-y",
            str(temporary),
        ]
    )
    if result.returncode:
        temporary.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise AudioProcessingError(AudioProcessingError.public_message)
    try:
        processed_probe = _probe(temporary)
        decode = _run(["ffmpeg", "-v", "error", "-i", str(temporary), "-f", "null", "-"])
        if decode.returncode:
            raise AudioProcessingError(AudioProcessingError.public_message)
        loudness, peak = _loudness(temporary)
        leading, trailing, internal = _silence(temporary, float(processed_probe["duration"]))
        temporary.replace(output_path)
        # `output_path` was exclusively reserved above; replace only swaps our
        # reservation inode, so no concurrent creator can be overwritten.
    except Exception:
        temporary.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise
    return AudioProcessingReport(
        raw_sha256=_sha256(raw_path),
        processed_sha256=_sha256(output_path),
        raw_size_bytes=raw_path.stat().st_size,
        raw_format=str(raw_probe["format"]),
        duration_seconds=float(processed_probe["duration"]),
        sample_rate=int(processed_probe["sample_rate"]),
        channels=int(processed_probe["channels"]),
        loudness_lufs=loudness,
        true_peak_dbfs=peak,
        leading_silence_seconds=leading,
        trailing_silence_seconds=trailing,
        long_internal_pauses=internal,
        ffmpeg_filter=PROCESSING_FILTER,
    )
