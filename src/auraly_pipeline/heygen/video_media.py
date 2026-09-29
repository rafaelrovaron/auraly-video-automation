from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from urllib.parse import urlparse

import httpx

from auraly_pipeline.heygen.video_domain import HeyGenRender, VideoSource
from auraly_pipeline.probe import probe_media

MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


def _paths(render: HeyGenRender, work_root: Path) -> tuple[Path, Path]:
    root=work_root.absolute()
    final=root/'campaigns'/render.item.campaign_id/'heygen'/render.item.scene_variant_id/render.logical_key/'source.mp4'
    current=final
    while True:
        if current.exists() or current.is_symlink():
            facts=current.lstat()
            if current.is_symlink() or getattr(facts,'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('unsafe video path')
        if current==current.parent:
            break
        current=current.parent
    resolved=root.resolve()
    if not final.resolve().is_relative_to(resolved):
        raise ValueError('unsafe video containment')
    return resolved,final


def _qc(path: Path, render: HeyGenRender, root: Path, final: Path) -> VideoSource:
    try:
        size=path.stat().st_size
        if size<=0 or size>MAX_DOWNLOAD_BYTES:
            raise ValueError('invalid video size')
        probe=probe_media(path,timeout_seconds=30)
        expected=(1080,1920) if render.item.config.resolution=='1080p' else (720,1280)
        if ('mp4' not in probe.format_name.split(',') or probe.video.codec!='h264'
            or probe.audio is None or probe.audio.codec!='aac'
            or (probe.video.width,probe.video.height)!=expected or probe.video.rotation%360!=0
            or abs(probe.duration_sec-render.item.duration_seconds)>max(2,0.05*render.item.duration_seconds)):
            raise ValueError('video QC rejected')
        subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(path),'-map','0:v:0','-map','0:a:0','-f','null','-'],capture_output=True,check=True,timeout=120)
        with path.open('rb') as stream:
            sha=hashlib.file_digest(stream,'sha256').hexdigest()
        return VideoSource(path=final.relative_to(root).as_posix(),sha256=sha,size_bytes=size,probe=probe)
    except Exception:
        raise ValueError('video download or QC failed safely') from None


def _manifest(render: HeyGenRender, source: VideoSource) -> dict[str, object]:
    return {'render_id':render.render_id,'logical_key':render.logical_key,'source':source.model_dump(mode='json',exclude_computed_fields=True)}


def _publish_json(path: Path, payload: dict[str, object]) -> None:
    if path.is_symlink():
        raise ValueError('unsafe video manifest path')
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=payload:
            raise ValueError('video manifest conflict')
        return
    staging=path.with_suffix('.json.part')
    if staging.is_symlink() or (staging.exists() and getattr(staging.lstat(),'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError('unsafe video staging path')
    with staging.open('w',encoding='utf-8') as stream:
        json.dump(payload,stream,sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(staging,path)
    staging.unlink()


def recover_source(render: HeyGenRender, *, work_root: Path) -> VideoSource | None:
    root,final=_paths(render,work_root)
    if not final.exists():
        return None
    source=_qc(final,render,root,final)
    if render.source is not None and (render.source.sha256!=source.sha256 or render.source.size_bytes!=source.size_bytes):
        raise ValueError('video source conflict')
    facts=final.with_name('publication.json')
    if not facts.is_file() or facts.is_symlink() or json.loads(facts.read_text(encoding='utf-8'))!=_manifest(render,source):
        raise ValueError('video publication identity conflict')
    _publish_json(final.with_name('source.json'),_manifest(render,source))
    return source


def download_source(render: HeyGenRender, url: str, *, work_root: Path, client: httpx.Client) -> VideoSource:
    recovered=recover_source(render,work_root=work_root)
    if recovered is not None:
        return recovered
    root,final=_paths(render,work_root)
    final.parent.mkdir(parents=True,exist_ok=True)
    part=final.with_suffix('.mp4.part')
    _paths(render,work_root)
    if part.is_symlink() or (part.exists() and getattr(part.lstat(),'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError('unsafe video staging path')
    try:
        for _ in range(6):
            parsed=urlparse(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('HTTPS video download required')
            with client.stream('GET',url,follow_redirects=False,timeout=httpx.Timeout(60,connect=10)) as response:
                if response.is_redirect:
                    url=str(response.url.join(response.headers['location']))
                    continue
                response.raise_for_status()
                if int(response.headers.get('content-length','0'))>MAX_DOWNLOAD_BYTES:
                    raise ValueError('video size limit')
                size=0
                with part.open('wb') as stream:
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>MAX_DOWNLOAD_BYTES:
                            raise ValueError('video size limit')
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                break
        else:
            raise ValueError('video redirect limit')
        source=_qc(part,render,root,final)
        _publish_json(final.with_name('publication.json'),_manifest(render,source))
        _paths(render,work_root)
        os.link(part,final)
        part.unlink()
        _publish_json(final.with_name('source.json'),_manifest(render,source))
        return source
    except Exception:
        raise ValueError('video download or publication failed safely') from None
