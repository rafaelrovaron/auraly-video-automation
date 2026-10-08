from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tests.api_helpers import create_ready_api_fixture, database_dump
from tests.test_api_http import client_for

pytest_plugins = ['tests.test_heygen_video_media']


def files(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def poster_url(campaign: str, render: str, sha: str) -> str:
    return f'/api/v1/campaigns/{campaign}/heygen/renders/{render}/poster/{sha}'


def test_ready_poster_is_png_without_writes(tmp_path: Path, mp4: bytes) -> None:
    settings, request = create_ready_api_fixture(tmp_path, mp4)
    before_files, before_sql = files(settings.work_root), database_dump(settings.database)
    with client_for(settings) as client:
        response = client.get(poster_url('campaign-one', request.render_id, hashlib.sha256(mp4).hexdigest()))
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['cache-control'] == 'no-store'
    assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert 0 < len(response.content) <= 4 * 1024 * 1024
    width, height = struct.unpack('>II', response.content[16:24])
    assert 0 < max(width, height) <= 720
    assert files(settings.work_root) == before_files
    assert database_dump(settings.database) == before_sql


def test_poster_rejects_wrong_campaign_hash_and_state(tmp_path: Path, mp4: bytes) -> None:
    settings, request = create_ready_api_fixture(tmp_path, mp4)
    sha = hashlib.sha256(mp4).hexdigest()
    with client_for(settings) as client:
        assert client.get(poster_url('campaign-other', request.render_id, sha)).status_code == 404
        assert client.get(poster_url('campaign-one', 'missing', sha)).status_code == 404
        assert client.get(poster_url('campaign-one', request.render_id, '0' * 64)).status_code == 409
        with sqlite3.connect(settings.database) as connection:
            connection.execute("UPDATE heygen_renders SET status='processing' WHERE id=?", (request.render_id,))
        assert client.get(poster_url('campaign-one', request.render_id, sha)).status_code == 409


@pytest.mark.parametrize('mutation', ['missing', 'changed', 'escape', 'link'])
def test_poster_rejects_missing_changed_or_linked_source(tmp_path: Path, mp4: bytes, mutation: str) -> None:
    settings, request = create_ready_api_fixture(tmp_path, mp4)
    sha = hashlib.sha256(mp4).hexdigest()
    with sqlite3.connect(settings.database) as connection:
        source = json.loads(connection.execute('SELECT source_json FROM heygen_renders WHERE id=?', (request.render_id,)).fetchone()[0])
        path = settings.work_root / source['path']
        if mutation == 'missing':
            path.unlink()
        elif mutation == 'changed':
            path.write_bytes(b'x' * len(mp4))
        elif mutation == 'escape':
            source['path'] = '../private.mp4'
            connection.execute('UPDATE heygen_renders SET source_json=? WHERE id=?', (json.dumps(source), request.render_id))
        else:
            target = tmp_path / 'outside.mp4'
            target.write_bytes(mp4)
            path.unlink()
            try:
                path.symlink_to(target)
            except OSError:
                pytest.skip('symlink creation unavailable on this host')
    with client_for(settings) as client:
        response = client.get(poster_url('campaign-one', request.render_id, sha))
    assert response.status_code == 409
    assert str(tmp_path) not in response.text


def test_poster_keeps_http_boundary(tmp_path: Path, mp4: bytes) -> None:
    settings, request = create_ready_api_fixture(tmp_path, mp4)
    url = poster_url('campaign-one', request.render_id, hashlib.sha256(mp4).hexdigest())
    with client_for(settings) as client:
        assert client.get(url + '?path=private').status_code == 422
        assert client.get(url, headers={'host': 'evil.example'}).status_code == 400


@pytest.mark.parametrize(('case', 'error'), [('exact', None), ('overflow', 'artifact_invalid'),
                                           ('invalid', 'artifact_invalid'), ('timeout', 'storage_unavailable'),
                                           ('missing', 'storage_unavailable')])
def test_decode_bounds_and_sanitizes_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            case: str, error: str | None) -> None:
    from auraly_pipeline.api import poster
    from auraly_pipeline.api.contracts import QueryError

    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        if case == 'missing':
            raise FileNotFoundError('private secret stderr')
        size = 4 * 1024 * 1024 + (case == 'overflow')
        code = ("import sys,struct;sys.stdout.buffer.write(b'\\x89PNG\\r\\n\\x1a\\n'+b'\\0'*8+"
                f"struct.pack('>II',32,64)+b'x'*({size}-24))")
        if case == 'timeout':
            code = 'import time;time.sleep(2)'
        elif case == 'invalid':
            code = "import sys;sys.stdout.buffer.write(b'not a png')"
        child = real_popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        children.append(child)
        return child

    monkeypatch.setattr(poster.subprocess, 'Popen', spawn)
    if case == 'timeout':
        monkeypatch.setattr(poster, 'DECODE_TIMEOUT_SEC', 0.05)
    if error is None:
        assert len(poster.decode_poster(tmp_path / 'private.mp4')) == 4 * 1024 * 1024
    else:
        with pytest.raises(QueryError) as caught:
            poster.decode_poster(tmp_path / 'private.mp4')
        assert caught.value.code == error
        assert 'private' not in str(caught.value)
    assert all(child.poll() is not None and child.stdout is not None and child.stdout.closed for child in children)
