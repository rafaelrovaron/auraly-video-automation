from __future__ import annotations

import re
import unicodedata
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a project identifier cannot be mapped to a safe directory."""


def slugify_reel_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    if not slug:
        raise UnsafePathError("reel ID must contain at least one letter or number")
    return slug


def create_new_workdir(work_root: Path, reel_id: str) -> Path:
    work_root.mkdir(parents=True, exist_ok=True)
    reel_dir = work_root / slugify_reel_id(reel_id)
    try:
        reel_dir.mkdir()
    except FileExistsError as exc:
        raise UnsafePathError(f"reel workspace already exists: {reel_dir}") from exc
    return reel_dir
