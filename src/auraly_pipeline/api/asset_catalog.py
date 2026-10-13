from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from auraly_pipeline.editing.domain import AssetRef, EditingError
from auraly_pipeline.editing.service import validate_editing_path
from auraly_pipeline.models import ContractModel


AssetKind = Literal['font', 'music', 'timing']


class LocalAsset(ContractModel):
    kind: AssetKind
    name: str
    path: str
    sha256: str


_FOLDERS: tuple[tuple[AssetKind, str, frozenset[str]], ...] = (
    ('font', 'fonts', frozenset({'.ttf', '.otf'})),
    ('music', 'music', frozenset({'.mp3', '.wav', '.m4a', '.aac'})),
    ('timing', 'timing', frozenset({'.json'})),
)
_MAX_BYTES = 100 * 1024 * 1024


def list_local_assets(project_root: Path) -> list[LocalAsset]:
    """Expose only files in fixed opt-in asset folders, never arbitrary project paths."""
    items: list[LocalAsset] = []
    for kind, folder, suffixes in _FOLDERS:
        directory = project_root / 'assets' / folder
        try:
            validate_editing_path(project_root, directory)
            if not directory.is_dir():
                continue
            candidates = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
        except (EditingError, OSError):
            continue
        for candidate in candidates:
            try:
                validate_editing_path(project_root, candidate)
                if candidate.suffix.lower() not in suffixes or not candidate.is_file() or candidate.stat().st_size > _MAX_BYTES:
                    continue
                path = candidate.relative_to(project_root).as_posix()
                with candidate.open('rb') as stream:
                    sha = hashlib.file_digest(stream, 'sha256').hexdigest()
                ref = AssetRef(path=path, sha256=sha)
                items.append(LocalAsset(kind=kind, name=candidate.name, path=ref.path, sha256=ref.sha256))
            except (EditingError, OSError, ValueError):
                continue
    return items
