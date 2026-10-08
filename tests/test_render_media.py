from __future__ import annotations

from io import BytesIO
from pathlib import Path
import math
import struct
import subprocess
from typing import Literal, cast

from PIL import Image
import pytest

from auraly_pipeline.editing.domain import AssetRef, EditingError
from auraly_pipeline.editing.render_media import encode_master, audio_filter, check_master
from auraly_pipeline.editing.render_runtime import detect_runtime, run_ffmpeg
from tests.render_helpers import make_render_plan
from tests.editing_helpers import file_sha


def audio(path: Path) -> tuple[float, ...]:
    data = run_ffmpeg(["-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                       "-ar", "48000", "-f", "f32le", "-"])
    return struct.unpack("<" + "f" * (len(data) // 4), data)


def amplitude(samples: tuple[float, ...], frequency: int, start: float = .3, end: float = 1.6) -> float:
    values = samples[round(start * 48000):round(end * 48000)]
    phase = 2 * math.pi * frequency / 48000
    real = sum(v * math.cos(i * phase) for i, v in enumerate(values))
    imag = sum(v * math.sin(i * phase) for i, v in enumerate(values))
    return 2 * math.hypot(real, imag) / len(values)


def packet_hashes(path: Path) -> bytes:
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
        "-show_data_hash", "sha256", "-show_entries", "packet=data_hash", "-of", "csv=p=0", str(path)],
        capture_output=True, check=True, timeout=30).stdout


def image(path: Path, at: float = .5) -> Image.Image:
    data = run_ffmpeg(["-v", "error", "-i", str(path), "-ss", str(at), "-frames:v", "1",
                       "-f", "image2pipe", "-c:v", "png", "-"])
    return Image.open(BytesIO(data)).convert("RGB")


@pytest.mark.parametrize("fit,x", [("contain", .5), ("cover", 0), ("cover", 1)])
def test_cover_contain_and_zoom_geometry(tmp_path: Path, fit: Literal["contain", "cover"], x: float) -> None:
    plan = make_render_plan(tmp_path, pattern=True)
    m = plan.outputs[0].manifest
    m.headline.enabled = False
    m.framing.fit, m.framing.x = fit, x
    out = tmp_path / "master.mp4"
    probe = encode_master(m, source_path=tmp_path / "source.mp4", music_path=None,
                          ass_path=None, output_path=out, runtime=detect_runtime())
    assert (probe.video.width, probe.video.height, probe.video.fps) == (1080, 1920, 30)
    rgb = image(out)
    r, g, b = cast(tuple[int, int, int], rgb.getpixel((100, 100)))
    if fit == "contain":
        assert max(r, g, b) < 5  # black padding
        r, g, b = cast(tuple[int, int, int], rgb.getpixel((200, 800)))
        assert r > 200 and g < 10 and b < 10
    elif x == 0:
        assert r > 200 and g < 10 and b < 10
    else:
        assert g > 90 and r < 10 and b < 10


def test_no_music_copies_aac(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    out = tmp_path / "master.mp4"
    encode_master(plan.outputs[0].manifest, source_path=tmp_path / "source.mp4", music_path=None,
                  ass_path=None, output_path=out, runtime=detect_runtime())
    assert packet_hashes(out) == packet_hashes(tmp_path / "source.mp4")


def test_zoom_changes_video_over_time(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path, pattern=True)
    m = plan.outputs[0].manifest
    m.framing.fit = "contain"
    m.framing.zoom_start, m.framing.zoom_end = 1, 1.25
    out = tmp_path / "master.mp4"
    encode_master(m, source_path=tmp_path / "source.mp4", music_path=None,
                  ass_path=None, output_path=out, runtime=detect_runtime())
    first = cast(tuple[int, int, int], image(out, .05).getpixel((200, 630)))
    last = cast(tuple[int, int, int], image(out, 1.8).getpixel((200, 630)))
    assert max(first) < 10
    assert last[0] > 200 and last[1] < 10


def test_trim_loop_and_fades_use_selected_segment(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    m = plan.outputs[0].manifest
    m.music.enabled = True
    m.music.trim_start_sec, m.music.trim_end_sec = .5, 1
    m.music.fade_in_sec, m.music.fade_out_sec = .3, .4
    m.music.volume_db, m.music.duck_under_voice_db = 0, 0
    graph = audio_filter(m, music_duration_sec=1)
    assert graph is not None
    data = run_ffmpeg(["-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=2",
        "-f", "lavfi", "-i",
        "sine=frequency=880:sample_rate=48000:duration=0.5[a];sine=frequency=1760:sample_rate=48000:duration=0.5[b];[a][b]concat=n=2:v=0:a=1",
        "-filter_complex", graph, "-map", "[audio]", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"])
    samples = struct.unpack("<" + "f" * (len(data) // 4), data)
    middle = amplitude(samples, 1760, .8, 1.2)
    assert .12 < middle < .13 and amplitude(samples, 880, .8, 1.2) < .001
    assert amplitude(samples, 1760, .02, .08) < middle / 2
    assert amplitude(samples, 1760, 1.85, 1.95) < middle / 2


@pytest.mark.parametrize("loop,duration", [(False, .5), (True, .5), (True, 2.0)])
def test_music_shorter_than_voice_keeps_duration(tmp_path: Path, loop: bool, duration: float) -> None:
    plan = make_render_plan(tmp_path)
    m = plan.outputs[0].manifest
    music = tmp_path / "music.wav"
    run_ffmpeg(["-v", "error", "-f", "lavfi", "-i", f"sine=frequency=880:sample_rate=48000:duration={duration}", str(music)])
    m.music.enabled, m.music_accepted, m.music.loop = True, True, loop
    m.music.asset = AssetRef(path="music.wav", sha256=file_sha(music))
    m.music.fade_in_sec = m.music.fade_out_sec = 0
    out = tmp_path / "master.mp4"
    probe = encode_master(m, source_path=tmp_path / "source.mp4", music_path=music,
                          ass_path=None, output_path=out, runtime=detect_runtime())
    assert abs(probe.duration_sec - 2) <= 1/30 + 1024/48000
    samples = audio(out)
    assert amplitude(samples, 440, 1, 1.8) > .1  # voice remains after short music
    assert (amplitude(samples, 880, 1, 1.8) > .002) == loop


def test_mix_preserves_voice_gain(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    m = plan.outputs[0].manifest
    music = tmp_path / "music.wav"
    run_ffmpeg(["-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2", str(music)])
    m.music.enabled, m.music_accepted = True, True
    m.music.asset = AssetRef(path="music.wav", sha256=file_sha(music))
    m.music.fade_in_sec = m.music.fade_out_sec = 0
    out = tmp_path / "master.mp4"
    encode_master(m, source_path=tmp_path / "source.mp4", music_path=music,
                  ass_path=None, output_path=out, runtime=detect_runtime())
    source, mixed = audio(tmp_path / "source.mp4"), audio(out)
    assert abs(20 * math.log10(amplitude(mixed, 440) / amplitude(source, 440))) < .5
    assert abs(20 * math.log10(amplitude(mixed, 880) / amplitude(audio(music), 880)) - (-30)) < 1
    graph = audio_filter(m, music_duration_sec=2)
    assert graph is not None
    data = run_ffmpeg(["-v", "error", "-i", str(tmp_path / "source.mp4"), "-i", str(music),
                       "-filter_complex", graph, "-map", "[audio]", "-ac", "1",
                       "-ar", "48000", "-f", "f32le", "-"])
    pcm_mix = struct.unpack("<" + "f" * (len(data) // 4), data)
    # Isolate the added music from the voice's existing spectral content.
    residual = tuple(a - b for a, b in zip(pcm_mix, source))
    music_gain = 20 * math.log10(amplitude(residual, 880) / amplitude(audio(music), 880))
    assert abs(music_gain - (-22 - 8)) < 1


def test_limiter_no_auto_gain(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    m = plan.outputs[0].manifest
    m.music.enabled, m.music.volume_db, m.music.duck_under_voice_db = True, 0, 0
    m.music.fade_in_sec = m.music.fade_out_sec = 0
    graph = audio_filter(m, music_duration_sec=2)
    assert graph is not None
    for gain in (1, 8):
        data = run_ffmpeg(["-v", "error", "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=48000:duration=2,volume={gain}",
            "-f", "lavfi", "-i", f"sine=frequency=880:sample_rate=48000:duration=2,volume={gain}",
            "-filter_complex", graph, "-map", "[audio]", "-ac", "1", "-f", "f32le", "-"])
        samples = struct.unpack("<" + "f" * (len(data) // 4), data)
        assert max(abs(v) for v in samples) <= .951
        if gain == 1:
            assert .12 < amplitude(samples, 440) < .13


@pytest.mark.parametrize("case", ["corrupt", "no_audio", "rotation"])
def test_invalid_media_is_rejected(tmp_path: Path, case: str) -> None:
    plan = make_render_plan(tmp_path)
    source = tmp_path / "source.mp4"
    invalid = tmp_path / "invalid.mp4"
    if case == "corrupt":
        invalid.write_bytes(b"invalid media")
        with pytest.raises(EditingError):
            check_master(invalid, duration_sec=2, full_decode=True)
        return
    args = ["-v", "error", "-i", str(source), "-c", "copy"]
    args += ["-an"] if case == "no_audio" else []
    run_ffmpeg([*args, str(invalid)])
    if case == "rotation":
        # A real MP4 display matrix, independent of FFmpeg's rotate-tag support.
        data = bytearray(invalid.read_bytes())
        track = data.index(b"tkhd")
        assert data[track + 4] == 0  # version-zero track header
        struct.pack_into(">9i", data, track + 44, 0, 65536, 0, -65536, 0, 0, 0, 0, 1073741824)
        invalid.write_bytes(data)
    with pytest.raises(EditingError, match="source"):
        encode_master(plan.outputs[0].manifest, source_path=invalid, music_path=None,
                      ass_path=None, output_path=tmp_path / "out.mp4", runtime=detect_runtime())
    assert not (tmp_path / "out.mp4").exists()


def test_mix_preserves_delayed_voice_and_complete_duration(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    source = tmp_path / "offset.mp4"
    run_ffmpeg(["-v", "error", "-f", "lavfi", "-i", "color=s=320x180:r=30:d=2",
                "-itsoffset", "0.4", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "2", str(source)])
    music = tmp_path / "silence.wav"
    run_ffmpeg(["-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=2", str(music)])
    m = plan.outputs[0].manifest
    m.music.enabled = True
    m.music.fade_in_sec = m.music.fade_out_sec = 0
    output = tmp_path / "mixed.mp4"
    encode_master(m, source_path=source, music_path=music, ass_path=None,
                  output_path=output, runtime=detect_runtime())
    samples = audio(output)
    assert amplitude(samples, 440, .05, .25) < .001
    assert .12 < amplitude(samples, 440, .7, 1.2) < .13
    assert amplitude(samples, 440, 1.8, 1.95) > .1
    assert len(samples) / 48000 >= 1.98


def test_short_nonloop_music_fades_before_silence(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    m = plan.outputs[0].manifest
    m.music.enabled, m.music.loop = True, False
    m.music.volume_db = m.music.duck_under_voice_db = m.music.fade_in_sec = 0
    m.music.fade_out_sec = .2
    graph = audio_filter(m, music_duration_sec=.5)
    assert graph is not None
    data = run_ffmpeg(["-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=2",
                      "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=0.5",
                      "-filter_complex", graph, "-map", "[audio]", "-ac", "1", "-f", "f32le", "-"])
    samples = struct.unpack("<" + "f" * (len(data) // 4), data)
    assert amplitude(samples, 880, .1, .2) > .12
    assert amplitude(samples, 880, .45, .5) < .04
    assert amplitude(samples, 880, .7, 1.7) < .001
