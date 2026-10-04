from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator
import hashlib
import errno
import json
from typing import Any, cast

from fastapi import FastAPI
from PIL import Image
from pydantic import JsonValue, ValidationError
import pytest

from auraly_pipeline.api.contracts import ApiSettings
from auraly_pipeline.jobs.domain import JobSubmit, RetrySafety
from tests.api_helpers import create_api_fixture
from tests.test_api_http import client_for
from tests.test_api_operations import commands, count_images, request
from tests.test_image_import_batch import _manifest


def test_manifest_operation_enqueues_without_writing_and_replays(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    campaign = service.campaigns.get_campaign("campaign-one")
    service.images.prepare_directory("campaign-one", settings.work_root / "inbox")
    with client_for(settings) as client:
        relative = (settings.work_root / "inbox").relative_to(settings.project_root).as_posix()
        body = {"campaignId": "campaign-one", "directoryPath": relative,
                "items": [{"variantId": campaign.scene_variants[0].variant_id, "path": "images/a.png"}]}
        url = "/api/v1/campaigns/campaign-one/images/import/manifests"
        response = client.post(url, json=body)
        assert response.status_code == 202, response.text
        job_id = response.json()["jobId"]
        assert list((settings.work_root / "inbox").glob("image-import-*.json")) == []
        app = cast(FastAPI, client.app)
        job = app.state.commands.jobs.get_job(job_id)
        assert job.max_attempts == 1 and job.retry_safety == "manual_only"
        app.state.commands.jobs.worker_once("test-worker", campaign_id="campaign-one", job_type="api.local.operation")
        view = client.get(f"/api/v1/campaigns/campaign-one/operations/{job_id}").json()
        assert view["status"] == "completed"
        assert view["result"]["operation"] == "image_manifest"
        assert view["result"]["manifestPath"].startswith(relative + "/image-import-")
        assert str(tmp_path) not in str(view)
        assert client.post(url, json=body).json()["jobId"] == job_id
        assert client.post(url, json=body | {"campaignId": "other"}).status_code == 422
    service._engine.dispose()


@pytest.mark.parametrize("directory", ["../outside", "C:/outside", "/outside", "other-root/inbox"])
def test_manifest_submission_rejects_untrusted_directory(tmp_path: Path, directory: str) -> None:
    settings = create_api_fixture(tmp_path)
    with client_for(settings) as client:
        response = client.post("/api/v1/campaigns/campaign-one/images/import/manifests", json={
            "campaignId": "campaign-one", "directoryPath": directory,
            "items": [{"variantId": "laundromat", "path": "images/a.png"}],
        })
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert client.get("/api/v1/campaigns/campaign-one/jobs").json()["items"] == []


@pytest.fixture
def image_case(tmp_path: Path) -> Iterator[tuple[ApiSettings, Any, Path, str]]:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    variant = service.campaigns.get_campaign("campaign-one").scene_variants[0].variant_id
    manifest = _manifest(tmp_path / "manual", "campaign-one", [variant])
    payload = json.loads(manifest.read_bytes())
    payload.update(approveImported=False, approvedBy=None)
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        yield settings, service, manifest, variant
    finally:
        service._engine.dispose()


def completed(service: Any, operation: Any) -> Any:
    submitted = service.submit_operation(operation)
    service.jobs.worker_once("test-worker", campaign_id="campaign-one", job_type="api.local.operation")
    return service.get_operation("campaign-one", submitted.job_id)


def diagnostic_request(manifest: Path, validation_id: str = "check-one") -> Any:
    return request(operation="image_import", campaignId="campaign-one", manifestPath=str(manifest),
                   mode="dry_run", includeDiagnostics=True, validationId=validation_id)


def test_diagnostic_dry_run_reports_facts_without_writes(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    settings, service, manifest, variant = image_case
    view = completed(service, diagnostic_request(manifest))
    dry = view.result
    assert view.status == "completed" and dry.valid is True
    assert dry.created == dry.reused == dry.approved == 0 and dry.total == 1
    assert dry.validation_id == "check-one"
    assert dry.items[0].variant_id == variant
    assert dry.items[0].width == 360 and dry.items[0].height == 640
    assert dry.items[0].format == "png" and dry.items[0].size_bytes > 0
    assert dry.items[0].sha256 == hashlib.sha256((manifest.parent / f"images/{variant}.png").read_bytes()).hexdigest()
    assert dry.issues == [] and count_images(settings.database) == 0


@pytest.mark.parametrize("problem", ["missing", "landscape", "unknown", "duplicate"])
def test_invalid_diagnostic_is_completed_but_not_valid(
    image_case: tuple[ApiSettings, Any, Path, str], problem: str,
) -> None:
    settings, service, manifest, variant = image_case
    image = manifest.parent / f"images/{variant}.png"
    payload = json.loads(manifest.read_bytes())
    if problem == "missing":
        image.unlink()
    elif problem == "landscape":
        Image.new("RGB", (640, 360)).save(image)
    elif problem == "unknown":
        payload["items"][0]["variantId"] = "unknown"
    else:
        payload["items"].append(payload["items"][0])
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    view = completed(service, diagnostic_request(manifest))
    assert view.status == "completed" and view.result.valid is False
    assert view.result.items == [] and view.result.issues
    assert view.result.created == view.result.reused == view.result.approved == 0
    assert count_images(settings.database) == 0
    assert str(settings.project_root) not in view.model_dump_json(by_alias=True)
    assert "message" not in view.result.issues[0].model_dump()


def test_legacy_invalid_import_still_fails(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    settings, service, manifest, variant = image_case
    (manifest.parent / f"images/{variant}.png").unlink()
    view = completed(service, request(operation="image_import", campaignId="campaign-one",
                                      manifestPath=str(manifest), mode="dry_run"))
    assert view.status == "failed" and view.result is None and count_images(settings.database) == 0


def test_validation_id_refreshes_file_facts(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    _, service, manifest, variant = image_case
    original = diagnostic_request(manifest)
    first = completed(service, original)
    Image.new("RGB", (360, 640), (200, 20, 40)).save(manifest.parent / f"images/{variant}.png")
    assert service.submit_operation(original).job_id == first.job_id
    second = completed(service, diagnostic_request(manifest, "check-two"))
    assert second.job_id != first.job_id
    assert first.result.items[0].sha256 != second.result.items[0].sha256
    assert first.result.manifest_sha256 == second.result.manifest_sha256


@pytest.mark.parametrize("change_after_queue", [False, True])
def test_snapshot_blocks_changed_images(
    image_case: tuple[ApiSettings, Any, Path, str], change_after_queue: bool,
) -> None:
    settings, service, manifest, variant = image_case
    dry = completed(service, diagnostic_request(manifest)).result
    execute = request(operation="image_import", campaignId="campaign-one", manifestPath=str(manifest),
                      mode="execute", manifestSha256=dry.manifest_sha256,
                      expectedSources=[{"variantId": variant, "sha256": dry.items[0].sha256}])
    submitted = service.submit_operation(execute) if change_after_queue else None
    Image.new("RGB", (360, 640), (40, 20, 200)).save(manifest.parent / f"images/{variant}.png")
    if submitted is None:
        view = completed(service, execute)
    else:
        service.jobs.worker_once("test-worker", campaign_id="campaign-one", job_type="api.local.operation")
        view = service.get_operation("campaign-one", submitted.job_id)
    assert view.status == "failed" and view.error_code == "artifact_invalid"
    assert count_images(settings.database) == 0


def test_valid_snapshot_imports_pending_images_and_replays(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    settings, service, manifest, variant = image_case
    dry = completed(service, diagnostic_request(manifest)).result
    operation = request(operation="image_import", campaignId="campaign-one", manifestPath=str(manifest),
                        mode="execute", manifestSha256=dry.manifest_sha256,
                        expectedSources=[{"variantId": variant, "sha256": dry.items[0].sha256}])
    view = completed(service, operation)
    assert view.status == "completed" and view.result.created == 1 and view.result.approved == 0
    candidate = service.image_review.get_candidate(view.result.items[0].image_candidate_id)
    assert candidate.review_status == "pending_review"
    assert service.submit_operation(operation).job_id == view.job_id and count_images(settings.database) == 1


def test_manifest_digest_uses_raw_bytes(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    _, service, manifest, _ = image_case
    dry = completed(service, diagnostic_request(manifest)).result
    assert dry.manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert dry.manifest_sha256 != service.images.plan(manifest).manifest_sha256


def test_legacy_job_identity_survives_new_defaults(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    settings, service, manifest, _ = image_case
    payload: dict[str, JsonValue] = {"operation": "image_import", "campaignId": "campaign-one", "mode": "dry_run",
               "manifestPath": manifest.relative_to(settings.project_root).as_posix(),
               "manifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
    identity = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    old = service.jobs.submit_job(JobSubmit(campaign_id="campaign-one", job_type="api.local.operation",
                                           idempotency_key=f"api.local.operation:{identity}", input=payload,
                                           max_attempts=1, retry_safety=RetrySafety.MANUAL_ONLY))
    assert service.submit_operation(request(**payload)).job_id == old.job_id


@pytest.mark.parametrize("extra", [
    {"expectedSources": []},
    {"expectedSources": [{"variantId": "laundromat", "sha256": "bad"}]},
    {"expectedSources": [{"variantId": "laundromat", "sha256": "f" * 64}] * 2},
    {"includeDiagnostics": True}, {"validationId": "http://private.invalid/token"},
])
def test_snapshot_and_diagnostic_contracts_reject_invalid_combinations(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        request(operation="image_import", campaignId="campaign-one", manifestPath="manual/batch.json",
                mode="execute", **extra)


def test_snapshot_requires_exact_variant_set(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    settings, service, manifest, _ = image_case
    view = completed(service, request(operation="image_import", campaignId="campaign-one",
                                      manifestPath=str(manifest), mode="execute",
                                      expectedSources=[{"variantId": "unknown", "sha256": "f" * 64}]))
    assert view.status == "failed" and count_images(settings.database) == 0


def test_diagnostic_io_failure_remains_failed(
    image_case: tuple[ApiSettings, Any, Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, service, manifest, _ = image_case

    def failing(*args: Any, **kwargs: Any) -> Any:
        raise OSError("private storage detail")

    monkeypatch.setattr(service.images, "plan", failing)
    view = completed(service, diagnostic_request(manifest))
    assert view.status == "failed" and view.result is None and count_images(settings.database) == 0
    assert "private storage" not in view.model_dump_json(by_alias=True)


@pytest.mark.parametrize("failure", [errno.EIO, errno.EACCES])
def test_diagnostic_actual_image_open_failure_remains_failed(
    image_case: tuple[ApiSettings, Any, Path, str], monkeypatch: pytest.MonkeyPatch, failure: int,
) -> None:
    import auraly_pipeline.flow.artifacts as artifacts

    settings, service, manifest, variant = image_case
    original = artifacts.os.open
    source = manifest.parent / f"images/{variant}.png"

    def failing(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path).replace("\\\\?\\", "") == str(source):
            raise OSError(failure, "private storage detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "open", failing)
    view = completed(service, diagnostic_request(manifest))
    assert view.status == "failed" and view.result is None and count_images(settings.database) == 0
    assert "private storage" not in view.model_dump_json(by_alias=True)


def test_prepare_result_binds_the_exact_work_root_relative_output(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    service = commands(settings)
    try:
        view = completed(service, request(operation="image_prepare", campaignId="campaign-one", outputPath="nested/inbox"))
        assert view.result.output_path == "nested/inbox"
        assert view.result.manifest_path == (settings.work_root / "nested/inbox/image-import.json").relative_to(settings.project_root).as_posix()
    finally:
        service._engine.dispose()


def test_diagnostic_approved_candidate_conflict_is_explicit(image_case: tuple[ApiSettings, Any, Path, str]) -> None:
    _, service, manifest, variant = image_case
    imported = completed(service, request(operation="image_import", campaignId="campaign-one",
                                          manifestPath=str(manifest), mode="execute"))
    candidate_id = imported.result.items[0].image_candidate_id
    service.image_review.approve_candidate(candidate_id, "rafael")
    Image.new("RGB", (360, 640), (200, 20, 40)).save(manifest.parent / f"images/{variant}.png")
    view = completed(service, diagnostic_request(manifest))
    assert view.result.valid is False
    assert any(issue.code == "image_import_approved_candidate_conflict" for issue in view.result.issues)
    assert service.image_review.get_candidate(candidate_id).review_status == "approved"


def test_diagnostic_unknown_issue_is_sanitized(
    image_case: tuple[ApiSettings, Any, Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auraly_pipeline.images.import_batch import ImageImportIssue, ImageImportValidationError

    _, service, manifest, _ = image_case

    def failing(*args: Any, **kwargs: Any) -> Any:
        raise ImageImportValidationError([ImageImportIssue(code="private-secret-code", message="private-secret-text")])

    monkeypatch.setattr(service.images, "plan", failing)
    view = completed(service, diagnostic_request(manifest))
    assert view.result.valid is False and view.result.issues[0].code == "image_validation_failed"
    assert "private-secret" not in view.model_dump_json(by_alias=True)
