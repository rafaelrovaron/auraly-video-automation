from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import subprocess
from typing import Literal

from auraly_pipeline.editing.domain import EditManifestV2, EditingError
from auraly_pipeline.editing.render_domain import RenderRuntime
from auraly_pipeline.editing.render_runtime import invoke_ffmpeg, run_ffmpeg
from auraly_pipeline.probe import MediaProbe, ProbeError, probe_media


def video_filter(manifest: EditManifestV2, source: MediaProbe) -> str:
    f = manifest.framing
    ratios = (1080 / source.video.width, 1920 / source.video.height)
    base = max(ratios) if f.fit == "cover" else min(ratios)
    zoom = f"({f.scale:g}*({f.zoom_start:g}+({f.zoom_end-f.zoom_start:g})*min(t/{manifest.source.duration_sec:g},1)))"
    w, h = source.video.width * base, source.video.height * base
    # FFmpeg normalizes the input against its common container start; do not
    # independently zero each stream and lose the original A/V relationship.
    return ("fps=30:start_time=0,"
            f"scale=w='ceil({w:g}*{zoom}/2)*2':h='ceil({h:g}*{zoom}/2)*2':eval=frame,"
            f"pad=w='max(iw,1080)':h='max(ih,1920)':x='(ow-iw)*{f.x:g}':y='(oh-ih)*{f.y:g}':color=black:eval=frame,"
            f"crop=1080:1920:x='(iw-1080)*{f.x:g}':y='(ih-1920)*{f.y:g}',setsar=1")


def audio_filter(manifest: EditManifestV2, *, music_duration_sec: float) -> str | None:
    if not manifest.music.enabled:
        return None
    m = manifest.music
    end = m.trim_end_sec if m.trim_end_sec is not None else music_duration_sec
    if not (math.isfinite(end) and 0 <= m.trim_start_sec < end <= music_duration_sec):
        raise EditingError("music.trimEndSec", "music trim is outside local audio")
    duration = manifest.source.duration_sec
    segment = end - m.trim_start_sec
    if max(m.fade_in_sec, m.fade_out_sec) > min(segment, duration):
        raise EditingError("music.fadeOutSec", "fade exceeds music segment")
    repeat = f",aloop=loop=-1:size={round(segment*48000)}:start=0" if m.loop else ""
    fades = (f",afade=t=in:st=0:d={m.fade_in_sec:g}" if m.fade_in_sec > 0 else "")
    if m.fade_out_sec > 0:
        effective_end = duration if m.loop else min(segment, duration)
        fades += f",afade=t=out:st={max(0, effective_end-m.fade_out_sec):g}:d={m.fade_out_sec:g}"
    return (f"[1:a:0]aresample=48000,atrim=start={m.trim_start_sec:g}:end={end:g},"
            f"asetpts=PTS-STARTPTS{repeat},apad,atrim=duration={duration:g},"
            f"volume={m.volume_db+m.duck_under_voice_db:g}dB{fades}[music];"
            f"[0:a:0]aresample=48000:first_pts=0,apad,atrim=duration={duration:g}[voice];"
            "[voice][music]amix=inputs=2:duration=first:normalize=0,"
            "alimiter=limit=0.95:level=0:latency=1[audio]")


def _faststart(path: Path) -> bool:
    size = path.stat().st_size
    with path.open("rb") as stream:
        while stream.tell() < size:
            start = stream.tell()
            header = stream.read(8)
            if len(header) != 8:
                return False
            length, name = struct.unpack(">I4s", header)
            if length == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    return False
                length = struct.unpack(">Q", extended)[0]
            if name == b"moov":
                return True
            if name == b"mdat" or length < 8 or start + length > size:
                return False
            stream.seek(start + length)
    return False


class MasterCheckError(EditingError):
    def __init__(self, kind: Literal["invalid", "operational"], probe: MediaProbe | None = None) -> None:
        self.kind, self.probe = kind, probe
        super().__init__("output", "master integrity validation failed")


def check_master(path: Path, *, duration_sec: float, full_decode: bool = False) -> MediaProbe:
    probe = None
    try:
        probe = probe_media(path, timeout_seconds=30)
        result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_entries", "stream=codec_type,pix_fmt", "-of", "json", str(path)],
            capture_output=True, check=True, timeout=30)
        streams = json.loads(result.stdout)["streams"]
        if ("mp4" not in probe.format_name.split(",") or probe.video.codec != "h264"
                or (probe.video.width, probe.video.height, probe.video.fps) != (1080, 1920, 30)
                or probe.video.rotation % 360 != 0 or probe.audio is None or probe.audio.codec != "aac"
                or len(streams) != 2 or streams[0].get("pix_fmt") != "yuv420p"
                or not math.isfinite(probe.duration_sec) or probe.size_bytes <= 0
                or abs(probe.duration_sec - duration_sec) > 1/30 + 1024/probe.audio.sample_rate
                or not _faststart(path)):
            raise MasterCheckError("invalid", probe)
        if full_decode:
            decode = invoke_ffmpeg(["-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0",
                                    "-map", "0:a:0", "-f", "null", "-"], timeout_sec=120, check=False)
            if decode.returncode:
                raise MasterCheckError("invalid", probe)
        return probe
    except MasterCheckError:
        raise
    except ProbeError as exc:
        kind: Literal["invalid", "operational"] = "operational" if isinstance(exc.__cause__, OSError) else "invalid"
        raise MasterCheckError(kind, probe) from None
    except (OSError, subprocess.TimeoutExpired, EditingError, json.JSONDecodeError):
        raise MasterCheckError("operational", probe) from None
    except subprocess.CalledProcessError:
        raise MasterCheckError("invalid", probe) from None
    except (ValueError, KeyError, TypeError):
        raise MasterCheckError("operational", probe) from None


def encode_master(manifest: EditManifestV2, *, source_path: Path, music_path: Path | None,
                  ass_path: Path | None, output_path: Path, runtime: RenderRuntime) -> MediaProbe:
    del runtime  # Fixed encoding policy is fingerprinted by the caller.
    try:
        source = probe_media(source_path, timeout_seconds=30)
        if (source.audio is None or source.audio.codec != "aac" or source.video.codec != "h264"
                or source.video.rotation % 360 != 0
                or not math.isfinite(source.duration_sec)
                or abs(source.duration_sec - manifest.source.duration_sec) > .01):
            raise EditingError("source", "supported synchronized source required")
        args = ["-v", "error", "-i", str(source_path)]
        graph = None
        if manifest.music.enabled:
            if music_path is None:
                raise EditingError("music.asset", "local accepted music required")
            result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-show_entries", "format=duration", "-of", "json", str(music_path)],
                capture_output=True, check=True, timeout=30)
            duration = float(json.loads(result.stdout)["format"]["duration"])
            graph = audio_filter(manifest, music_duration_sec=duration)
            args.extend(["-i", str(music_path)])
        vf = video_filter(manifest, source)
        if ass_path is not None:
            if ass_path.parent != output_path.parent:
                raise EditingError("path", "ASS must belong to render staging")
            vf += ",ass=filename=overlay.ass:fontsdir=fonts"
        args.extend(["-vf", vf, "-map", "0:v:0"])
        if graph is not None:
            args.extend(["-filter_complex", graph, "-map", "[audio]", "-c:a", "aac",
                         "-b:a", "192k", "-ar", "48000", "-ac", "2"])
        else:
            args.extend(["-map", "0:a:0", "-c:a", "copy"])
        args.extend(["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                     "-movflags", "+faststart", "-map_metadata", "-1", "-t", str(source.duration_sec),
                     str(output_path)])
        run_ffmpeg(args, cwd=output_path.parent)
        return check_master(output_path, duration_sec=source.duration_sec, full_decode=True)
    except EditingError:
        raise
    except (OSError, ValueError, KeyError, TypeError, ProbeError, subprocess.SubprocessError):
        raise EditingError("output", "local master encode failed safely") from None
