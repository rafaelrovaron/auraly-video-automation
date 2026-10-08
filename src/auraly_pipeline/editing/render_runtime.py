from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_domain import RenderRuntime
from auraly_pipeline.editing.resolver import content_hash


def invoke_ffmpeg(args: list[str], *, cwd: Path | None = None,
                  timeout_sec: float = 600, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Private diagnostics stay in memory; public errors never include tool output."""
    local: list[str] = []
    for arg in args:
        if arg == "-i":
            local.extend(["-protocol_whitelist", "file,pipe"])
        local.append(arg)
    try:
        environment = os.environ.copy()
        if cwd is not None and (cwd / "fontconfig.xml").is_file():
            environment["FONTCONFIG_FILE"] = str(cwd / "fontconfig.xml")
        result = subprocess.run(["ffmpeg", "-nostdin", "-n", *local], cwd=cwd, env=environment,
                                capture_output=True, timeout=timeout_sec, check=False)
    except (OSError, subprocess.SubprocessError):
        raise EditingError("runtime", "local FFmpeg unavailable or timed out") from None
    if check and result.returncode:
        raise EditingError("runtime", "local FFmpeg operation failed") from None
    return result


def run_ffmpeg(args: list[str], *, cwd: Path | None = None, timeout_sec: float = 600) -> bytes:
    return invoke_ffmpeg(args, cwd=cwd, timeout_sec=timeout_sec).stdout


def detect_runtime() -> RenderRuntime:
    version = run_ffmpeg(["-version"], timeout_sec=30).decode("utf-8", errors="replace")
    filters = run_ffmpeg(["-filters"], timeout_sec=30).decode("utf-8", errors="replace")
    encoders = run_ffmpeg(["-encoders"], timeout_sec=30).decode("utf-8", errors="replace")
    for name in ("ass", "scale", "crop", "pad", "zoompan", "amix", "alimiter", "atrim", "afade"):
        if not re.search(r"\s" + name + r"\s", filters):
            raise EditingError("runtime", "required FFmpeg filter unavailable")
    for name in ("libx264", "aac"):
        if not re.search(r"\s" + name + r"\s", encoders):
            raise EditingError("runtime", "required FFmpeg encoder unavailable")
    # libass reports its version before rejecting the intentionally absent script.
    probe = invoke_ffmpeg(["-v", "info", "-f", "lavfi", "-i", "color=s=16x16:d=0.01",
                           "-vf", "ass=filename=", "-f", "null", "-"],
                          timeout_sec=30, check=False)
    log = probe.stderr.decode("utf-8", errors="replace")
    match = re.search(r"libass API version:\s*([^\r\n]+)", log)
    if match is None:
        raise EditingError("runtime", "libass runtime version cannot be verified")
    encoding = {"videoCodec": "libx264", "crf": "18", "preset": "medium",
                "audioCodec": "aac", "audioBitrate": "192k", "sampleRate": "48000",
                "channels": "2", "pixelFormat": "yuv420p", "fps": "30"}
    libass = match.group(1).strip()
    fingerprint = content_hash({"ffmpegBuild": version, "libassVersion": libass,
                                "encoding": encoding})
    return RenderRuntime(fingerprint=fingerprint, ffmpeg_version=version.splitlines()[0],
                         libass_version=libass, encoding=encoding)
