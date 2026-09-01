from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import inspect
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from typing import Any, cast
from urllib.parse import quote, urlencode

from PIL import Image
from playwright.sync_api import Page, sync_playwright
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.cli import app
from auraly_pipeline.flow.artifacts import (
    FlowArtifactFacts,
    allocate_flow_staging_path,
    inspect_flow_artifact,
    publish_flow_artifact_exclusive,
    resolve_flow_final_path,
)
from auraly_pipeline.flow.diagnostics import FlowGridEvidence, publish_flow_grid_evidence
from auraly_pipeline.flow.generation import (
    FlowGenerationArtifactContext,
    FlowGenerationRuntime,
)
from auraly_pipeline.flow.generation_domain import (
    FlowCandidateObservation,
    FlowGenerationObservation,
)
from auraly_pipeline.images.db_models import (
    FlowCandidateSlotRow,
    FlowGenerationRunRow,
    ImageCandidateRow,
    ImageGenerationRow,
)
from auraly_pipeline.images.domain import ImageGenerateRequest
from auraly_pipeline.images.flow_handler import FlowImageGenerateHandler
from auraly_pipeline.images.service import ImageRecoveryBlockedError, ImageService
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.handlers import JobExecutionContext, SimulatedWorkerCrash
from tests.test_campaign_domain import valid_campaign_data
from tests.test_flow_generation import _task9_runtime
from tests.test_flow_security import (
    _expanded_zip_bytes,
    _published_bytes,
    _run_dir,
    _serialized_result,
    _local_paths,
    local_preflight,
)


REPOSITORY_ROOT = Path(__file__).parents[1].resolve()
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "auraly_pipeline"
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
WORKSPACE_PATH = "fx/tools/flow/task-12-workspace"
WORKSPACE_SHA = hashlib.sha256(WORKSPACE_PATH.encode("utf-8")).hexdigest()

CRASH_POINTS = (
    "before_inputs_checkpoint",
    "after_inputs_checkpoint",
    "before_dispatch_intent",
    "after_dispatch_intent",
    "during_generate_click",
    "after_dispatch_confirmation_observed",
    "after_dispatch_confirmation_checkpoint",
    "after_grid_observed",
    "after_grid_evidence_published",
    "after_slot_0_intent",
    "after_slot_0_download_saved",
    "after_slot_0_download_checkpoint",
    "after_slot_0_final_publish",
    "after_slot_0_candidate_ingest",
    "after_slot_1_intent",
    "after_slot_1_download_saved",
    "after_slot_1_download_checkpoint",
    "after_slot_1_final_publish",
    "after_slot_1_candidate_ingest",
    "after_run_completed",
    "before_job_completion",
)

DENY_VALUES = (
    "PRIVATE PROMPT phrase",
    "reference-secret.png",
    "person@example.com",
    "AUTHORIZATION_SECRET",
    "COOKIE_SECRET",
    "SIGNED_QUERY_SECRET",
    "FRAGMENT_SECRET",
    r"C:\Users\Private\reference-secret.png",
    "/home/private/reference-secret.png",
)

_PRE_INTENT_POINTS = frozenset(
    {
        "before_inputs_checkpoint",
        "after_inputs_checkpoint",
        "before_dispatch_intent",
    }
)
_EXPECTED_BLOCKED_POINTS = frozenset(
    {
        "after_dispatch_intent",
        "after_grid_evidence_published",
    }
)


@pytest.fixture(name="flow_generation_page")
def provide_flow_generation_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            yield browser.new_page()
        finally:
            browser.close()


@dataclass
class _MutableClock:
    value: datetime = NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass
class _CrashScenario:
    crash_point: str
    source_artifacts: tuple[Path, Path]
    crash_armed: bool = True
    generate_clicks: int = 0
    recovery_browser_opens: int = 0

    def crash(self, point: str) -> None:
        if self.crash_armed and point == self.crash_point:
            self.crash_armed = False
            raise SimulatedWorkerCrash(f"Task 12 crash at {point}")


class _CrashMatrixRuntime:
    """Precise provider double; persistence, publication, and recovery stay real."""

    def __init__(
        self,
        context: FlowGenerationArtifactContext,
        scenario: _CrashScenario,
        *,
        recovery: bool = False,
    ) -> None:
        self._context = context
        self._scenario = scenario
        self._recovery = recovery

    @staticmethod
    def _observation() -> FlowGenerationObservation:
        return FlowGenerationObservation(reference_verified=True, prompt_verified=True)

    @staticmethod
    def _candidate(slot_index: int) -> FlowCandidateObservation:
        return FlowCandidateObservation(
            fingerprint=hashlib.sha256(f"task-12-slot-{slot_index}".encode()).hexdigest(),
            semantic_order=slot_index,
            completed=True,
        )

    def prepare_and_dispatch(self, _request: object, sink: object) -> None:
        self._scenario.crash("before_inputs_checkpoint")
        sink.record_inputs_verified(self._observation())  # type: ignore[attr-defined]
        self._scenario.crash("after_inputs_checkpoint")
        self._scenario.crash("before_dispatch_intent")
        sink.record_dispatch_intent(self._context.workspace)  # type: ignore[attr-defined]
        if self._scenario.crash_point == "after_dispatch_intent" and self._scenario.crash_armed:
            # Provider mutation may have happened when the process disappears after intent.
            self._scenario.generate_clicks += 1
            self._scenario.crash("after_dispatch_intent")
        self._scenario.generate_clicks += 1
        self._scenario.crash("during_generate_click")
        self._scenario.crash("after_dispatch_confirmation_observed")
        sink.record_dispatch_confirmed(self._observation())  # type: ignore[attr-defined]
        self._scenario.crash("after_dispatch_confirmation_checkpoint")

    def reconcile(self, _workspace: object, _sink: object) -> bool:
        return False

    def recover_dispatch(
        self,
        _workspace: object,
        *,
        expected_fingerprints: tuple[str, ...] = (),
    ) -> bool:
        self._scenario.recovery_browser_opens += 1
        assert len(expected_fingerprints) <= 2
        return self._scenario.crash_point != "after_dispatch_intent"

    def observe_candidates(self, sink: object) -> tuple[FlowCandidateObservation, ...]:
        candidates = (self._candidate(0), self._candidate(1))
        self._scenario.crash("after_grid_observed")
        for index, candidate in enumerate(candidates):
            sink.bind_candidate_slot(index, candidate)  # type: ignore[attr-defined]
        evidence = self._publish_grid_evidence()
        self._scenario.crash("after_grid_evidence_published")
        sink.record_candidates_observed(evidence)  # type: ignore[attr-defined]
        return candidates

    def _publish_grid_evidence(self) -> FlowGridEvidence:
        final = resolve_flow_final_path(
            work_root=self._context.work_root,
            campaign_id=self._context.campaign_id,
            scene_variant_id=self._context.scene_variant_id,
            generation_number=self._context.generation_number,
            candidate_index=0,
            image_format="png",
        )
        local = publish_flow_grid_evidence(
            self._scenario.source_artifacts[0].read_bytes(),
            evidence_root=final.parent / "inspection",
        )
        published = final.parent / "inspection" / local.relative_path
        return FlowGridEvidence(
            relative_path=published.relative_to(self._context.work_root).as_posix(),
            sha256=local.sha256,
        )

    def download_slot(self, slot_index: int, sink: object) -> FlowArtifactFacts:
        fingerprint = sink.candidate_fingerprint(slot_index)  # type: ignore[attr-defined]
        sink.record_download_intent(slot_index, fingerprint)  # type: ignore[attr-defined]
        self._scenario.crash(f"after_slot_{slot_index}_intent")
        staging = allocate_flow_staging_path(
            work_root=self._context.work_root,
            campaign_id=self._context.campaign_id,
            scene_variant_id=self._context.scene_variant_id,
            generation_number=self._context.generation_number,
            candidate_index=slot_index,
        )
        staging.write_bytes(self._scenario.source_artifacts[slot_index].read_bytes())
        self._scenario.crash(f"after_slot_{slot_index}_download_saved")
        staged = inspect_flow_artifact(staging)
        sink.record_downloaded(  # type: ignore[attr-defined]
            slot_index,
            relative_path=staging.relative_to(self._context.work_root).as_posix(),
            sha256=staged.sha256,
        )
        self._scenario.crash(f"after_slot_{slot_index}_download_checkpoint")
        final = resolve_flow_final_path(
            work_root=self._context.work_root,
            campaign_id=self._context.campaign_id,
            scene_variant_id=self._context.scene_variant_id,
            generation_number=self._context.generation_number,
            candidate_index=slot_index,
            image_format=staged.format,
        )
        published = publish_flow_artifact_exclusive(
            staging,
            final,
            trusted_root=self._context.work_root,
        )
        self._scenario.crash(f"after_slot_{slot_index}_final_publish")
        return published


class _CrashAwareFlowHandler(FlowImageGenerateHandler):
    def __init__(self, *args: Any, scenario: _CrashScenario, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._scenario = scenario

    def _ingest_slot(
        self,
        generation: ImageGenerationRow,
        run_id: str,
        index: int,
        facts: FlowArtifactFacts,
    ) -> None:
        super()._ingest_slot(generation, run_id, index, facts)
        self._scenario.crash(f"after_slot_{index}_candidate_ingest")

    def _complete(self, generation_id: str, run_id: str) -> None:
        super()._complete(generation_id, run_id)
        self._scenario.crash("after_run_completed")

    def execute(self, context: JobExecutionContext):
        result = super().execute(context)
        if result.outcome == "success":
            self._scenario.crash("before_job_completion")
        return result


def _source_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    artifacts: list[Path] = []
    for index in range(2):
        path = tmp_path / f"source-{index}.png"
        Image.new("RGB", (2048, 1), color=(20 + index, 40, 60)).save(path, format="PNG")
        artifacts.append(path)
    return artifacts[0], artifacts[1]


def _create_crash_service(
    tmp_path: Path,
    crash_point: str,
) -> tuple[ImageService, _MutableClock, _CrashScenario, str, str, Path]:
    database = tmp_path / "auraly.db"
    work_root = tmp_path / "work"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"trusted-reference")
    clock = _MutableClock()
    scenario = _CrashScenario(crash_point, _source_artifacts(tmp_path))
    service = ImageService.for_database(
        database,
        clock=clock,
        work_root=work_root,
        _recovery_runtime_factory=lambda context: _CrashMatrixRuntime(
            context,
            scenario,
            recovery=True,
        ),
    )
    handler = _CrashAwareFlowHandler(
        service._sessions,
        work_root=work_root,
        clock=clock,
        _runtime_factory=lambda context: _CrashMatrixRuntime(context, scenario),
        scenario=scenario,
    )
    service._jobs._handlers["image.generate"] = handler
    submission = service.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key=f"task-12-{crash_point}",
            prompt_snapshot="Task 12 deterministic prompt",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="task-12-operator",
            provider_workspace_path=WORKSPACE_PATH,
            provider_workspace_fingerprint=WORKSPACE_SHA,
        )
    )
    return (
        service,
        clock,
        scenario,
        submission.generation.image_generation_id,
        submission.job.job_id,
        work_root,
    )


def _durable_snapshot(
    service: ImageService,
    generation_id: str,
    work_root: Path,
) -> dict[str, object]:
    with service._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation_id
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
        candidates = list(
            session.scalars(
                select(ImageCandidateRow)
                .where(ImageCandidateRow.image_generation_id == generation_id)
                .order_by(ImageCandidateRow.candidate_index)
            )
        )
        artifact_facts = {
            path.relative_to(work_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(work_root.rglob("*"))
            if path.is_file()
        }
        canonical_final_facts = {
            relative: sha256
            for relative, sha256 in artifact_facts.items()
            if Path(relative).name.startswith("candidate-")
            and Path(relative).suffix in {".png", ".jpeg", ".webp"}
        }
        staging_artifact_facts = {
            relative: sha256
            for relative, sha256 in artifact_facts.items()
            if ".staging" in Path(relative).parts or Path(relative).suffix == ".part"
        }
        grid_evidence_facts = {
            relative: sha256
            for relative, sha256 in artifact_facts.items()
            if "inspection" in Path(relative).parts
        }
        return {
            "runStage": run.stage,
            "gridPath": run.grid_evidence_path,
            "gridSha": run.grid_evidence_sha256,
            "slotFingerprints": tuple(slot.provider_slot_fingerprint for slot in slots),
            "slotStates": tuple(slot.state for slot in slots),
            "candidateFacts": tuple(
                (candidate.id, candidate.source_path, candidate.sha256)
                for candidate in candidates
            ),
            "candidateFinalFacts": {
                candidate.source_path: candidate.sha256 for candidate in candidates
            },
            "canonicalFinalFacts": canonical_final_facts,
            "stagingArtifactFacts": staging_artifact_facts,
            "gridEvidenceFacts": grid_evidence_facts,
            "artifactFacts": artifact_facts,
        }


def _assert_preserved_evidence(
    before: dict[str, object],
    after: dict[str, object],
    work_root: Path,
) -> None:
    before_fingerprints = before["slotFingerprints"]
    after_fingerprints = after["slotFingerprints"]
    assert isinstance(before_fingerprints, tuple)
    assert isinstance(after_fingerprints, tuple)
    for index, fingerprint in enumerate(before_fingerprints):
        if fingerprint is not None:
            assert after_fingerprints[index] == fingerprint
    before_artifacts = cast(dict[str, str], before["artifactFacts"])
    after_artifacts = cast(dict[str, str], after["artifactFacts"])
    before_staging = cast(dict[str, str], before["stagingArtifactFacts"])
    after_finals = cast(dict[str, str], after["canonicalFinalFacts"])
    for source_path, sha256 in before_artifacts.items():
        if source_path in after_artifacts:
            assert after_artifacts[source_path] == sha256
            continue
        assert source_path in before_staging
        assert sha256 in after_finals.values()
    for category in ("canonicalFinalFacts", "gridEvidenceFacts"):
        before_category = cast(dict[str, str], before[category])
        after_category = cast(dict[str, str], after[category])
        for source_path, sha256 in before_category.items():
            assert after_category[source_path] == sha256
            artifact = work_root / source_path
            assert artifact.is_file()
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == sha256
    if before["gridPath"] is not None:
        assert after["gridPath"] == before["gridPath"]
        assert after["gridSha"] == before["gridSha"]


@pytest.mark.parametrize("crash_point", CRASH_POINTS)
def test_complete_crash_matrix_recovers_without_redispatch_or_overwrite(
    tmp_path: Path,
    crash_point: str,
) -> None:
    """Every process boundary resumes from durable facts or stays safely blocked."""
    service, clock, scenario, generation_id, job_id, work_root = _create_crash_service(
        tmp_path,
        crash_point,
    )
    try:
        with pytest.raises(SimulatedWorkerCrash):
            service.worker_once("task-12-crashing-worker", lease_seconds=1)
        clicks_at_crash = scenario.generate_clicks
        assert clicks_at_crash == (0 if crash_point in _PRE_INTENT_POINTS else 1)
        before = _durable_snapshot(service, generation_id, work_root)
        assert len(before["candidateFacts"]) <= 2  # type: ignore[arg-type]

        clock.advance(2)
        stale = service._jobs.recover_stale_jobs()
        assert [job.job_id for job in stale] == [job_id]
        assert stale[0].status == "blocked"

        if crash_point in _EXPECTED_BLOCKED_POINTS:
            with pytest.raises(ImageRecoveryBlockedError):
                service.recover_generation(generation_id, reconciled_by="task-12-operator")
            after = _durable_snapshot(service, generation_id, work_root)
            assert len(after["candidateFacts"]) <= 2  # type: ignore[arg-type]
            assert scenario.generate_clicks == clicks_at_crash
            _assert_preserved_evidence(before, after, work_root)
            return

        recovered = service.recover_generation(
            generation_id,
            reconciled_by="task-12-operator",
        )
        assert recovered.job.status == "queued"
        assert scenario.generate_clicks == clicks_at_crash
        after_recovery = _durable_snapshot(service, generation_id, work_root)
        assert len(after_recovery["candidateFacts"]) <= 2  # type: ignore[arg-type]
        _assert_preserved_evidence(before, after_recovery, work_root)

        completed = service.worker_once("task-12-resume-worker", lease_seconds=1)
        assert completed is not None
        assert completed.status == "completed"
        assert scenario.generate_clicks == 1
        final_snapshot = _durable_snapshot(service, generation_id, work_root)
        assert len(final_snapshot["candidateFacts"]) == 2  # type: ignore[arg-type]
        _assert_preserved_evidence(before, final_snapshot, work_root)

        with service._sessions() as session:
            generation = session.get(ImageGenerationRow, generation_id)
            candidates = list(
                session.scalars(
                    select(ImageCandidateRow)
                    .where(ImageCandidateRow.image_generation_id == generation_id)
                    .order_by(ImageCandidateRow.candidate_index)
                )
            )
            assert generation is not None
            assert generation.dispatched_at is not None
            assert len(candidates) == 2
            for candidate in candidates:
                expected = resolve_flow_final_path(
                    work_root=work_root,
                    campaign_id=generation.campaign_id,
                    scene_variant_id=generation.scene_variant_id,
                    generation_number=generation.generation_number,
                    candidate_index=candidate.candidate_index,
                    image_format=candidate.format,
                )
                assert (work_root / candidate.source_path).resolve() == expected.resolve()
    finally:
        service.close()


@pytest.mark.parametrize(
    ("crash_point", "published_slot"),
    (("after_slot_0_final_publish", 0), ("after_slot_1_final_publish", 1)),
)
def test_crash_snapshot_includes_uningested_published_final(
    tmp_path: Path,
    crash_point: str,
    published_slot: int,
) -> None:
    """A final published before candidate ingestion is still durable evidence."""
    service, _clock, _scenario, generation_id, _job_id, work_root = _create_crash_service(
        tmp_path,
        crash_point,
    )
    try:
        with pytest.raises(SimulatedWorkerCrash):
            service.worker_once("task-12-published-final-worker", lease_seconds=1)
        snapshot = _durable_snapshot(service, generation_id, work_root)
        with service._sessions() as session:
            generation = session.get(ImageGenerationRow, generation_id)
            assert generation is not None
        final = resolve_flow_final_path(
            work_root=work_root,
            campaign_id=generation.campaign_id,
            scene_variant_id=generation.scene_variant_id,
            generation_number=generation.generation_number,
            candidate_index=published_slot,
            image_format="png",
        )
        relative = final.relative_to(work_root).as_posix()
        canonical_finals = cast(dict[str, str], snapshot["canonicalFinalFacts"])
        all_artifacts = cast(dict[str, str], snapshot["artifactFacts"])
        assert canonical_finals[relative] == hashlib.sha256(final.read_bytes()).hexdigest()
        assert all_artifacts[relative] == canonical_finals[relative]
    finally:
        service.close()


def _create_sensitive_generation(tmp_path: Path) -> tuple[Path, Path, str, str]:
    database = tmp_path / "sensitive.db"
    work_root = tmp_path / "work"
    campaign_data = valid_campaign_data()
    campaign_data["campaignId"] = "task-12-sensitive"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(campaign_data))
    campaigns.close()
    reference = work_root / "references" / "reference-secret.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"trusted-reference")
    service = ImageService.for_database(database, clock=lambda: NOW, work_root=work_root)
    submission = service.generate(
        ImageGenerateRequest(
            campaign_id=campaign.campaign_id,
            scene_variant_id=campaign.scene_variants[0].scene_variant_id,
            idempotency_key="task-12-sensitive-generation",
            prompt_snapshot=" | ".join(DENY_VALUES),
            reference_image_path="references/reference-secret.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="task-12-operator",
            provider_workspace_path=WORKSPACE_PATH,
            provider_workspace_fingerprint=WORKSPACE_SHA,
        )
    )
    with service._sessions() as session:
        job = session.get(JobRow, submission.job.job_id)
        assert job is not None
        job.status = "blocked"
        job.last_error_code = "flow_recovery_blocked"
        job.last_error_message = "The Flow generation requires reconciliation."
        session.commit()
    service.close()
    return (
        database,
        work_root,
        submission.generation.image_generation_id,
        submission.job.job_id,
    )


def _operational_sqlite_text(database: Path) -> bytes:
    """Serialize every operational/audit text cell except immutable generation intent."""
    excluded = {
        ("image_generations", "prompt_snapshot"),
        ("image_generations", "reference_image_path"),
    }
    values: list[tuple[str, str, object]] = []
    with sqlite3.connect(database) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = [
                (row[1], str(row[2]).upper())
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            text_columns = [
                name
                for name, kind in columns
                if any(token in kind for token in ("TEXT", "CHAR", "JSON", "VARCHAR"))
                and (table, name) not in excluded
            ]
            if not text_columns:
                continue
            select_list = ", ".join(f'"{column}"' for column in text_columns)
            for row in connection.execute(f'SELECT {select_list} FROM "{table}"'):
                values.extend(
                    (table, column, value)
                    for column, value in zip(text_columns, row, strict=True)
                    if value is not None
                )
    return json.dumps(values, sort_keys=True, default=str).encode("utf-8")


def test_seeded_sensitive_corpus_does_not_spread_to_public_or_operational_boundaries(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Persisted creative intent cannot spread into Job, Flow, CLI, result, or log data."""
    caplog.set_level("DEBUG")
    database, work_root, generation_id, job_id = _create_sensitive_generation(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "image",
            "generation",
            "recover",
            generation_id,
            "--reconciled-by",
            "task-12-operator",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )

    assert result.exit_code == 0, result.stdout
    public_output = (result.stdout + result.stderr).encode("utf-8")
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert {"promptSnapshot", "promptSha256", "referenceImagePath", "referenceImageSha256"}.isdisjoint(
        payload["generation"]
    )

    service = ImageService.for_database(database, work_root=work_root)
    try:
        job = service._jobs.get_job(job_id)
        job_surface = job.model_dump_json(by_alias=True).encode("utf-8")
    finally:
        service.close()
    operational = _operational_sqlite_text(database)
    result_json = json.dumps(payload, sort_keys=True).encode("utf-8")
    logs = caplog.text.encode("utf-8")
    for forbidden in DENY_VALUES:
        encoded = forbidden.encode("utf-8")
        assert encoded not in public_output
        assert encoded not in job_surface
        assert encoded not in operational
        assert encoded not in result_json
        assert encoded not in logs


def _mask_pixel_coordinates(
    bounds: tuple[tuple[int, int, int, int], ...],
) -> set[tuple[int, int]]:
    return {
        (x, y)
        for left, top, width, height in bounds
        for x in range(left, left + width)
        for y in range(top, top + height)
    }


def _assert_exact_raster_masks(
    screenshot_png: bytes,
    bounds: tuple[tuple[int, int, int, int], ...],
) -> None:
    with Image.open(BytesIO(screenshot_png)) as image:
        pixels = image.convert("RGB")
        actual = {
            (x, y)
            for y in range(pixels.height)
            for x in range(pixels.width)
            if pixels.getpixel((x, y)) == (255, 0, 255)
        }
    assert actual == _mask_pixel_coordinates(bounds), "raster mask pixels differ from semantic bounds"


def test_seeded_browser_corpus_is_covered_by_exact_semantic_raster_masks(
    tmp_path: Path,
    flow_generation_page: Page,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four private semantic regions are fully covered by the published raster masks."""
    work_root = tmp_path / "work"
    runtime = _task9_runtime("grid-two.html", flow_generation_page, work_root)
    seeded_regions = {
        "Account identity": "person@example.com | AUTHORIZATION_SECRET | COOKIE_SECRET",
        "Prompt": "PRIVATE PROMPT phrase | SIGNED_QUERY_SECRET | FRAGMENT_SECRET",
        "Reference preview": "reference preview containing private source imagery",
        "Upload filename": (
            r"reference-secret.png | C:\Users\Private\reference-secret.png"
            " | /home/private/reference-secret.png"
        ),
    }
    assert all(value in " | ".join(seeded_regions.values()) for value in DENY_VALUES)
    expected_labels = tuple(seeded_regions)
    for index, (label, value) in enumerate(seeded_regions.items()):
        locator = flow_generation_page.get_by_label(label, exact=True)
        if label == "Prompt":
            locator.fill(value)
        elif label != "Reference preview":
            locator.evaluate("(element, text) => { element.textContent = text; }", value)
        locator.evaluate(
            """(element, layout) => {
                Object.assign(element.style, {
                    position: 'fixed',
                    left: `${layout.left}px`,
                    top: `${layout.top}px`,
                    width: `${layout.width}px`,
                    height: `${layout.height}px`,
                    margin: '0',
                    padding: '0',
                    border: '0',
                    boxSizing: 'border-box',
                    display: 'block',
                    overflow: 'hidden',
                    background: '#ffffff',
                    color: '#000000',
                    zIndex: '1000'
                });
            }""",
            {"left": 20, "top": 20 + index * 50, "width": 320, "height": 32},
        )

    expected_bounds = tuple((20, 20 + index * 50, 320, 32) for index in range(4))
    unmasked = flow_generation_page.screenshot(type="png", animations="disabled")
    observed: list[tuple[str | None, tuple[int, int, int, int]]] = []
    real_screenshot = Page.screenshot

    def record_semantic_masks(page: Page, **kwargs: Any) -> bytes:
        masks = tuple(kwargs.get("mask", ()))
        for mask in masks:
            box = mask.bounding_box()
            assert box is not None
            bounds = (
                int(box["x"]),
                int(box["y"]),
                int(box["width"]),
                int(box["height"]),
            )
            observed.append(
                (
                    mask.get_attribute("aria-label"),
                    bounds,
                )
            )
        return real_screenshot(page, **kwargs)

    monkeypatch.setattr(Page, "screenshot", record_semantic_masks)
    evidence = runtime.capture_grid_evidence()
    screenshot_bytes = (work_root / evidence.relative_path).read_bytes()

    assert tuple(label for label, _bounds in observed) == expected_labels
    assert tuple(bounds for _label, bounds in observed) == expected_bounds
    _assert_exact_raster_masks(screenshot_bytes, expected_bounds)
    with pytest.raises(AssertionError, match="raster mask pixels"):
        _assert_exact_raster_masks(unmasked, expected_bounds)


def test_seeded_browser_corpus_is_absent_from_expanded_trace_and_result(
    tmp_path: Path,
) -> None:
    """The complete seeded corpus and its URL encoding are removed from diagnostics."""

    query = urlencode({f"secret{index}": value for index, value in enumerate(DENY_VALUES)})
    preflight_root = tmp_path / "preflight"
    result = local_preflight(
        fixture=f"missing-prompt.html?{query}#FRAGMENT_SECRET",
        deny_values=DENY_VALUES,
        tmp_path=preflight_root,
    )
    run_dir = _run_dir(_local_paths(preflight_root), result)
    trace_bytes = _expanded_zip_bytes(run_dir / "trace.zip")
    published = _published_bytes(run_dir)
    public_result = _serialized_result(result)

    assert result.status == "ui_contract_failed"
    for forbidden in DENY_VALUES:
        literal = forbidden.encode("utf-8")
        percent_encoded = quote(forbidden, safe="").encode("utf-8")
        assert literal not in trace_bytes + published + public_result
        assert percent_encoded not in trace_bytes + published + public_result


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _dotted_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _symbol_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                aliases[imported.asname or imported.name.split(".")[0]] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for imported in node.names:
                aliases[imported.asname or imported.name] = f"{node.module}.{imported.name}"
    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            value = assignment.value
            if value is None:
                continue
            resolved = _dotted_name(value, aliases)
            if resolved is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and aliases.get(target.id) != resolved:
                    aliases[target.id] = resolved
                    changed = True
        if not changed:
            break
    return aliases


def _function_records(tree: ast.Module) -> dict[str, tuple[str | None, ast.FunctionDef]]:
    records: dict[str, tuple[str | None, ast.FunctionDef]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.FunctionDef):
                records[node.name] = (None, node)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    records[f"{node.name}.{child.name}"] = (node.name, child)
    return records


def _reachable_recovery_forbidden_calls(
    tree: ast.Module,
    *,
    all_functions_are_roots: bool = False,
) -> set[str]:
    """Follow local recovery helpers and report any Generate-capable callsite."""
    aliases = _symbol_aliases(tree)
    records = _function_records(tree)
    roots = {
        key
        for key, (_class_name, function) in records.items()
        if all_functions_are_roots
        or any(token in function.name for token in ("recover", "reconcile", "resolve_no_dispatch"))
    }
    pending = list(roots)
    visited: set[str] = set()
    forbidden: set[str] = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        class_name, function = records[key]
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            called = _dotted_name(call.func, aliases)
            if called is None:
                continue
            tail = called.rsplit(".", 1)[-1]
            if tail in {
                "click",
                "resolve_generate_control",
                "prepare_and_dispatch",
                "_fresh_generate_control_after_intent",
            }:
                forbidden.add(called)
            local: str | None = None
            if called.startswith("self.") and class_name is not None:
                local = f"{class_name}.{called.removeprefix('self.')}"
            elif called in records:
                local = called
            elif class_name is not None and f"{class_name}.{called}" in records:
                local = f"{class_name}.{called}"
            if local in records and local not in visited:
                pending.append(local)
    return forbidden


def _called_symbols(tree: ast.Module) -> set[str]:
    aliases = _symbol_aliases(tree)
    return {
        called
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (called := _dotted_name(node.func, aliases)) is not None
    }


def _open_mode(call: ast.Call, called: str) -> str | None:
    if called.endswith("os.open"):
        return None
    position = 0 if "." in called and called.rsplit(".", 1)[-1] == "open" else 1
    mode_node: ast.AST | None = call.args[position] if len(call.args) > position else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    return mode_node.value if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str) else None


def _flag_symbols(node: ast.AST, aliases: dict[str, str]) -> set[str]:
    symbols: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, (ast.Name, ast.Attribute)):
            name = _dotted_name(child, aliases)
            if name is not None:
                symbols.add(name.rsplit(".", 1)[-1])
    return symbols


def _overwrite_findings(tree: ast.Module) -> set[str]:
    aliases = _symbol_aliases(tree)
    findings: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        called = _dotted_name(call.func, aliases)
        if called is None:
            continue
        tail = called.rsplit(".", 1)[-1]
        receiver = called.rsplit(".", 1)[0].casefold() if "." in called else ""
        path_replace = tail == "replace" and (
            called.endswith("os.replace")
            or any(
                token in receiver
                for token in ("path", "artifact", "staging", "final", "destination")
            )
        )
        if path_replace or tail in {"write_bytes", "write_text", "copyfile", "copy2"}:
            findings.add(called)
        if tail == "open":
            mode = _open_mode(call, called)
            if mode is not None and "x" not in mode and (
                mode.startswith(("w", "a")) or "+" in mode
            ):
                findings.add(f"{called}:{mode}")
            if called.endswith("os.open"):
                flags_node = call.args[1] if len(call.args) > 1 else next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "flags"),
                    None,
                )
                flags = _flag_symbols(flags_node, aliases) if flags_node is not None else set()
                if "O_TRUNC" in flags or ("O_CREAT" in flags and "O_EXCL" not in flags):
                    findings.add(f"{called}:{','.join(sorted(flags))}")
    return findings


def _unsafe_locator_findings(tree: ast.Module) -> set[str]:
    aliases = _symbol_aliases(tree)
    findings: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        called = _dotted_name(call.func, aliases)
        if called is None:
            continue
        tail = called.rsplit(".", 1)[-1]
        if tail == "nth":
            findings.add(called)
        if tail == "click" and (call.args or call.keywords):
            findings.add(called)
        if tail in {"locator", "get_by_text", "get_by_role", "get_by_label"}:
            strings = [
                value.value.casefold()
                for value in (*call.args, *(keyword.value for keyword in call.keywords))
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            ]
            if any(value.startswith("xpath=") or "nth-child" in value for value in strings):
                findings.add(called)
    return findings


def _sensitive_browser_reads(tree: ast.Module) -> set[str]:
    aliases = _symbol_aliases(tree)
    findings: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        called = _dotted_name(call.func, aliases)
        if called is None:
            continue
        tail = called.rsplit(".", 1)[-1]
        if tail in {"cookies", "storage_state"}:
            findings.add(called)
        if tail in {"read_bytes", "read_text", "open"}:
            sources = [ast.unparse(call.func), *(ast.unparse(argument) for argument in call.args[:1])]
            if any(
                token in source.casefold()
                for source in sources
                for token in ("profile", "user_data", "cookie", "storage_state")
            ):
                findings.add(called)
    return findings


_TARGET_INJECTION_TOKENS = (
    "flowurl",
    "targeturl",
    "runtimetarget",
    "locator_target",
    "target",
    "factory",
)


def _is_target_injection_name(name: str) -> bool:
    normalized = name.replace("-", "").replace("_", "").casefold()
    return any(token.replace("_", "") in normalized for token in _TARGET_INJECTION_TOKENS)


def _job_submit_injection_findings(tree: ast.Module) -> set[str]:
    aliases = _symbol_aliases(tree)
    findings: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        called = _dotted_name(call.func, aliases)
        if called is None or called.rsplit(".", 1)[-1] != "JobSubmit":
            continue
        for keyword in call.keywords:
            if keyword.arg is not None and _is_target_injection_name(keyword.arg):
                findings.add(keyword.arg)
            if keyword.arg == "input" and isinstance(keyword.value, ast.Dict):
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        if _is_target_injection_name(key.value):
                            findings.add(key.value)
    return findings


def _environment_target_findings(tree: ast.Module) -> set[str]:
    """Find environment-backed browser target keys at actual lookup callsites."""
    findings: set[str] = set()
    for node in ast.walk(tree):
        candidates: list[ast.AST] = []
        if isinstance(node, ast.Call):
            candidates.extend(node.args)
            candidates.extend(keyword.value for keyword in node.keywords)
        elif isinstance(node, ast.Subscript):
            candidates.append(node.slice)
        for candidate in candidates:
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                normalized = candidate.value.replace("_", "").casefold()
                if "flow" in normalized and ("url" in normalized or "target" in normalized):
                    findings.add(candidate.value)
    return findings


def test_structural_boundaries_isolate_browser_and_recovery_mutation() -> None:
    """Browser authority and Generate mutation remain unreachable from all recovery owners."""
    for relative in ("images/service.py", "images/repository.py"):
        imports = _imported_modules(_module_tree(SOURCE_ROOT / relative))
        assert not any(module == "playwright" or module.startswith("playwright.") for module in imports)

    for path in sorted((SOURCE_ROOT / "flow").glob("generation*.py")):
        assert _sensitive_browser_reads(_module_tree(path)) == set()
    assert _environment_target_findings(_module_tree(SOURCE_ROOT / "flow" / "config.py")) == set()

    recovery_owners = (
        (SOURCE_ROOT / "flow" / "generation.py", False),
        (SOURCE_ROOT / "images" / "service.py", False),
        (SOURCE_ROOT / "images" / "flow_handler.py", False),
        (SOURCE_ROOT / "images" / "repository.py", True),
    )
    for path, all_functions_are_roots in recovery_owners:
        assert _reachable_recovery_forbidden_calls(
            _module_tree(path),
            all_functions_are_roots=all_functions_are_roots,
        ) == set()

    worker_tree = _module_tree(SOURCE_ROOT / "images" / "flow_handler.py")
    worker_calls = {symbol.rsplit(".", 1)[-1] for symbol in _called_symbols(worker_tree)}
    assert {"record_download_baseline", "wait_for_download"}.isdisjoint(worker_calls)

    aliased_unsafe_recovery = ast.parse(
        """
from provider import resolve_generate_control as locate_generate
def helper(page):
    control = locate_generate(page)
    control.click()
def recover_generation(page):
    delegated = helper
    delegated(page)
"""
    )
    assert _reachable_recovery_forbidden_calls(aliased_unsafe_recovery) == {
        "control.click",
        "provider.resolve_generate_control",
    }
    aliased_legacy_worker = ast.parse(
        """
from auraly_pipeline.image_generation import wait_for_download as await_legacy
legacy = await_legacy
def execute():
    legacy()
"""
    )
    assert "wait_for_download" in {
        symbol.rsplit(".", 1)[-1] for symbol in _called_symbols(aliased_legacy_worker)
    }
    unsafe_sensitive_reads = ast.parse(
        """
def unsafe(context, profile_path):
    context.storage_state()
    profile_path.read_bytes()
"""
    )
    assert _sensitive_browser_reads(unsafe_sensitive_reads) == {
        "context.storage_state",
        "profile_path.read_bytes",
    }


def test_structural_boundaries_prohibit_unsafe_selectors_overwrite_and_targets(
    tmp_path: Path,
) -> None:
    """Callsites and persisted schemas reject overwrite, blind selectors, and target injection."""
    guarded = (
        SOURCE_ROOT / "flow" / "generation.py",
        SOURCE_ROOT / "flow" / "generation_locators.py",
        SOURCE_ROOT / "flow" / "artifacts.py",
        SOURCE_ROOT / "images" / "flow_handler.py",
        SOURCE_ROOT / "images" / "service.py",
        SOURCE_ROOT / "images" / "repository.py",
    )
    for path in guarded:
        tree = _module_tree(path)
        assert _overwrite_findings(tree) == set()
        assert _unsafe_locator_findings(tree) == set()

    for callable_object in (
        ImageService.for_database,
        FlowImageGenerateHandler,
        FlowGenerationRuntime,
    ):
        for parameter in inspect.signature(callable_object).parameters.values():
            if parameter.name in {
                "target",
                "flow_url",
                "runtime_factory",
                "recovery_runtime_factory",
                "workspace_factory",
                "locator_target",
            } or parameter.name.endswith("_target"):
                assert parameter.name.startswith("_")

    request_fields = set(ImageGenerateRequest.model_json_schema()["properties"])
    job_fields = set(JobSubmit.model_json_schema()["properties"])
    assert not any(_is_target_injection_name(field) for field in request_fields | job_fields)
    assert _job_submit_injection_findings(_module_tree(SOURCE_ROOT / "images" / "service.py")) == set()

    help_result = CliRunner().invoke(app, ["image", "generate", "--help"])
    assert help_result.exit_code == 0
    normalized_help = help_result.stdout.casefold().replace("_", "-")
    assert all(
        option not in normalized_help
        for option in ("--flow-url", "--target", "--runtime-factory", "--locator-target")
    )

    database, work_root, _generation_id, job_id = _create_sensitive_generation(tmp_path)
    service = ImageService.for_database(database, work_root=work_root)
    try:
        job_input = service._jobs.get_job(job_id).input
        assert set(job_input) == {"imageRequestFingerprint"}
        fingerprint = job_input["imageRequestFingerprint"]
        assert isinstance(fingerprint, str) and len(fingerprint) == 64
        int(fingerprint, 16)
    finally:
        service.close()

    aliased_overwrite = ast.parse(
        """
import os as disk
from pathlib import Path as ArtifactPath
def unsafe(path: ArtifactPath):
    path.write_bytes(b'replaced')
    path.open(mode='wb')
    disk.open(path, disk.O_WRONLY | disk.O_CREAT | disk.O_TRUNC)
    disk.replace(path, path)
"""
    )
    overwrite_findings = _overwrite_findings(aliased_overwrite)
    assert any(finding.endswith("write_bytes") for finding in overwrite_findings)
    assert any(finding.endswith("open:wb") for finding in overwrite_findings)
    assert any("O_TRUNC" in finding for finding in overwrite_findings)
    assert "os.replace" in overwrite_findings

    unsafe_locators = ast.parse(
        """
def unsafe(page):
    candidate = page.locator('xpath=//button').nth(0)
    candidate.click(position={'x': 1, 'y': 2})
"""
    )
    locator_findings = _unsafe_locator_findings(unsafe_locators)
    assert {"page.locator", "candidate.click"}.issubset(locator_findings)
    assert any(finding.endswith("nth") for finding in locator_findings)

    aliased_job_target = ast.parse(
        """
from auraly_pipeline.jobs.domain import JobSubmit as Submit
def unsafe():
    return Submit(job_type='image.generate', input={'flowUrl': 'https://attacker.invalid'})
"""
    )
    assert _job_submit_injection_findings(aliased_job_target) == {"flowUrl"}

    unsafe_environment_target = ast.parse(
        """
def unsafe(environment):
    return environment['AURALY_FLOW_TARGET_URL']
"""
    )
    assert _environment_target_findings(unsafe_environment_target) == {
        "AURALY_FLOW_TARGET_URL"
    }
