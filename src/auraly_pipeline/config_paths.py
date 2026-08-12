from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PROJECT_ROOT = Path.home() / "Documents" / "Auraly"
WORK_ROOT_RELATIVE = Path("pipeline/work")


def configured_project_root(value: Path | None = None) -> Path:
    """Return the absolute trusted Auraly project root."""
    if value is not None:
        return value.expanduser().resolve()
    configured = os.environ.get("AURALY_PROJECT_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_PROJECT_ROOT.resolve()


def configured_work_root(
    value: Path | None = None,
    *,
    project_root: Path | None = None,
) -> Path:
    """Return the canonical artifact root or an explicit trusted override."""
    if value is not None:
        return value.expanduser().resolve()
    return (configured_project_root(project_root) / WORK_ROOT_RELATIVE).resolve()
