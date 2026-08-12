from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.cli import app
from tests.test_campaign_domain import valid_campaign_data


runner = CliRunner()


def _database(tmp_path: Path) -> Path:
    database = tmp_path / "auraly.db"
    data = valid_campaign_data()
    data["campaignId"] = "voice-cli"
    data["budget"]["limitCents"] = 1000
    service = CampaignService.for_database(database)
    service.create_campaign(CampaignCreate.model_validate(data))
    service.close()
    return database


def test_voice_cli_generate_get_list_return_stable_json_without_secret(
    tmp_path: Path, monkeypatch
) -> None:
    database = _database(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "SENSITIVE-KEY-NEVER-OUTPUT")
    generated = runner.invoke(
        app,
        [
            "voice",
            "generate",
            "voice-cli",
            "--voice-id",
            "voice-explicit",
            "--model-id",
            "eleven_multilingual_v2",
            "--approve-paid-request",
            "--paid-request-approved-by",
            "rafael",
            "--approved-budget-cents",
            "1000",
            "--database",
            str(database),
        ],
    )
    assert generated.exit_code == 0
    payload = json.loads(generated.stdout)
    assert payload["voiceMaster"]["campaignId"] == "voice-cli"
    assert payload["job"]["retrySafety"] == "reconcile_before_retry"
    assert "SENSITIVE" not in generated.stdout
    voice_id = payload["voiceMaster"]["voiceMasterId"]

    got = runner.invoke(app, ["voice", "get", voice_id, "--database", str(database)])
    listed = runner.invoke(
        app, ["voice", "list", "--campaign-id", "voice-cli", "--database", str(database)]
    )
    assert got.exit_code == listed.exit_code == 0
    assert json.loads(got.stdout)["voiceMaster"]["voiceMasterId"] == voice_id
    assert json.loads(listed.stdout)["count"] == 1


def test_voice_cli_missing_api_key_only_fails_when_worker_executes(
    tmp_path: Path, monkeypatch
) -> None:
    database = _database(tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    generated = runner.invoke(
        app,
        [
            "voice",
            "generate",
            "voice-cli",
            "--voice-id",
            "voice-explicit",
            "--model-id",
            "eleven_multilingual_v2",
            "--approve-paid-request",
            "--paid-request-approved-by",
            "rafael",
            "--approved-budget-cents",
            "1000",
            "--database",
            str(database),
        ],
    )
    assert generated.exit_code == 0
    worked = runner.invoke(
        app,
        ["job", "worker-once", "--worker-id", "voice-worker", "--database", str(database)],
    )
    assert worked.exit_code == 0
    output = json.loads(worked.stdout)
    assert output["job"]["status"] == "failed"
    assert output["job"]["lastErrorCode"] == "provider_request_failed"
    assert "ELEVENLABS_API_KEY" not in worked.stdout


def test_voice_cli_approve_reject_errors_are_sanitized(tmp_path: Path) -> None:
    database = _database(tmp_path)
    missing = runner.invoke(
        app,
        ["voice", "get", "11111111-1111-4111-8111-111111111111", "--database", str(database)],
    )
    assert missing.exit_code == 1
    assert json.loads(missing.stdout) == {
        "success": False,
        "error": {"code": "voice_not_found", "message": "Voice Master not found."},
    }
