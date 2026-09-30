from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.handlers import SuccessHandler
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.voices import handler
from tests.test_voice_external_import import Transcript, setup_import


@pytest.mark.parametrize("color", [False, True])
def test_cli_import_is_lazy_and_worker_is_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, color: bool
) -> None:
    service, voices, source, request = setup_import(tmp_path)
    service.close()
    voices.close()
    if color:
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setattr(handler.FasterWhisperTranscriber, "transcribe", Transcript(None).transcribe)
    database = tmp_path / "test.db"
    jobs = JobService.for_database(database, handlers={"fake.success": SuccessHandler()})
    unrelated = jobs.submit_job(
        JobSubmit(
            job_type="fake.success",
            campaign_id=request.campaign_id,
            idempotency_key="unrelated",
            input={},
        )
    )
    runner = CliRunner()
    help_result = runner.invoke(app, ["voice", "import", "--help"])
    assert help_result.exit_code == 0
    imported = runner.invoke(
        app,
        [
            "voice",
            "import",
            request.campaign_id,
            "--source",
            str(source),
            "--database",
            str(database),
            "--project-root",
            str(source.parent),
            "--work-root",
            str(source.parent / "work"),
        ],
    )
    assert imported.exit_code == 0, imported.stdout
    payload = json.loads(imported.stdout)
    assert payload["voiceMaster"]["provider"] == "imported"
    assert payload["voiceMaster"]["status"] == "pending"
    worked = runner.invoke(
        app,
        [
            "voice",
            "run-import",
            request.campaign_id,
            "--worker-id",
            "cli-test",
            "--database",
            str(database),
            "--work-root",
            str(source.parent / "work"),
        ],
    )
    assert worked.exit_code == 0, worked.stdout
    assert json.loads(worked.stdout)["job"]["lastErrorCode"] == "voice_import_failed"
    assert "private-runtime-path" not in worked.stdout
    assert jobs.get_job(unrelated.job_id).status == "queued"
    jobs.close()


def test_default_worker_registers_external_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, voices, source, request = setup_import(tmp_path)
    submitted = service.import_audio(request, source=source)
    monkeypatch.setattr(handler.FasterWhisperTranscriber, "transcribe", Transcript(None).transcribe)
    jobs = JobService.for_database(tmp_path / "test.db", work_root=source.parent / "work")
    try:
        job = jobs.worker_once(
            "default-test", campaign_id=request.campaign_id, job_type="voice.import"
        )
        assert job is not None and job.job_id == submitted.job.job_id
        assert job.last_error_code == "voice_import_failed"
    finally:
        jobs.close()
        service.close()
        voices.close()


def test_cli_external_import_errors_are_sanitized(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "voice",
            "import",
            "campaign",
            "--source",
            str(tmp_path / "private.wav"),
            "--database",
            str(tmp_path / "test.db"),
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "voice_import_failed"
    assert str(tmp_path) not in result.stdout
