from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright
import pytest
from sqlalchemy import select, text

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.images.domain import ImageGenerateRequest
from auraly_pipeline.images.flow_handler import (
    FlowCheckpointConflictError,
    FlowGenerationCheckpointSink,
    FlowImageGenerateHandler,
)
from auraly_pipeline.images.service import ImageService
from auraly_pipeline.jobs.handlers import JobExecutionContext
from tests.test_campaign_domain import valid_campaign_data
from tests.test_flow_generation import LOCAL_TARGET, _fixture_url
from auraly_pipeline.flow.config import FlowGenerationConfig
from auraly_pipeline.flow.generation import FlowGenerationRuntime
from auraly_pipeline.images.db_models import FlowGenerationRunRow
from auraly_pipeline.images.db_models import FlowCandidateSlotRow, ImageGenerationRow
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow


def test_run_checkpoint_conflict_uses_typed_flow_error(tmp_path: Path) -> None:
    """A stale run checkpoint must reach the Flow-safe conflict boundary."""
    database = tmp_path / "checkpoint-conflict.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference")
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="checkpoint-conflict",
            prompt_snapshot="prompt",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="operator",
            provider_workspace_path=workspace_path,
            provider_workspace_fingerprint=hashlib.sha256(workspace_path.encode()).hexdigest(),
        )
    )
    with images._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        assert run is not None
        run.stage = "blocked"
        session.commit()
    sink = FlowGenerationCheckpointSink(
        images._sessions, run_id=run.id, clock=lambda: run.created_at
    )

    with pytest.raises(FlowCheckpointConflictError):
        sink.record_inputs_verified(
            type("Observation", (), {"reference_verified": True, "prompt_verified": True})()
        )
    images.close()


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
            provider_workspace_path="fx/tools/flow/local-workspace",
            provider_workspace_fingerprint=hashlib.sha256(
                b"fx/tools/flow/local-workspace"
            ).hexdigest(),
        )
    )

    assert submission.generation.executor == "playwright_python"
    assert submission.generation.provider_state == "queued"
    assert submission.job.retry_safety == "reconcile_before_retry"
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
            provider_workspace_path="fx/tools/flow/local-workspace",
            provider_workspace_fingerprint=hashlib.sha256(
                b"fx/tools/flow/local-workspace"
            ).hexdigest(),
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


@pytest.mark.parametrize(
    "corruption",
    [
        "job_fingerprint",
        "scene_ownership",
        "executor_policy",
        "authorization_event",
        "workspace_and_reference_sha",
        "run_contract",
        "missing_slot",
        "extra_slot",
        "completed_missing_artifact",
    ],
)
def test_flow_integrity_matrix_rejects_before_runtime_construction(
    tmp_path: Path, corruption: str
) -> None:
    """Every persisted authorization/integrity break stops before the browser seam."""
    database = tmp_path / f"integrity-{corruption}.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    other_campaign_id: str | None = None
    if corruption == "scene_ownership":
        other_data = valid_campaign_data()
        other_data["campaignId"] = "other-campaign"
        other_campaign_id = campaigns.create_campaign(
            CampaignCreate.model_validate(other_data)
        ).campaign_id
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference-image")
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key=f"integrity-{corruption.replace('authorization', 'auth')}",
            prompt_snapshot="A moonlit studio",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="creative-operator",
            provider_workspace_path=workspace_path,
            provider_workspace_fingerprint=hashlib.sha256(workspace_path.encode()).hexdigest(),
        )
    )
    with images._sessions() as session:
        job = session.get(JobRow, submission.job.job_id)
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        slots = list(
            session.scalars(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == run.id
                )
            )
        )
        assert job is not None and run is not None and len(slots) == 2
        if corruption == "job_fingerprint":
            job.request_fingerprint = "0" * 64
        elif corruption == "scene_ownership":
            assert other_campaign_id is not None
        elif corruption == "executor_policy":
            job.retry_safety = "idempotent"
        elif corruption == "authorization_event":
            authorization = session.scalar(
                select(JobEventRow).where(
                    JobEventRow.job_id == job.id,
                    JobEventRow.event_type == "job.authorized",
                )
            )
            assert authorization is not None
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=job.id,
                    event_type="job.authorized",
                    timestamp=authorization.timestamp,
                    metadata_json={"executor": "local_fake"},
                )
            )
        elif corruption == "workspace_and_reference_sha":
            run.provider_workspace_path = "../outside"
            reference.write_bytes(b"tampered-reference")
        elif corruption == "run_contract":
            session.execute(text("PRAGMA ignore_check_constraints = ON"))
            run.required_resolution = "1K"
        elif corruption == "missing_slot":
            session.delete(slots[1])
        elif corruption == "extra_slot":
            session.execute(text("PRAGMA ignore_check_constraints = ON"))
            session.add(
                FlowCandidateSlotRow(
                    id=str(uuid4()),
                    flow_generation_run_id=run.id,
                    slot_index=2,
                    provider_slot_fingerprint=None,
                    state="pending",
                    download_intent_at=None,
                    staging_path=None,
                    staged_sha256=None,
                    image_candidate_id=None,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
            )
        else:
            session.execute(text("PRAGMA ignore_check_constraints = ON"))
            run.stage = "completed"
            generation = session.get(ImageGenerationRow, submission.generation.image_generation_id)
            assert generation is not None
            generation.provider_state = "completed"
            for slot in slots:
                slot.state = "ingested"
        session.commit()
    calls = 0

    def factory(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("integrity rejection must precede browser construction")

    result = FlowImageGenerateHandler(
        images._sessions, work_root=work_root, _runtime_factory=factory
    ).execute(
        JobExecutionContext(
            job_id=submission.job.job_id,
            job_type="image.generate",
            campaign_id=(
                other_campaign_id if corruption == "scene_ownership" else campaign.campaign_id
            ),
            input=submission.job.input,
            attempt_number=1,
        )
    )
    assert result.outcome == "terminal_failure"
    assert result.error_code == "image_job_integrity_failed"
    assert calls == 0
    images.close()


def test_local_playwright_flow_job_ingests_two_2k_candidates(tmp_path: Path) -> None:
    """The durable Flow handler observes and downloads only the two bound local slots."""
    database = tmp_path / "local-flow.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference-image")
    workspace_path = "fx/tools/flow/local-workspace"
    workspace_hash = hashlib.sha256(workspace_path.encode()).hexdigest()
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="local-flow-job",
            prompt_snapshot="A moonlit studio",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="creative-operator",
            provider_workspace_path=workspace_path,
            provider_workspace_fingerprint=workspace_hash,
        )
    )
    with images._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        assert run is not None
        now = run.created_at
        run.stage = "dispatch_confirmed"
        run.dispatch_intent_at = now
        run.dispatch_confirmed_at = now
        session.commit()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        try:

            def runtime_factory(context: object) -> FlowGenerationRuntime:
                page.goto(_fixture_url("grid-two.html"))

                class LocalSession:
                    @property
                    def page(self):
                        return page

                    def require_current_flow_page(self) -> None:
                        assert LOCAL_TARGET.allows_url(page.url)

                    def workspace_identity(self):
                        from auraly_pipeline.flow.generation_domain import FlowWorkspaceIdentity

                        return FlowWorkspaceIdentity(
                            workspace_path=workspace_path,
                            fingerprint=workspace_hash,
                        )

                @contextmanager
                def session_factory():
                    yield LocalSession()

                return FlowGenerationRuntime(
                    FlowGenerationConfig(generation_timeout_seconds=1, download_timeout_seconds=1),
                    _session_factory=session_factory,
                    _locator_target=LOCAL_TARGET,
                    artifact_context=context,  # type: ignore[arg-type]
                )

            handler = FlowImageGenerateHandler(
                images._sessions,
                work_root=work_root,
                _runtime_factory=runtime_factory,
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
        finally:
            browser.close()
    assert result.outcome == "success", result
    assert result.result["candidateCount"] == 2
    candidates = images.list_candidates(submission.generation.image_generation_id)
    assert [candidate.candidate_index for candidate in candidates] == [0, 1]
    assert all(max(candidate.width, candidate.height) >= 2048 for candidate in candidates)
    images.close()


def test_download_intent_slot_is_rejected_before_runtime_construction(tmp_path: Path) -> None:
    """An ambiguous post-intent slot must not re-enter the browser runtime."""
    # Reuse the persisted Flow request shape, then advance only its unsafe slot checkpoint.
    database = tmp_path / "intent-slot.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"reference")
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="intent-slot",
            prompt_snapshot="prompt",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="operator",
            provider_workspace_path=workspace_path,
            provider_workspace_fingerprint=hashlib.sha256(workspace_path.encode()).hexdigest(),
        )
    )
    with images._sessions() as session:
        slot = session.scalar(
            select(FlowCandidateSlotRow).where(FlowCandidateSlotRow.slot_index == 0)
        )
        assert slot is not None
        slot.provider_slot_fingerprint = "a" * 64
        slot.download_intent_at = slot.created_at
        slot.state = "download_intent_recorded"
        session.commit()
    calls = 0

    def factory(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("must not construct runtime")

    result = FlowImageGenerateHandler(
        images._sessions, work_root=work_root, _runtime_factory=factory
    ).execute(
        JobExecutionContext(
            job_id=submission.job.job_id,
            job_type="image.generate",
            campaign_id=campaign.campaign_id,
            input=submission.job.input,
            attempt_number=1,
        )
    )
    assert result.error_code == "image_job_integrity_failed"
    assert calls == 0
    images.close()
