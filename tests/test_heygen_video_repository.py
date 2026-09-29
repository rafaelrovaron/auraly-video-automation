from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.heygen.video_repository import HeyGenVideoRepository
from auraly_pipeline.jobs.domain import JobSubmit
from auraly_pipeline.jobs.service import JobIdempotencyConflictError, JobService
from tests.heygen_support import create_ready_campaign
from tests.test_heygen_video_domain import video_item


def test_batch_budget_race(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    create_ready_campaign(db, tmp_path / "work")
    engine = create_sqlite_engine(db)
    repo = HeyGenVideoRepository(sessionmaker(engine, expire_on_commit=False))
    service = JobService.for_database(db)
    items = [
        video_item(
            scene_variant_id=f"20000000-0000-4000-8000-00000000000{i}",
            image_candidate_id=f"30000000-0000-4000-8000-00000000000{i}",
        )
        for i in range(3)
    ]
    requests = [
        JobSubmit(
            job_type="fake.success",
            campaign_id="campaign-one",
            scene_variant_id=i.scene_variant_id,
            idempotency_key=f"batch-{n}",
            input={"index": n},
        )
        for n, i in enumerate(items)
    ]

    def submit(limit: int) -> list[str]:
        def create(session: Session, row: object) -> object:
            from auraly_pipeline.jobs.db_models import JobRow

            assert isinstance(row, JobRow)
            return repo.create_in_session(
                session,
                row,
                items[int(str(row.input_json["index"]))],
                max_paid_renders=limit,
                approved_by="tester",
            )

        result = service.submit_linked_batch(
            requests,
            create,
            lambda job: repo.list_campaign("campaign-one")[int(str(job.input["index"]))],
            before_commit=lambda s: repo.check_budget_in_session(s, "campaign-one", limit),
        )
        return [r.job.job_id for r in result]

    with pytest.raises(ValueError):
        submit(2)
    assert repo.list_campaign("campaign-one") == []
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [3, 3]))
    assert results[0] == results[1]
    assert len(repo.list_campaign("campaign-one")) == 3
    assert sum(len(service.list_jobs(scene_variant_id=i.scene_variant_id)) for i in items) == 3
    assert submit(3) == results[0]
    renders = repo.list_campaign("campaign-one")
    repo.record_video(renders[0].render_id, "remote-one")
    with pytest.raises(ValueError):
        repo.record_video(renders[1].render_id, "remote-one")
    assert "heygen_renders" in inspect(engine).get_table_names()
    service.close()
    engine.dispose()


def test_batch_rolls_back_on_conflict(tmp_path: Path) -> None:
    service = JobService.for_database(tmp_path / "jobs.db")
    original = JobSubmit(job_type="fake.success", idempotency_key="same", input={})
    service.submit_job(original)
    fresh = JobSubmit(job_type="fake.success", idempotency_key="new", input={})
    conflict = original.model_copy(update={"input": {"different": True}})
    with pytest.raises(JobIdempotencyConflictError):
        service.submit_linked_batch(
            [fresh, conflict], lambda s, j: j.id, lambda j: j.job_id, before_commit=lambda s: None
        )
    assert len(service.list_jobs()) == 1
    service.close()
