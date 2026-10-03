from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.campaigns.persistence import create_existing_sqlite_engine
from auraly_pipeline.campaigns.service import CampaignService
from tests.api_helpers import create_api_fixture, create_ready_api_fixture
from tests.test_image_import_batch import _manifest

pytest_plugins = ["tests.test_heygen_video_media"]


def commands(settings: Any) -> Any:
    assert importlib.util.find_spec("auraly_pipeline.api.commands"), "commands not implemented"
    module = importlib.import_module("auraly_pipeline.api.commands")
    return module.ApiCommands(settings, create_existing_sqlite_engine(settings.database))


def request(**data: Any) -> Any:
    module = importlib.import_module("auraly_pipeline.api.action_contracts")
    return TypeAdapter(module.LocalOperationRequest).validate_python(data)


def count_images(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute("SELECT count(*) FROM image_candidates").fetchone()[0])


def test_prepare_queues_and_replays_without_files_until_worker(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    operation = request(operation="image_prepare", campaignId="campaign-one", outputPath="inbox")
    first = service.submit_operation(operation)
    assert first == service.submit_operation(operation)
    assert not (settings.work_root / "inbox").exists()
    assert service.get_operation("campaign-one", first.job_id).result is None
    job = service.jobs.get_job(first.job_id)
    assert job.job_type == "api.local.operation"
    assert job.max_attempts == 1 and job.retry_safety == "manual_only"
    assert service.jobs.worker_once("local-worker", campaign_id="campaign-one",
                                    job_type="api.local.operation").status == "completed"
    view = service.get_operation("campaign-one", first.job_id)
    assert view.result.operation == "image_prepare"
    assert view.result.variant_count == 1
    assert (settings.work_root / "inbox" / "image-import.json").is_file()
    assert str(tmp_path) not in view.model_dump_json(by_alias=True)


def test_import_dry_run_then_execute_reuses_candidates_and_safe_results(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    campaign = CampaignService(create_existing_sqlite_engine(settings.database)).get_campaign("campaign-one")
    manifest = _manifest(tmp_path / "manual", "campaign-one",
                         [variant.variant_id for variant in campaign.scene_variants])
    dry = request(operation="image_import", campaignId="campaign-one",
                  manifestPath=str(manifest), mode="dry_run")
    dry_submission = service.submit_operation(dry)
    assert count_images(settings.database) == 0
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    dry_view = service.get_operation("campaign-one", dry_submission.job_id)
    assert dry_view.result.operation == "image_import" and dry_view.result.total == 1
    assert count_images(settings.database) == 0
    execute = request(operation="image_import", campaignId="campaign-one",
                      manifestPath=str(manifest), mode="execute")
    submission = service.submit_operation(execute)
    assert submission.job_id != dry_submission.job_id
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    view = service.get_operation("campaign-one", submission.job_id)
    assert view.status == "completed" and view.result.created == 1
    assert count_images(settings.database) == 1
    assert service.submit_operation(execute).job_id == submission.job_id
    assert "importSourcePath" not in view.model_dump_json(by_alias=True)
    assert str(tmp_path) not in view.model_dump_json(by_alias=True)


def test_changed_queued_manifest_fails_without_importing(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    campaign_service = CampaignService(create_existing_sqlite_engine(settings.database))
    campaign = campaign_service.get_campaign("campaign-one")
    campaign_service.close()
    manifest = _manifest(tmp_path / "manual", "campaign-one",
                         [variant.variant_id for variant in campaign.scene_variants])
    operation = request(operation="image_import", campaignId="campaign-one",
                        manifestPath=str(manifest), mode="execute")
    first = service.submit_operation(operation)
    manifest.write_text(manifest.read_text() + "\n", encoding="utf-8")
    second = service.submit_operation(operation)
    assert first.job_id != second.job_id
    # Only claim the earlier request.
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    view = service.get_operation("campaign-one", first.job_id)
    assert view.status == "failed" and view.result is None
    assert view.error_code == "artifact_invalid"
    assert count_images(settings.database) == 0


def test_unknown_operation_foreign_campaign_and_manifest_root_rejected(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    with pytest.raises(ValidationError):
        request(operation="arbitrary_command", campaignId="campaign-one")
    with pytest.raises(QueryError) as missing:
        service.submit_operation(request(
            operation="image_prepare", campaignId="campaign-two", outputPath="inbox",
        ))
    assert missing.value.code == "not_found"
    manifest = tmp_path / "image-import.json"
    manifest.write_text(json.dumps({
        "schemaVersion": "1.0", "campaignId": "campaign-one", "sourceRoot": str(tmp_path.parent),
        "items": [{"variantId": "v1", "path": "images/a.png"}],
    }), encoding="utf-8")
    with pytest.raises(QueryError):
        service.submit_operation(request(
            operation="image_import", campaignId="campaign-one", manifestPath=str(manifest),
            mode="execute",
        ))
    assert service.jobs.list_jobs(campaign_id="campaign-one") == []
    assert count_images(settings.database) == 0


def test_prepare_rejects_reserved_filename_before_job_submission(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    with pytest.raises(QueryError):
        service.submit_operation(request(
            operation="image_prepare", campaignId="campaign-one",
            outputPath=str(settings.work_root / "CON"),
        ))
    assert service.jobs.list_jobs(campaign_id="campaign-one") == []


def test_corrupt_preparation_result_cannot_expose_absolute_paths(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    submission = service.submit_operation(request(
        operation="image_prepare", campaignId="campaign-one", outputPath="inbox",
    ))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    with sqlite3.connect(settings.database) as connection:
        connection.execute("UPDATE jobs SET output_json=? WHERE id=?", (json.dumps({
            "operation": "image_prepare", "manifestPath": str(tmp_path / "private.json"),
            "imagesPath": str(tmp_path), "variantCount": 1,
        }), submission.job_id))
    with pytest.raises(QueryError) as error:
        service.get_operation("campaign-one", submission.job_id)
    assert error.value.code == "artifact_invalid"
    assert str(tmp_path) not in str(error.value)


def test_manifest_outside_root_or_symlink_rejected_before_enqueue(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    with pytest.raises(QueryError):
        service.submit_operation(request(
            operation="image_import", campaignId="campaign-one",
            manifestPath=str(tmp_path.parent / "foreign.json"), mode="execute",
        ))
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlinks")
    with pytest.raises(QueryError):
        service.submit_operation(request(
            operation="image_import", campaignId="campaign-one",
            manifestPath=str(linked / "image-import.json"), mode="execute",
        ))
    assert service.jobs.list_jobs(campaign_id="campaign-one") == []


def test_editorial_dry_run_and_persisted_plan_keep_pinned_identity(tmp_path: Path, mp4: bytes) -> None:
    settings, edit_request = create_ready_api_fixture(tmp_path, mp4)
    service = commands(settings)
    dry = service.submit_operation(request(
        operation="edit_plan", campaignId="campaign-one",
        request=edit_request.model_dump(mode="json", by_alias=True), persist=False,
    ))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    view = service.get_operation("campaign-one", dry.job_id)
    assert view.status == "completed"
    assert view.result.plan.source.id == edit_request.render_id
    assert not list(settings.work_root.glob("campaigns/*/editing/plans/*/*/plan.json"))
    persisted = service.submit_operation(request(
        operation="edit_plan", campaignId="campaign-one",
        request=edit_request.model_dump(mode="json", by_alias=True), persist=True,
    ))
    service.jobs.worker_once("local-worker", campaign_id="campaign-one", job_type="api.local.operation")
    final = service.get_operation("campaign-one", persisted.job_id)
    assert final.status == "completed"
    assert final.result.plan.plan_hash == view.result.plan.plan_hash
    assert len(list(settings.work_root.glob("campaigns/*/editing/plans/*/*/plan.json"))) == 1
    with pytest.raises(QueryError):
        service.get_operation("campaign-two", persisted.job_id)
