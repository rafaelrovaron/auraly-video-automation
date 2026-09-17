from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from playwright.sync_api import sync_playwright
import pytest
from PIL import Image
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError

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
from auraly_pipeline.flow.domain import (
    FlowAuthenticationTimeoutError,
    FlowDiagnosticSanitizationError,
    FlowRuntimeBusyError,
)
from auraly_pipeline.flow.artifacts import inspect_flow_artifact, resolve_flow_final_path
from auraly_pipeline.flow.generation import FlowGenerationRuntime
from auraly_pipeline.flow.generation_domain import (
    FlowCandidateBaselineFailure,
    FlowGenerationObservation,
    FlowGenerationRuntimeError,
    FlowGenerationUiContractError,
)
from auraly_pipeline.images.db_models import ImageCandidateRow, FlowGenerationRunRow
from auraly_pipeline.images.db_models import FlowCandidateSlotRow, ImageGenerationRow
from auraly_pipeline.images.domain import ImageCandidate
from auraly_pipeline.images import domain as image_domain
from auraly_pipeline.images.repository import ImageRepository
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow


def _write_reference(path: Path) -> None:
    Image.new("RGB", (4, 4), color=(16, 32, 64)).save(path, format="PNG")


def test_run_checkpoint_conflict_uses_typed_flow_error(tmp_path: Path) -> None:
    """A stale run checkpoint must reach the Flow-safe conflict boundary."""
    database = tmp_path / "checkpoint-conflict.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    _write_reference(reference)
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
    _write_reference(reference)
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
    with images._sessions() as session:
        events = list(
            session.scalars(
                select(JobEventRow).where(
                    JobEventRow.job_id == submission.job.job_id,
                    JobEventRow.event_type == "job.provider_action_authorized",
                )
            )
        )
        assert len(events) == 1
        assert events[0].metadata_json == {
            "approvedBy": "creative-operator",
            "candidateCount": 2,
            "executor": "playwright_python",
            "resolution": "2K",
        }
    images.close()


@pytest.mark.parametrize("corruption", ("missing", "invalid_format"))
def test_invalid_flow_reference_is_terminal_before_runtime_construction(
    tmp_path: Path,
    corruption: str,
) -> None:
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
    if corruption == "invalid_format":
        reference.write_bytes(b"not an image")
    else:
        _write_reference(reference)
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
    if corruption == "missing":
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
    ("runtime_error", "expected_code", "expected_result"),
    (
        (FlowRuntimeBusyError(), "flow_runtime_busy", {}),
        (FlowAuthenticationTimeoutError(), "flow_authentication_required", {}),
        (FlowDiagnosticSanitizationError(), "flow_diagnostic_sanitization_failed", {}),
        (
            FlowGenerationRuntimeError(failed_step="upload_reference"),
            "flow_input_verification_failed",
            {},
        ),
        (
            FlowGenerationRuntimeError(failed_step="close_browser"),
            "flow_browser_close_failed",
            {},
        ),
        (
            FlowGenerationUiContractError(
                failed_locator="CANDIDATE_SLOT",
                candidate_baseline_failure=FlowCandidateBaselineFailure(
                    category="candidate_identity_invalid",
                    grid_count=1,
                    listitem_count="2+",
                    visible_candidate_count="2+",
                    admissible_candidate_count=1,
                    blocker_present=False,
                    loading_state_present=False,
                    duplicate_fingerprint_detected=False,
                    hidden_candidate_detected=False,
                    invalid_identity_detected=True,
                    incomplete_candidate_detected=False,
                    malformed_grid_detected=False,
                ),
            ),
            "flow_candidate_grid_ambiguous",
            {
                "candidateBaselineFailure": {
                    "phase": "candidate_baseline",
                    "category": "candidate_identity_invalid",
                    "gridCount": 1,
                    "listitemCount": "2+",
                    "visibleCandidateCount": "2+",
                    "admissibleCandidateCount": 1,
                    "blockerPresent": False,
                    "loadingStatePresent": False,
                    "duplicateFingerprintDetected": False,
                    "hiddenCandidateDetected": False,
                    "invalidIdentityDetected": True,
                    "incompleteCandidateDetected": False,
                    "malformedGridDetected": False,
                }
            },
        ),
    ),
)
def test_flow_handler_upload_request_keeps_exact_bytes_and_maps_runtime_failures(
    tmp_path: Path,
    runtime_error: BaseException,
    expected_code: str,
    expected_result: dict[str, object],
) -> None:
    database = tmp_path / "reference-toctou.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    _write_reference(reference)
    authorized_bytes = reference.read_bytes()
    workspace_path = "fx/tools/flow/reference-toctou"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="reference-toctou",
            prompt_snapshot="prompt",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(authorized_bytes).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="operator",
            provider_workspace_path=workspace_path,
            provider_workspace_fingerprint=hashlib.sha256(workspace_path.encode()).hexdigest(),
        )
    )

    class InspectingRuntime:
        def prepare_and_dispatch(self, request: object, _sink: object) -> None:
            upload = getattr(request, "reference")
            assert upload.payload == authorized_bytes
            assert upload.sha256 == hashlib.sha256(authorized_bytes).hexdigest()
            raise runtime_error

    def runtime_factory(_context: object) -> Any:
        Image.new("RGB", (4, 4), color=(255, 0, 0)).save(reference)
        return InspectingRuntime()

    result = FlowImageGenerateHandler(
        images._sessions,
        work_root=work_root,
        _runtime_factory=runtime_factory,  # type: ignore[arg-type]
    ).execute(
        JobExecutionContext(
            job_id=submission.job.job_id,
            job_type="image.generate",
            campaign_id=campaign.campaign_id,
            input=submission.job.input,
            attempt_number=1,
        )
    )

    assert result.outcome == "blocked"
    assert result.error_code == expected_code
    assert result.result == expected_result
    images.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "job_fingerprint",
        "scene_ownership",
        "executor_policy",
        "authorization_event",
        "workspace_path_escape",
        "workspace_fingerprint",
        "reference_sha",
        "run_contract",
        "missing_slot",
        "extra_slot",
        "prepared_dispatch_intent",
        "slot_chronology",
        "unaudited_retry",
        "completed_missing_artifact",
    ],
)
def test_flow_integrity_matrix_rejects_before_runtime_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
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
    _write_reference(reference)
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    request_data = {
        "campaign_id": campaign.campaign_id,
        "scene_variant_id": campaign.scene_variants[0].scene_variant_id,
        "idempotency_key": f"integrity-{corruption.replace('authorization', 'auth')}",
        "prompt_snapshot": "A moonlit studio",
        "reference_image_path": "references/avatar.png",
        "reference_image_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "executor": "playwright_python",
        "generation_contract_version": "flow-generation-v1",
        "provider_action_confirmed": True,
        "provider_action_approved_by": "creative-operator",
        "provider_workspace_path": workspace_path,
        "provider_workspace_fingerprint": (
            "0" * 64
            if corruption == "workspace_fingerprint"
            else hashlib.sha256(workspace_path.encode()).hexdigest()
        ),
    }
    if corruption == "workspace_fingerprint":
        real_sha256 = hashlib.sha256

        class WrongWorkspaceDigest:
            @staticmethod
            def hexdigest() -> str:
                return "0" * 64

        with monkeypatch.context() as patch:
            patch.setattr(
                image_domain.hashlib,
                "sha256",
                lambda data=b"": (
                    WrongWorkspaceDigest() if data == workspace_path.encode() else real_sha256(data)
                ),
            )
            request = ImageGenerateRequest(**request_data)  # type: ignore[arg-type]
    else:
        request = ImageGenerateRequest(**request_data)  # type: ignore[arg-type]
    submission = images.generate(request)
    with images._sessions() as session:
        job = session.get(JobRow, submission.job.job_id)
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        assert run is not None
        slots = list(
            session.scalars(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == run.id
                )
            )
        )
        assert job is not None and len(slots) == 2
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
                    JobEventRow.event_type == "job.provider_action_authorized",
                )
            )
            assert authorization is not None
            session.add(
                JobEventRow(
                    id=str(uuid4()),
                    job_id=job.id,
                    event_type="job.provider_action_authorized",
                    timestamp=authorization.timestamp,
                    metadata_json={"executor": "local_fake"},
                )
            )
        elif corruption == "workspace_path_escape":
            run.provider_workspace_path = "../outside"
        elif corruption == "reference_sha":
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
        elif corruption == "prepared_dispatch_intent":
            session.execute(text("PRAGMA ignore_check_constraints = ON"))
            run.dispatch_intent_at = run.created_at
        elif corruption == "slot_chronology":
            slots[0].state = "observed"
            slots[0].provider_slot_fingerprint = "slot-a"
        elif corruption == "unaudited_retry":
            run.dispatch_attempt_number = 2
        elif corruption == "completed_missing_artifact":
            session.execute(text("PRAGMA ignore_check_constraints = ON"))
            run.stage = "completed"
            generation = session.get(ImageGenerationRow, submission.generation.image_generation_id)
            assert generation is not None
            generation.provider_state = "completed"
            for slot in slots:
                slot.state = "ingested"
        session.commit()
    calls = 0

    def factory(*_args: object) -> FlowGenerationRuntime:
        nonlocal calls
        calls += 1
        raise AssertionError("integrity rejection must precede browser construction")

    execution_campaign_id = (
        other_campaign_id if corruption == "scene_ownership" else campaign.campaign_id
    )
    assert execution_campaign_id is not None
    result = FlowImageGenerateHandler(
        images._sessions, work_root=work_root, _runtime_factory=factory
    ).execute(
        JobExecutionContext(
            job_id=submission.job.job_id,
            job_type="image.generate",
            campaign_id=execution_campaign_id,
            input=submission.job.input,
            attempt_number=1,
        )
    )
    assert result.outcome == "terminal_failure"
    assert result.error_code == "image_job_integrity_failed"
    assert calls == 0
    images.close()


def test_dispatch_intent_cas_rejects_preexisting_checkpoint_timestamps(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dispatch-intent-cas.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4)).save(reference)
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="dispatch-intent-cas",
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
        session.execute(text("PRAGMA ignore_check_constraints = ON"))
        run.stage = "inputs_verified"
        run.dispatch_intent_at = run.created_at
        session.commit()
        run_id = run.id

    sink = FlowGenerationCheckpointSink(
        images._sessions,
        run_id=run_id,
        clock=lambda: submission.generation.created_at,
    )
    from auraly_pipeline.flow.generation_domain import FlowWorkspaceIdentity

    with pytest.raises(FlowCheckpointConflictError):
        sink.record_dispatch_intent(
            FlowWorkspaceIdentity(
                workspace_path=workspace_path,
                fingerprint=hashlib.sha256(workspace_path.encode()).hexdigest(),
            )
        )
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
    _write_reference(reference)
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
        generation = session.get(
            ImageGenerationRow,
            submission.generation.image_generation_id,
        )
        assert generation is not None
        now = run.created_at
        run.stage = "dispatch_confirmed"
        run.dispatch_intent_at = now
        run.dispatch_confirmed_at = now
        generation.dispatched_at = now
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
    _write_reference(reference)
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

    def factory(*_args: object) -> FlowGenerationRuntime:
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


@pytest.mark.parametrize("lock_target", ["run_checkpoint", "generation_checkpoint"])
def test_checkpoint_database_lock_returns_flow_safe_blocked_result(
    tmp_path: Path,
    lock_target: str,
) -> None:
    """A transient SQLite checkpoint lock must not escape the Job handler boundary."""
    database = tmp_path / "checkpoint-lock.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    _write_reference(reference)
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key=f"checkpoint-lock-{lock_target}",
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
    armed = lock_target == "generation_checkpoint"

    class LockedCheckpointRuntime:
        def prepare_and_dispatch(self, _request: object, sink: object) -> None:
            nonlocal armed
            armed = lock_target == "run_checkpoint"
            sink.record_inputs_verified(  # type: ignore[attr-defined]
                FlowGenerationObservation(reference_verified=True, prompt_verified=True)
            )

    def lock_checkpoint_write(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal armed
        expected_prefix = (
            "UPDATE FLOW_GENERATION_RUNS"
            if lock_target == "run_checkpoint"
            else "UPDATE IMAGE_GENERATIONS"
        )
        if armed and statement.lstrip().upper().startswith(expected_prefix):
            armed = False
            raise OperationalError(
                statement,
                None,
                sqlite3.OperationalError("database is locked"),
            )

    def runtime_factory(_context: object) -> Any:
        return LockedCheckpointRuntime()

    event.listen(images._engine, "before_cursor_execute", lock_checkpoint_write)
    try:
        result = FlowImageGenerateHandler(
            images._sessions,
            work_root=work_root,
            _runtime_factory=runtime_factory,
        ).execute(
            JobExecutionContext(
                job_id=submission.job.job_id,
                job_type="image.generate",
                campaign_id=campaign.campaign_id,
                input=submission.job.input,
                attempt_number=1,
            )
        )
    finally:
        event.remove(images._engine, "before_cursor_execute", lock_checkpoint_write)

    assert result.outcome == "blocked"
    assert result.error_code == "flow_recovery_blocked"
    with images._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        assert run is not None
        slots = list(
            session.scalars(
                select(FlowCandidateSlotRow).where(
                    FlowCandidateSlotRow.flow_generation_run_id == run.id
                )
            )
        )
        assert run.stage == "blocked"
        assert run.last_failure_code == "flow_recovery_blocked"
        assert run.dispatch_intent_at is None
        assert [slot.state for slot in slots] == ["pending", "pending"]
        generation = session.get(ImageGenerationRow, submission.generation.image_generation_id)
        assert generation is not None
        assert generation.provider_state == (
            "generating" if lock_target == "run_checkpoint" else "queued"
        )
    images.close()


def test_stale_checkpoint_conflict_does_not_regress_advanced_run(tmp_path: Path) -> None:
    """A losing worker cannot replace a concurrently advanced checkpoint with blocked."""
    database = tmp_path / "stale-checkpoint.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    _write_reference(reference)
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="stale-checkpoint",
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

    class RacingCheckpointRuntime:
        def prepare_and_dispatch(self, _request: object, sink: object) -> None:
            with images._sessions() as session:
                run = session.scalar(
                    select(FlowGenerationRunRow).where(
                        FlowGenerationRunRow.image_generation_id
                        == submission.generation.image_generation_id
                    )
                )
                assert run is not None
                run.stage = "inputs_verified"
                session.commit()
            sink.record_inputs_verified(  # type: ignore[attr-defined]
                FlowGenerationObservation(reference_verified=True, prompt_verified=True)
            )

    def runtime_factory(_context: object) -> Any:
        return RacingCheckpointRuntime()

    result = FlowImageGenerateHandler(
        images._sessions,
        work_root=work_root,
        _runtime_factory=runtime_factory,
    ).execute(
        JobExecutionContext(
            job_id=submission.job.job_id,
            job_type="image.generate",
            campaign_id=campaign.campaign_id,
            input=submission.job.input,
            attempt_number=1,
        )
    )

    assert result.outcome == "blocked"
    assert result.error_code == "flow_recovery_blocked"
    with images._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        assert run is not None
        assert run.stage == "inputs_verified"
        assert run.last_failure_code is None
    images.close()


@pytest.mark.parametrize("lock_boundary", ["begin_immediate", "post_commit_reload"])
def test_completion_database_lock_returns_blocked_without_corrupting_committed_state(
    tmp_path: Path,
    lock_boundary: str,
) -> None:
    """Completion contention preserves both ingested candidates and committed checkpoints."""
    database = tmp_path / f"completion-{lock_boundary}-lock.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    _write_reference(reference)
    workspace_path = "fx/tools/flow/local-workspace"
    images = ImageService.for_database(database, work_root=work_root)
    submission = images.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key=f"completion-lock-{lock_boundary}",
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
    expected_hashes: list[str] = []
    with images._sessions() as session:
        generation = session.get(ImageGenerationRow, submission.generation.image_generation_id)
        assert generation is not None
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation.id
            )
        )
        assert run is not None
        slots = list(
            session.scalars(
                select(FlowCandidateSlotRow)
                .where(FlowCandidateSlotRow.flow_generation_run_id == run.id)
                .order_by(FlowCandidateSlotRow.slot_index)
            )
        )
        assert len(slots) == 2
        generation.provider_state = "generating"
        run.stage = "downloading"
        run.dispatch_intent_at = run.created_at
        run.dispatch_confirmed_at = run.created_at
        generation.dispatched_at = run.created_at
        evidence = work_root / "evidence" / "grid.png"
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(b"sanitized-grid-evidence")
        run.grid_evidence_path = evidence.relative_to(work_root).as_posix()
        run.grid_evidence_sha256 = hashlib.sha256(evidence.read_bytes()).hexdigest()
        for index, slot in enumerate(slots):
            final = resolve_flow_final_path(
                work_root=work_root,
                campaign_id=generation.campaign_id,
                scene_variant_id=generation.scene_variant_id,
                generation_number=generation.generation_number,
                candidate_index=index,
                image_format="png",
            )
            final.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (2048, 1), color=(16 + index, 32, 64)).save(final)
            facts = inspect_flow_artifact(final)
            expected_hashes.append(facts.sha256)
            candidate = ImageCandidate(
                image_candidate_id=str(uuid4()),
                image_generation_id=generation.id,
                candidate_index=index,
                source_path=final.relative_to(work_root).as_posix(),
                sha256=facts.sha256,
                width=facts.width,
                height=facts.height,
                size_bytes=facts.size_bytes,
                format=facts.format,
                review_status="pending_review",
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
            ImageRepository.create_candidate_in_session(session, candidate)
            slot.state = "ingested"
            slot.provider_slot_fingerprint = hashlib.sha256(
                f"slot-{index}".encode()
            ).hexdigest()
            slot.download_intent_at = run.created_at
            slot.staging_path = final.relative_to(work_root).as_posix()
            slot.staged_sha256 = facts.sha256
            slot.image_candidate_id = candidate.image_candidate_id
        session.commit()
    armed = False

    class CompletionRuntime:
        def reconcile(self, _workspace: object, _sink: object) -> None:
            return None

    def lock_completion_boundary(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal armed
        normalized = " ".join(statement.upper().split())
        if (
            lock_boundary == "post_commit_reload"
            and normalized.startswith("UPDATE FLOW_GENERATION_RUNS SET")
        ):
            armed = True
            return
        locked_begin = lock_boundary == "begin_immediate" and normalized == "BEGIN IMMEDIATE"
        locked_reload = (
            lock_boundary == "post_commit_reload"
            and normalized.startswith("SELECT FLOW_GENERATION_RUNS")
        )
        if armed and (locked_begin or locked_reload):
            armed = False
            raise OperationalError(
                statement,
                None,
                sqlite3.OperationalError("database is locked"),
            )

    def runtime_factory(_context: object) -> CompletionRuntime:
        nonlocal armed
        armed = lock_boundary == "begin_immediate"
        return CompletionRuntime()

    event.listen(images._engine, "before_cursor_execute", lock_completion_boundary)
    try:
        result = FlowImageGenerateHandler(
            images._sessions,
            work_root=work_root,
            _runtime_factory=runtime_factory,  # type: ignore[arg-type]
        ).execute(
            JobExecutionContext(
                job_id=submission.job.job_id,
                job_type="image.generate",
                campaign_id=campaign.campaign_id,
                input=submission.job.input,
                attempt_number=1,
            )
        )
    finally:
        event.remove(images._engine, "before_cursor_execute", lock_completion_boundary)

    assert result.outcome == "blocked"
    assert result.error_code == "flow_recovery_blocked"
    with images._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        candidates = list(
            session.scalars(
                select(ImageCandidateRow)
                .where(
                    ImageCandidateRow.image_generation_id
                    == submission.generation.image_generation_id
                )
                .order_by(ImageCandidateRow.candidate_index)
            )
        )
        assert run is not None
        if lock_boundary == "begin_immediate":
            assert run.stage == "blocked"
            assert run.last_failure_code == "flow_recovery_blocked"
        else:
            assert run.stage == "completed"
            assert run.last_failure_code is None
            generation = session.get(
                ImageGenerationRow, submission.generation.image_generation_id
            )
            assert generation is not None
            assert generation.provider_state == "completed"
        assert [candidate.sha256 for candidate in candidates] == expected_hashes
    images.close()
