from __future__ import annotations

import json

from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.heygen.domain import (
    AssetPreparationPlan,
    AssetSource,
    HeyGenPreflight,
    RemoteAssetKind,
)


runner = CliRunner()
SOURCE = AssetSource(
    source_id="00000000-0000-4000-8000-000000000001",
    kind=RemoteAssetKind.IMAGE,
    local_path="campaigns/one/image.png",
    sha256="1" * 64,
    mime_type="image/png",
    size_bytes=10,
)
PLAN = AssetPreparationPlan(
    campaign_id="campaign-one",
    account_ref="account-fake",
    sources=[SOURCE],
    upload_sources=[SOURCE],
)


class FakeService:
    def __init__(self) -> None:
        self.closed = False

    def connect(self) -> HeyGenPreflight:
        return self.preflight()

    def disconnect(self) -> None:
        return None

    def connection_status(self) -> bool:
        return False

    def preflight(self) -> HeyGenPreflight:
        return HeyGenPreflight(
            connected=True,
            account_ref="account-fake",
            capabilities=["get_current_user"],
            max_batch_size=100,
        )

    def plan_assets(self, campaign_id: str) -> AssetPreparationPlan:
        assert campaign_id == "campaign-one"
        return PLAN

    def submit_assets(self, plan: AssetPreparationPlan) -> object:
        return type(
            "Submission",
            (),
            {
                "job": object(),
                "model_dump": lambda self, **kwargs: {
                    "plan": plan.model_dump(mode="json", by_alias=True),
                    "job": {"jobId": "job-1"},
                    "uploadCount": 1,
                },
            },
        )()

    def reconcile_upload(self, job_id: str) -> object:
        return type("JobResult", (), {"model_dump": lambda self, **kwargs: {"jobId": job_id, "status": "queued"}})()

    def close(self) -> None:
        self.closed = True


def test_heygen_status_is_stable_json(monkeypatch) -> None:
    service = FakeService()
    monkeypatch.setattr(
        "auraly_pipeline.cli.HeyGenService.for_database", lambda *args, **kwargs: service
    )

    result = runner.invoke(app, ["heygen", "status"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"success": True, "connected": False}
    assert service.closed


def test_heygen_connect_disconnect_preflight_and_reconcile(monkeypatch) -> None:
    monkeypatch.setattr(
        "auraly_pipeline.cli.HeyGenService.for_database", lambda *args, **kwargs: FakeService()
    )

    for command in (["connect"], ["disconnect"], ["preflight"], ["reconcile", "job-1"]):
        result = runner.invoke(app, ["heygen", *command])
        assert result.exit_code == 0, result.stdout
        assert json.loads(result.stdout)["success"] is True


def test_prepare_assets_can_cancel_or_submit_non_interactively(monkeypatch) -> None:
    monkeypatch.setattr(
        "auraly_pipeline.cli.HeyGenService.for_database", lambda *args, **kwargs: FakeService()
    )

    cancelled = runner.invoke(app, ["heygen", "prepare-assets", "campaign-one"], input="n\n")
    submitted = runner.invoke(
        app, ["heygen", "prepare-assets", "campaign-one", "--yes"]
    )

    assert cancelled.exit_code == 0
    assert json.loads(cancelled.stdout[cancelled.stdout.index("{") :])["submitted"] is False
    assert submitted.exit_code == 0
    assert json.loads(submitted.stdout)["submitted"] is True
