from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig
from auraly_pipeline.heygen.video_service import HeyGenVideoService
from tests.heygen_video_support import ready_video_campaign

pytest_plugins = ["tests.test_heygen_video_media"]


def test_cli_three_video_flow(tmp_path: Path, mp4: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeHeyGenProvider()
    db = tmp_path / "test.db"
    root = tmp_path / "work"
    ready_video_campaign(db, root, provider)
    original = HeyGenVideoService.for_database

    def factory(database_path: Path, **kwargs: Any) -> HeyGenVideoService:
        return original(
            database_path,
            **kwargs,
            provider=provider,
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, content=mp4))
            ),
        )

    monkeypatch.setattr(HeyGenVideoService, "for_database", factory)
    config = tmp_path / "config.json"
    config.write_text(HeyGenVideoConfig(resolution="720p").model_dump_json())
    runner = CliRunner()
    common = ["campaign-one", "--database", str(db), "--work-root", str(root)]
    options = ["--config", str(config), "--max-paid-renders", "3"]
    planned = runner.invoke(app, ["heygen", "plan-videos", *common, *options])
    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.stdout)["plan"]["new_count"] == 3
    canceled = runner.invoke(
        app,
        ["heygen", "generate-videos", *common, *options, "--approved-by", "tester"],
        input="n\n",
    )
    assert canceled.exit_code != 0
    assert provider.events.count("create_video") == 0
    submitted = runner.invoke(
        app, ["heygen", "generate-videos", *common, *options, "--approved-by", "tester", "--yes"]
    )
    assert submitted.exit_code == 0, submitted.output
    result = runner.invoke(app, ["heygen", "run-videos", *common])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["summary"]["ready_count"] == 3
    assert "https://" not in result.stdout + result.stderr
    missing_budget = runner.invoke(
        app,
        [
            "heygen",
            "generate-videos",
            *common,
            "--config",
            str(config),
            "--approved-by",
            "tester",
            "--yes",
        ],
    )
    assert missing_budget.exit_code != 0
    assert provider.events.count("create_video") == 3
