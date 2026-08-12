from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from auraly_pipeline.voices.audio import (
    PROCESSING_FILTER,
    AudioProcessingError,
    _require_complete_mp3,
    process_voice_audio,
)


def _synthetic_mp3(path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1.2",
            "-af",
            "adelay=200|200,apad=pad_dur=0.3",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


def test_process_voice_audio_preserves_raw_and_creates_measured_48k_output(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "provider.mp3"
    processed = tmp_path / "processed" / "voice-master.wav"
    raw.parent.mkdir()
    _synthetic_mp3(raw)
    before = raw.read_bytes()

    report = process_voice_audio(raw, processed)

    assert raw.read_bytes() == before
    assert processed.is_file()
    assert report.raw_sha256
    assert report.processed_sha256
    assert report.raw_size_bytes == len(before)
    assert report.duration_seconds > 0
    assert report.sample_rate == 48000
    assert report.channels == 1
    assert -70 < report.loudness_lufs < 0
    assert report.true_peak_dbfs <= 0
    assert report.leading_silence_seconds >= 0
    assert report.trailing_silence_seconds >= 0
    assert report.ffmpeg_filter == PROCESSING_FILTER


def test_processing_preserves_audio_after_internal_pause(tmp_path: Path) -> None:
    raw = tmp_path / "pause.mp3"
    processed = tmp_path / "pause.wav"
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono:d=0.6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.5",
            "-filter_complex",
            "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
            "-map",
            "[out]",
            "-codec:a",
            "libmp3lame",
            "-y",
            str(raw),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    report = process_voice_audio(raw, processed)
    assert report.duration_seconds > 1.35


def test_corrupted_provider_audio_is_rejected_without_processed_artifact(tmp_path: Path) -> None:
    raw = tmp_path / "raw.mp3"
    processed = tmp_path / "processed.wav"
    raw.write_bytes(b"not audio")
    with pytest.raises(AudioProcessingError):
        process_voice_audio(raw, processed)
    assert not processed.exists()


def test_truncated_provider_mp3_is_rejected_without_processed_artifact(tmp_path: Path) -> None:
    source = tmp_path / "complete.mp3"
    _synthetic_mp3(source)
    complete = source.read_bytes()
    for removed in (1, 10, 100, 500):
        raw = tmp_path / f"truncated-{removed}.mp3"
        processed = tmp_path / f"truncated-{removed}.wav"
        raw.write_bytes(complete[:-removed])
        with pytest.raises(AudioProcessingError):
            process_voice_audio(raw, processed)
        assert not processed.exists()


def test_whole_frame_truncated_provider_mp3_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "complete-frame.mp3"
    _synthetic_mp3(source)
    complete = source.read_bytes()
    accepted = []
    for removed in range(1, min(3000, len(complete))):
        raw = tmp_path / "candidate.mp3"
        processed = tmp_path / "candidate.wav"
        raw.write_bytes(complete[:-removed])
        try:
            _require_complete_mp3(raw)
        except AudioProcessingError:
            pass
        else:
            accepted.append(removed)
        processed.unlink(missing_ok=True)
    assert accepted == []


def test_mp3_without_declared_frame_count_fails_closed(tmp_path: Path) -> None:
    raw = tmp_path / "no-xing.mp3"
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1.2",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-write_xing",
            "0",
            "-y",
            str(raw),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    with pytest.raises(AudioProcessingError):
        _require_complete_mp3(raw)


def test_processing_never_overwrites_existing_output(tmp_path: Path) -> None:
    raw = tmp_path / "raw.mp3"
    output = tmp_path / "voice.wav"
    _synthetic_mp3(raw)
    output.write_bytes(b"existing")
    with pytest.raises(AudioProcessingError):
        process_voice_audio(raw, output)
    assert output.read_bytes() == b"existing"
