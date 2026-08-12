from __future__ import annotations

from pathlib import Path

import pytest

from auraly_pipeline.config_paths import configured_project_root, configured_work_root
from auraly_pipeline.image_generation import WORK_ROOT_RELATIVE
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.voices.handler import VoiceGenerateHandler
from auraly_pipeline.voices.service import VoiceMasterService


def test_default_project_and_work_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AURALY_PROJECT_ROOT", raising=False)
    assert configured_project_root() == (Path.home() / "Documents" / "Auraly").resolve()
    assert (
        configured_work_root()
        == (Path.home() / "Documents" / "Auraly" / "pipeline" / "work").resolve()
    )


def test_configured_and_windows_style_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = Path("C:/Auraly Workspace")
    monkeypatch.setenv("AURALY_PROJECT_ROOT", str(configured))
    assert configured_project_root() == configured.resolve()
    assert configured_work_root() == (configured / "pipeline" / "work").resolve()


def test_image_helpers_use_runtime_configured_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auraly_pipeline.image_generation import validate_job

    root = tmp_path / "runtime-project"
    expected = root / "pipeline" / "work" / "runtime-job"
    expected.mkdir(parents=True)
    monkeypatch.setenv("AURALY_PROJECT_ROOT", str(root))

    assert validate_job("runtime-job") == expected.resolve()


def test_voice_and_job_services_share_canonical_work_root_and_database_is_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    database = tmp_path / "metadata" / "auraly.db"
    monkeypatch.setenv("AURALY_PROJECT_ROOT", str(project_root))
    expected = (project_root / "pipeline" / "work").resolve()

    voice = VoiceMasterService.for_database(database)
    jobs = JobService.for_database(database)
    registered = jobs._handlers["voice.generate"]

    assert voice._work_root == expected
    assert isinstance(registered, VoiceGenerateHandler)
    assert registered._work_root == expected
    assert database.resolve().parent != expected
    assert WORK_ROOT_RELATIVE == Path("pipeline/work")
    voice.close()
    jobs.close()


def test_explicit_work_root_overrides_remain_supported(tmp_path: Path) -> None:
    override = tmp_path / "isolated-work"
    database = tmp_path / "auraly.db"
    voice = VoiceMasterService.for_database(database, work_root=override)
    jobs = JobService.for_database(database, work_root=override)
    registered = jobs._handlers["voice.generate"]

    assert voice._work_root == override.resolve()
    assert isinstance(registered, VoiceGenerateHandler)
    assert registered._work_root == override.resolve()
    voice.close()
    jobs.close()
