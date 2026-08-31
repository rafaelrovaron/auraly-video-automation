from __future__ import annotations

import hashlib
from pathlib import Path

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images.domain import ImageGenerateRequest
from auraly_pipeline.images.flow_handler import FlowImageGenerateHandler
from auraly_pipeline.images.service import ImageService
from auraly_pipeline.jobs.handlers import JobExecutionContext
from tests.test_campaign_domain import valid_campaign_data


def test_playwright_image_submission_creates_authorized_durable_flow_run(tmp_path: Path) -> None:
    """A Flow request must be persisted as an authorized two-slot operation."""
    database = tmp_path / "flow-images.db"
    work_root = tmp_path / "work"
    campaign_service = CampaignService.for_database(database)
    campaign = campaign_service.create_campaign(
        CampaignCreate.model_validate(valid_campaign_data())
    )
    campaign_service.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference-image")
    images = ImageService.for_database(database, work_root=work_root)

    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="authorized-flow-submission",
            prompt_snapshot="A moonlit studio",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="creative-operator",
        )
    )

    assert submission.generation.executor == "playwright_python"
    assert submission.generation.provider_state == "queued"
    images.close()


def test_invalid_flow_reference_is_terminal_before_runtime_construction(tmp_path: Path) -> None:
    """A tampered persistent reference never gets as far as the browser boundary."""
    database = tmp_path / "invalid-flow-reference.db"
    work_root = tmp_path / "work"
    campaign_service = CampaignService.for_database(database)
    campaign = campaign_service.create_campaign(
        CampaignCreate.model_validate(valid_campaign_data())
    )
    campaign_service.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference-image")
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="tampered-flow-reference",
            prompt_snapshot="A moonlit studio",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="creative-operator",
        )
    )
    reference.unlink()
    runtime_calls = 0

    def runtime_factory(*_args: object) -> object:
        nonlocal runtime_calls
        runtime_calls += 1
        raise AssertionError("invalid input must not construct a browser runtime")

    handler = FlowImageGenerateHandler(
        images._sessions,
        work_root=work_root,
        _runtime_factory=runtime_factory,  # type: ignore[arg-type]
    )
    result = handler.execute(
        JobExecutionContext(
            job_id=submission.job.job_id,
            job_type="image.generate",
            campaign_id=campaign.campaign_id,
            input=submission.job.input,
            attempt_number=1,
        )
    )

    assert result.outcome == "terminal_failure"
    assert result.error_code == "image_job_integrity_failed"
    assert runtime_calls == 0
    images.close()
