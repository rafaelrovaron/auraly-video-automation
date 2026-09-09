from __future__ import annotations

import ast
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.images.domain import FlowCandidateSlot, FlowGenerationRun, ImageCandidate
from auraly_pipeline.images.flow_repository import (
    FlowCheckpointConflictError,
    FlowCheckpointRepository,
)


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
SCENE_ID = "11111111-1111-4111-8111-111111111111"
GENERATION_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"


def _sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, class_=Session)


def _seed_database(tmp_path: Path) -> tuple[Engine, sessionmaker[Session]]:
    database = tmp_path / "auraly.db"
    migrate_database(database)
    engine = create_sqlite_engine(database)
    sessions = _sessions(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO campaigns (id,character,proof_object,voice_preset,edit_preset,"
                "budget_json,config_json,status,created_at,updated_at) VALUES "
                "('campaign-1','character','proof','voice','edit','{}','{}','draft',:now,:now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO scene_variants (id,campaign_id,variant_id,location,time_atmosphere,"
                "action,prompt,proof_object,status,created_at,updated_at) VALUES "
                "(:scene,'campaign-1','scene-1','studio',NULL,'pose','prompt',NULL,'not_started',"
                ":now,:now)"
            ),
            {"scene": SCENE_ID, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id,job_type,campaign_id,scene_variant_id,status,priority,"
                "idempotency_key,request_fingerprint,input_json,attempt_count,max_attempts,"
                "retry_safety,created_at,updated_at,queued_at) VALUES "
                "(:job,'image.generate','campaign-1',:scene,'queued',0,'flow-job',:sha,'{}',0,3,"
                "'reconcile_before_retry',:now,:now,:now)"
            ),
            {"job": JOB_ID, "scene": SCENE_ID, "sha": "a" * 64, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO image_generations (id,campaign_id,scene_variant_id,job_id,"
                "generation_number,idempotency_key,request_fingerprint,prompt_snapshot,"
                "prompt_sha256,reference_image_path,reference_image_sha256,provider,executor,"
                "provider_state,created_at,updated_at) VALUES "
                "(:generation,'campaign-1',:scene,:job,1,'flow-generation',:request_sha,'prompt',"
                ":prompt_sha,'refs/reference.png',:reference_sha,'google_flow',"
                "'playwright_python','generating',:now,:now)"
            ),
            {
                "generation": GENERATION_ID,
                "scene": SCENE_ID,
                "job": JOB_ID,
                "request_sha": "b" * 64,
                "prompt_sha": "c" * 64,
                "reference_sha": "d" * 64,
                "now": NOW,
            },
        )
    return engine, sessions


def _run(*, generation_id: str = GENERATION_ID, run_id: str = RUN_ID) -> FlowGenerationRun:
    return FlowGenerationRun(
        flow_generation_run_id=run_id,
        image_generation_id=generation_id,
        stage="prepared",
        provider_workspace_path="fx/tools/flow/local-workspace",
        provider_workspace_fingerprint="e" * 64,
        provider_action_approved_by="operator-1",
        provider_action_approved_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _slot(index: int, *, run_id: str = RUN_ID) -> FlowCandidateSlot:
    return FlowCandidateSlot(
        flow_candidate_slot_id=str(uuid4()),
        flow_generation_run_id=run_id,
        slot_index=index,
        state="pending",
        created_at=NOW,
        updated_at=NOW,
    )


def _candidate(
    index: int,
    *,
    generation_id: str = GENERATION_ID,
    candidate_id: str | None = None,
) -> ImageCandidate:
    return ImageCandidate(
        image_candidate_id=candidate_id or str(uuid4()),
        image_generation_id=generation_id,
        candidate_index=index,
        source_path=f"campaigns/campaign-1/images/generation-0001/candidate-{index:04d}.png",
        sha256=f"{index + 1:x}" * 64,
        width=1152,
        height=2048,
        size_bytes=1024 + index,
        format="png",
        review_status="pending_review",
        created_at=NOW + timedelta(seconds=4),
        updated_at=NOW + timedelta(seconds=4),
    )


@pytest.fixture
def checkpoint_repository(
    tmp_path: Path,
) -> Iterator[tuple[Engine, FlowCheckpointRepository]]:
    engine, sessions = _seed_database(tmp_path)
    repository = FlowCheckpointRepository(sessions)
    with sessions() as session:
        repository.create_run_in_session(session, _run(), [_slot(0), _slot(1)])
        session.commit()
    yield engine, repository
    engine.dispose()


def test_create_run_creates_exactly_two_pending_slots(tmp_path: Path) -> None:
    engine, sessions = _seed_database(tmp_path)
    repository = FlowCheckpointRepository(sessions)
    with sessions() as session:
        repository.create_run_in_session(session, _run(), [_slot(0), _slot(1)])
        session.commit()

    assert (run := repository.get_run(RUN_ID)) is not None
    assert run.stage == "prepared"
    assert [(slot.slot_index, slot.state) for slot in repository.list_slots(RUN_ID)] == [
        (0, "pending"),
        (1, "pending"),
    ]
    engine.dispose()


@pytest.mark.parametrize(
    "slots",
    [
        [_slot(0)],
        [_slot(0), _slot(1), FlowCandidateSlot.model_copy(_slot(1), update={"slot_index": 2})],
        [_slot(0), _slot(0)],
    ],
    ids=["missing-slot", "third-slot", "duplicate-index"],
)
def test_create_run_rolls_back_invalid_slot_sets(
    tmp_path: Path, slots: list[FlowCandidateSlot]
) -> None:
    engine, sessions = _seed_database(tmp_path)
    repository = FlowCheckpointRepository(sessions)
    with sessions() as session, pytest.raises(FlowCheckpointConflictError):
        repository.create_run_in_session(session, _run(), slots)
        session.commit()

    assert repository.get_run(RUN_ID) is None
    assert repository.list_slots(RUN_ID) == []
    engine.dispose()


def test_create_run_rejects_generation_ownership_mismatch(tmp_path: Path) -> None:
    engine, sessions = _seed_database(tmp_path)
    repository = FlowCheckpointRepository(sessions)
    with sessions() as session, pytest.raises(FlowCheckpointConflictError):
        repository.create_run_in_session(
            session,
            _run(generation_id=str(uuid4())),
            [_slot(0), _slot(1)],
        )
        session.commit()

    assert repository.get_run(RUN_ID) is None
    engine.dispose()


def test_run_transition_uses_expected_stage(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository],
) -> None:
    _engine, repository = checkpoint_repository
    transitioned = repository.transition_run(
        RUN_ID,
        expected_stage="prepared",
        target_stage="inputs_verified",
        now=NOW + timedelta(seconds=1),
    )
    assert transitioned.stage == "inputs_verified"

    with pytest.raises(FlowCheckpointConflictError):
        repository.transition_run(
            RUN_ID,
            expected_stage="prepared",
            target_stage="inputs_verified",
            now=NOW + timedelta(seconds=2),
        )
    assert (persisted := repository.get_run(RUN_ID)) is not None
    assert persisted.stage == "inputs_verified"


def test_slot_transition_uses_expected_state_and_fingerprint(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository],
) -> None:
    _engine, repository = checkpoint_repository
    fingerprint = "f" * 64
    observed = repository.transition_slot(
        RUN_ID,
        0,
        expected_state="pending",
        target_state="observed",
        now=NOW + timedelta(seconds=1),
        updates={"provider_slot_fingerprint": fingerprint},
    )
    assert observed.state == "observed"

    with pytest.raises(FlowCheckpointConflictError):
        repository.transition_slot(
            RUN_ID,
            0,
            expected_state="observed",
            target_state="download_intent_recorded",
            expected_fingerprint="0" * 64,
            now=NOW + timedelta(seconds=2),
            updates={"download_intent_at": NOW + timedelta(seconds=2)},
        )
    assert (persisted := repository.get_slot(RUN_ID, 0)) is not None
    assert persisted.state == "observed"


def test_stale_slot_expected_state_is_rejected(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository],
) -> None:
    _engine, repository = checkpoint_repository
    with pytest.raises(FlowCheckpointConflictError):
        repository.transition_slot(
            RUN_ID,
            1,
            expected_state="observed",
            target_state="download_intent_recorded",
            now=NOW + timedelta(seconds=1),
        )
    assert (persisted := repository.get_slot(RUN_ID, 1)) is not None
    assert persisted.state == "pending"


def test_dispatch_confirmation_updates_run_and_generation_atomically(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository],
) -> None:
    engine, repository = checkpoint_repository
    repository.transition_run(
        RUN_ID,
        expected_stage="prepared",
        target_stage="inputs_verified",
        now=NOW + timedelta(seconds=1),
    )
    intent_at = NOW + timedelta(seconds=2)
    repository.transition_run(
        RUN_ID,
        expected_stage="inputs_verified",
        target_stage="dispatch_intent_recorded",
        now=intent_at,
        updates={"dispatch_intent_at": intent_at},
    )
    confirmed_at = NOW + timedelta(seconds=3)
    repository.confirm_dispatch(
        RUN_ID,
        expected_stage="dispatch_intent_recorded",
        now=confirmed_at,
    )

    assert (persisted := repository.get_run(RUN_ID)) is not None
    assert persisted.dispatch_confirmed_at == confirmed_at.replace(tzinfo=None)
    with engine.connect() as connection:
        dispatched_at = connection.execute(
            text("SELECT dispatched_at FROM image_generations WHERE id = :id"),
            {"id": GENERATION_ID},
        ).scalar_one()
    assert datetime.fromisoformat(dispatched_at) == confirmed_at.replace(tzinfo=None)


def _downloaded_slot(repository: FlowCheckpointRepository, index: int) -> None:
    run = repository.get_run(RUN_ID)
    assert run is not None
    if run.stage == "prepared":
        repository.transition_run(
            RUN_ID,
            expected_stage="prepared",
            target_stage="inputs_verified",
            now=NOW + timedelta(seconds=1),
        )
        dispatch_intent_at = NOW + timedelta(seconds=2)
        repository.transition_run(
            RUN_ID,
            expected_stage="inputs_verified",
            target_stage="dispatch_intent_recorded",
            now=dispatch_intent_at,
            updates={"dispatch_intent_at": dispatch_intent_at},
        )
        repository.confirm_dispatch(
            RUN_ID,
            expected_stage="dispatch_intent_recorded",
            now=NOW + timedelta(seconds=3),
        )
        repository.transition_run(
            RUN_ID,
            expected_stage="dispatch_confirmed",
            target_stage="candidates_observed",
            now=NOW + timedelta(seconds=4),
            updates={
                "grid_evidence_path": "inspection/grid.png",
                "grid_evidence_sha256": "9" * 64,
            },
        )
    fingerprint = f"{index + 7:x}" * 64
    repository.transition_slot(
        RUN_ID,
        index,
        expected_state="pending",
        target_state="observed",
        now=NOW + timedelta(seconds=1),
        updates={"provider_slot_fingerprint": fingerprint},
    )
    repository.record_download_intent(
        RUN_ID,
        index,
        expected_state="observed",
        expected_fingerprint=fingerprint,
        now=NOW + timedelta(seconds=2),
    )
    repository.record_downloaded(
        RUN_ID,
        index,
        expected_state="download_intent_recorded",
        expected_fingerprint=fingerprint,
        relative_path=f"campaigns/campaign-1/images/.staging/candidate-{index}.part",
        sha256=f"{index + 1:x}" * 64,
        now=NOW + timedelta(seconds=3),
    )


def test_download_intent_and_downloaded_facts_are_persisted(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository],
) -> None:
    _engine, repository = checkpoint_repository
    repository.transition_run(
        RUN_ID,
        expected_stage="prepared",
        target_stage="inputs_verified",
        now=NOW + timedelta(seconds=1),
    )
    intent_at = NOW + timedelta(seconds=2)
    repository.transition_run(
        RUN_ID,
        expected_stage="inputs_verified",
        target_stage="dispatch_intent_recorded",
        now=intent_at,
        updates={"dispatch_intent_at": intent_at},
    )
    repository.confirm_dispatch(
        RUN_ID,
        expected_stage="dispatch_intent_recorded",
        now=NOW + timedelta(seconds=3),
    )
    repository.transition_run(
        RUN_ID,
        expected_stage="dispatch_confirmed",
        target_stage="candidates_observed",
        now=NOW + timedelta(seconds=4),
        updates={
            "grid_evidence_path": "inspection/grid.png",
            "grid_evidence_sha256": "9" * 64,
        },
    )
    fingerprint = "7" * 64
    repository.transition_slot(
        RUN_ID,
        0,
        expected_state="pending",
        target_state="observed",
        now=NOW + timedelta(seconds=4),
        updates={"provider_slot_fingerprint": fingerprint},
    )
    repository.record_download_intent(
        RUN_ID,
        0,
        expected_state="observed",
        expected_fingerprint=fingerprint,
        now=NOW + timedelta(seconds=5),
    )
    intended = repository.get_slot(RUN_ID, 0)
    assert intended is not None
    assert intended.state == "download_intent_recorded"
    assert intended.download_intent_at == (NOW + timedelta(seconds=5)).replace(tzinfo=None)
    assert (persisted_run := repository.get_run(RUN_ID)) is not None
    assert persisted_run.stage == "downloading"

    repository.record_downloaded(
        RUN_ID,
        0,
        expected_state="download_intent_recorded",
        expected_fingerprint=fingerprint,
        relative_path="campaigns/campaign-1/images/.staging/candidate-0.part",
        sha256="1" * 64,
        now=NOW + timedelta(seconds=6),
    )
    downloaded = repository.get_slot(RUN_ID, 0)
    assert downloaded is not None
    assert (downloaded.state, downloaded.staging_path, downloaded.staged_sha256) == (
        "downloaded",
        "campaigns/campaign-1/images/.staging/candidate-0.part",
        "1" * 64,
    )


def test_candidate_ingestion_is_atomic_and_exact_replay_is_idempotent(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository],
) -> None:
    _engine, repository = checkpoint_repository
    _downloaded_slot(repository, 0)
    candidate = _candidate(0)
    ingested = repository.ingest_candidate(
        RUN_ID,
        0,
        expected_state="downloaded",
        expected_staged_sha256=candidate.sha256,
        candidate=candidate,
        now=NOW + timedelta(seconds=4),
    )
    replayed = repository.ingest_candidate(
        RUN_ID,
        0,
        expected_state="downloaded",
        expected_staged_sha256=candidate.sha256,
        candidate=candidate,
        now=NOW + timedelta(seconds=5),
    )

    assert ingested.state == replayed.state == "ingested"
    assert ingested.image_candidate_id == replayed.image_candidate_id == candidate.image_candidate_id
    assert (persisted := repository.get_candidate(candidate.image_candidate_id)) is not None
    assert persisted.sha256 == candidate.sha256


@pytest.mark.parametrize("failure", ["cross-generation", "duplicate-conflict", "stale-state"])
def test_candidate_ingestion_conflicts_leave_no_orphan(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository], failure: str
) -> None:
    _engine, repository = checkpoint_repository
    _downloaded_slot(repository, 0)
    original = _candidate(0)
    if failure == "cross-generation":
        candidate = _candidate(0, generation_id=str(uuid4()))
    elif failure == "stale-state":
        candidate = original
        repository.transition_slot(
            RUN_ID,
            0,
            expected_state="downloaded",
            target_state="blocked",
            now=NOW + timedelta(seconds=4),
        )
    else:
        repository.ingest_candidate(
            RUN_ID,
            0,
            expected_state="downloaded",
            expected_staged_sha256=original.sha256,
            candidate=original,
            now=NOW + timedelta(seconds=4),
        )
        candidate = _candidate(0)

    with pytest.raises(FlowCheckpointConflictError):
        repository.ingest_candidate(
            RUN_ID,
            0,
            expected_state="downloaded",
            expected_staged_sha256=candidate.sha256,
            candidate=candidate,
            now=NOW + timedelta(seconds=5),
        )
    assert repository.get_candidate(candidate.image_candidate_id) is None


def test_complete_requires_exactly_two_ingested_slots(
    checkpoint_repository: tuple[Engine, FlowCheckpointRepository],
) -> None:
    _engine, repository = checkpoint_repository
    for index in (0, 1):
        _downloaded_slot(repository, index)
    first = _candidate(0)
    repository.ingest_candidate(
        RUN_ID,
        0,
        expected_state="downloaded",
        expected_staged_sha256=first.sha256,
        candidate=first,
        now=NOW + timedelta(seconds=4),
    )
    with pytest.raises(FlowCheckpointConflictError):
        repository.complete_generation(
            RUN_ID,
            GENERATION_ID,
            expected_stage="downloading",
            now=NOW + timedelta(seconds=5),
        )

    second = _candidate(1)
    repository.ingest_candidate(
        RUN_ID,
        1,
        expected_state="downloaded",
        expected_staged_sha256=second.sha256,
        candidate=second,
        now=NOW + timedelta(seconds=5),
    )
    repository.complete_generation(
        RUN_ID,
        GENERATION_ID,
        expected_stage="downloading",
        now=NOW + timedelta(seconds=6),
    )
    assert (persisted := repository.get_run(RUN_ID)) is not None
    assert persisted.stage == "completed"


def test_concurrent_run_compare_and_set_allows_one_worker(tmp_path: Path) -> None:
    engine, sessions = _seed_database(tmp_path)
    setup = FlowCheckpointRepository(sessions)
    with sessions() as session:
        setup.create_run_in_session(session, _run(), [_slot(0), _slot(1)])
        session.commit()
    barrier = Barrier(2)

    def transition(_: int) -> str:
        repository = FlowCheckpointRepository(sessions)
        barrier.wait()
        try:
            repository.transition_run(
                RUN_ID,
                expected_stage="prepared",
                target_stage="inputs_verified",
                now=NOW + timedelta(seconds=1),
            )
        except FlowCheckpointConflictError:
            return "stale"
        return "advanced"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(transition, (0, 1)))

    assert sorted(outcomes) == ["advanced", "stale"]
    assert (persisted := setup.get_run(RUN_ID)) is not None
    assert persisted.stage == "inputs_verified"
    engine.dispose()


def test_flow_checkpoint_sql_mutations_are_repository_owned() -> None:
    forbidden_assignments = {
        "stage",
        "provider_state",
        "dispatch_attempt_number",
        "dispatch_intent_at",
        "dispatch_confirmed_at",
        "last_failure_code",
        "provider_slot_fingerprint",
        "download_intent_at",
        "staging_path",
        "staged_sha256",
        "image_candidate_id",
        "completed_at",
        "dispatched_at",
    }
    for relative_path in (
        "src/auraly_pipeline/images/flow_handler.py",
        "src/auraly_pipeline/images/service.py",
    ):
        source = Path(relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert "BEGIN IMMEDIATE" not in source
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "update"
            for node in ast.walk(tree)
        )
        assert not any(
            isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            and any(
                isinstance(target, ast.Attribute) and target.attr in forbidden_assignments
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
            for node in ast.walk(tree)
        )
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"FlowGenerationRunRow", "FlowCandidateSlotRow"}
            for node in ast.walk(tree)
        )
