from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
from uuid import uuid4

import httpx
import pytest

from auraly_pipeline.heygen.video_domain import HeyGenRender, HeyGenRenderStatus, HeyGenVideoConfig, material_config_sha256, video_logical_key
from auraly_pipeline.heygen.video_media import download_source, recover_source
from tests.test_heygen_video_domain import video_item


def render() -> HeyGenRender:
    item=video_item(config=HeyGenVideoConfig(resolution='720p'))
    return HeyGenRender(render_id=str(uuid4()),item=item,logical_key=video_logical_key(item),config_sha256=material_config_sha256(item.config),job_id=str(uuid4()),status=HeyGenRenderStatus.DOWNLOAD_PENDING,max_paid_renders=3,approved_by='tester',remote_video_id='video-one',created_at=datetime.now(UTC),updated_at=datetime.now(UTC))


@pytest.fixture
def mp4(tmp_path: Path) -> bytes:
    file=tmp_path/'fixture.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=720x1280:r=5:d=1','-f','lavfi','-i','anullsrc=r=44100:cl=mono','-t','1','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',str(file)],check=True,capture_output=True,timeout=30)
    return file.read_bytes()


def test_publication_crash_recovery(tmp_path: Path, mp4: bytes) -> None:
    root=tmp_path/'work'
    root.mkdir()
    item=render()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=mp4))) as client:
        source=download_source(item,'https://signed.example/a?private=secret',work_root=root,client=client)
    assert source.probe.video.width==720 and source.probe.video.height==1280
    assert 'secret' not in (root/source.path).with_name('source.json').read_text()
    (root/source.path).with_name('source.json').unlink()
    recovered=recover_source(item,work_root=root)
    assert recovered is not None and recovered.sha256==source.sha256
    with httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('must not redownload'))) as client:
        assert download_source(item,'https://expired.example/a',work_root=root,client=client).sha256==source.sha256


@pytest.mark.parametrize('body',[b'',b'not-a-video'])
def test_qc_rejects_invalid_video(tmp_path: Path, body: bytes) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=body))) as client:
        with pytest.raises(ValueError):
            download_source(render(),'https://signed.example/a',work_root=tmp_path,client=client)


def test_https_stream_limits(tmp_path: Path) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://unsafe.example/a'}))) as client:
        with pytest.raises(ValueError):
            download_source(render(),'https://signed.example/a',work_root=tmp_path,client=client)


def test_final_conflict_and_path_escape(tmp_path: Path, mp4: bytes) -> None:
    item=render()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=mp4))) as client:
        source=download_source(item,'https://signed.example/a',work_root=tmp_path,client=client)
        (tmp_path/source.path).write_bytes(b'conflict')
        with pytest.raises(ValueError):
            download_source(item,'https://signed.example/a',work_root=tmp_path,client=client)
        assert (tmp_path/source.path).read_bytes()==b'conflict'
