from __future__ import annotations

from pathlib import Path
import struct
import subprocess
from threading import Event, Timer

from auraly_pipeline.api.contracts import QueryError

MAX_POSTER_BYTES = 4 * 1024 * 1024
DECODE_TIMEOUT_SEC = 10


def decode_poster(path: Path) -> bytes:
    try:
        process = subprocess.Popen(
            ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
             '-i', str(path), '-map', '0:v:0', '-frames:v', '1', '-vf',
             "scale=w='min(720,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease",
             '-threads', '1', '-c:v', 'png', '-f', 'image2pipe', 'pipe:1'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise QueryError('storage_unavailable') from None
    timed_out = Event()

    def expire() -> None:
        timed_out.set()
        if process.poll() is None:
            process.kill()

    timer = Timer(DECODE_TIMEOUT_SEC, expire)
    timer.daemon = True
    timer.start()
    try:
        assert process.stdout is not None
        data = process.stdout.read(MAX_POSTER_BYTES + 1)
        if len(data) > MAX_POSTER_BYTES:
            raise QueryError('artifact_invalid')
        code = process.wait()
        if timed_out.is_set():
            raise QueryError('storage_unavailable')
        if code != 0 or len(data) < 24 or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise QueryError('artifact_invalid')
        width, height = struct.unpack('>II', data[16:24])
        if not (0 < width <= 720 and 0 < height <= 720):
            raise QueryError('artifact_invalid')
        return data
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
