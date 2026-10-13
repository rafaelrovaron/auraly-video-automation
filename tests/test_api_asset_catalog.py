from __future__ import annotations

import hashlib
from pathlib import Path

from tests.api_helpers import create_api_fixture, database_dump
from tests.test_api_http import client_for


def test_catalog_lists_only_fixed_local_asset_folders_without_writes(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    for folder, name, data in (
        ('fonts', 'Brand.ttf', b'font'),
        ('music', 'Calm.wav', b'audio'),
        ('timing', 'Captions.json', b'{}'),
    ):
        target = tmp_path / 'assets' / folder / name
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
    (tmp_path / 'private.txt').write_text('secret', encoding='utf-8')
    before_sql = database_dump(settings.database)
    with client_for(settings) as client:
        response = client.get('/api/v1/editing/assets')
        assert client.get('/api/v1/editing/assets?path=private.txt').status_code == 422
    assert response.status_code == 200
    assert response.json() == {'items': [
        {'kind': 'font', 'name': 'Brand.ttf', 'path': 'assets/fonts/Brand.ttf', 'sha256': hashlib.sha256(b'font').hexdigest()},
        {'kind': 'music', 'name': 'Calm.wav', 'path': 'assets/music/Calm.wav', 'sha256': hashlib.sha256(b'audio').hexdigest()},
        {'kind': 'timing', 'name': 'Captions.json', 'path': 'assets/timing/Captions.json', 'sha256': hashlib.sha256(b'{}').hexdigest()},
    ]}
    assert (tmp_path / 'private.txt').read_text(encoding='utf-8') == 'secret'
    assert database_dump(settings.database) == before_sql


def test_catalog_ignores_links_and_wrong_file_types(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    folder = tmp_path / 'assets' / 'fonts'
    folder.mkdir(parents=True)
    (folder / 'note.txt').write_text('not a font', encoding='utf-8')
    nested = folder / 'nested'
    nested.mkdir()
    (nested / 'hidden.ttf').write_bytes(b'font')
    outside = tmp_path / 'secret.ttf'
    outside.write_bytes(b'secret')
    try:
        (folder / 'linked.ttf').symlink_to(outside)
    except OSError:
        pass  # The extension and nonrecursive checks still run on Windows without symlink privilege.
    with client_for(settings) as client:
        response = client.get('/api/v1/editing/assets')
    assert response.status_code == 200
    assert response.json() == {'items': []}
