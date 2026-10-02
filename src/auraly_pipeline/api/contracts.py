from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

from auraly_pipeline.campaigns.persistence import default_database_path
from auraly_pipeline.config_paths import DEFAULT_PROJECT_ROOT, WORK_ROOT_RELATIVE
from auraly_pipeline.editing.service import validate_editing_path

ErrorCode = Literal[
    "invalid_request", "not_found", "artifact_invalid", "storage_unavailable",
    "internal_error", "method_not_allowed",
]
ERROR_MESSAGES: dict[ErrorCode, str] = {
    "invalid_request": "Invalid request.",
    "not_found": "Resource not found.",
    "artifact_invalid": "Stored artifact is invalid.",
    "storage_unavailable": "Local storage is unavailable or incompatible.",
    "internal_error": "The local query failed safely.",
    "method_not_allowed": "This API is read-only.",
}


class QueryError(ValueError):
    def __init__(self, code: ErrorCode, field: str | None = None) -> None:
        self.code = code
        self.field = field if field in {
            "campaignId", "jobId", "profileId", "version", "videoId", "planHash",
        } else None
        super().__init__(ERROR_MESSAGES[code])


@dataclass(frozen=True)
class ApiSettings:
    project_root: Path
    work_root: Path
    database: Path

    def __post_init__(self) -> None:
        try:
            project = self.project_root.expanduser().absolute()
            work = self.work_root.expanduser().absolute()
            database = self.database.expanduser().absolute()
            validate_editing_path(project, project)
            validate_editing_path(project, work)
            validate_editing_path(database.parent, database)
            object.__setattr__(self, "project_root", project.resolve())
            object.__setattr__(self, "work_root", work.resolve())
            object.__setattr__(self, "database", database.resolve())
        except (ValueError, OSError):
            raise ValueError("Invalid local API configuration.") from None

    @classmethod
    def from_options(
        cls, *, project_root: Path | None = None, work_root: Path | None = None,
        database: Path | None = None,
    ) -> ApiSettings:
        configured = os.getenv("AURALY_PROJECT_ROOT", "").strip()
        project = project_root if project_root is not None else (
            Path(configured) if configured else DEFAULT_PROJECT_ROOT
        )
        return cls(project, work_root if work_root is not None else project / WORK_ROOT_RELATIVE,
                   database if database is not None else default_database_path())
