from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from auraly_pipeline.api.app import create_app
from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.api.render_routes import _chunks
from tests.render_helpers import publish_test_plan, refresh_plan
from tests.render_job_helpers import RenderApiCase, render_case as provide_render_case, run_render  # noqa: F401


def output(case: RenderApiCase, job_id: str) -> tuple[str, Path]:
    view = case.commands.editorial_renders.get(case.plan.campaign_id, job_id)
    assert view.result is not None
    item = view.result.outputs[0]
    assert item.path is not None
    return item.output_variant_id, case.commands.renderer.work_root / item.path


def test_media_serves_verified_master_inline_and_attachment(render_case: RenderApiCase) -> None:
    case = render_case
    job_id = run_render(case)
    variant, path = output(case, job_id)
    url = f"/api/v1/campaigns/campaign-one/editing/renders/{job_id}/outputs/{variant}/media"
    with TestClient(create_app(case.settings), base_url="http://127.0.0.1") as client:
        foreign = url.replace("campaign-one", "missing-campaign")
        assert client.get(foreign).status_code == 404
        for download, disposition in ((False, "inline"), (True, "attachment")):
            response = client.get(url, params={"download": str(download).lower()})
            assert response.status_code == 200 and response.content == path.read_bytes()
            assert response.headers["content-type"] == "video/mp4"
            assert response.headers["content-disposition"].startswith(disposition)
            assert response.headers["cache-control"] == "no-store"
        path.write_bytes(b"private-token-secret")
        bad = client.get(url)
        assert bad.status_code == 409 and b"private" not in bad.content and b"token" not in bad.content


@pytest.mark.parametrize("mutation", ["hash", "path", "variant", "output_hash", "runtime"])
def test_media_rejects_conflicting_receipt(render_case: RenderApiCase, mutation: str) -> None:
    case = render_case
    job_id = run_render(case)
    variant, path = output(case, job_id)
    receipt = path.with_name("render.json")
    data = json.loads(receipt.read_bytes())
    if mutation == "runtime":
        data["runtime"]["fingerprint"] = "d" * 64
    else:
        field, value = {"hash": ("sha256", "d" * 64), "path": ("path", "../private.mp4"),
                        "variant": ("outputVariantId", "foreign"), "output_hash": ("outputHash", "e" * 64)}[mutation]
        data[field] = value
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(QueryError) as error:
        case.commands.editorial_renders.open_media(case.plan.campaign_id, job_id, variant)
    assert error.value.code == "artifact_invalid"


def test_media_iterator_closes_handle_when_abandoned_or_finished() -> None:
    stream = BytesIO(b"x" * 100_000)
    chunks = _chunks(stream)
    assert next(chunks) == b"x" * 65_536
    chunks.close()
    assert stream.closed
    completed = BytesIO(b"master")
    assert b"".join(_chunks(completed)) == b"master"
    assert completed.closed


def test_media_allows_reuse_from_another_producer_plan(render_case: RenderApiCase) -> None:
    case = render_case
    first = run_render(case)
    variant, path = output(case, first)
    before = path.with_name("render.json").read_bytes()
    case.plan.max_outputs += 1
    refresh_plan(case.plan)
    publish_test_plan(case.plan, case.settings.work_root)
    request = case.request.model_copy(update={"plan_hash": case.plan.plan_hash,
        "execution_id": UUID("22222222-2222-4222-8222-222222222222")})
    second = run_render(case, request)
    stream, filename = case.commands.editorial_renders.open_media(case.plan.campaign_id, second, variant)
    with stream:
        assert stream.read() == path.read_bytes()
    assert filename == path.name and path.with_name("render.json").read_bytes() == before


def test_media_reads_verified_open_handle(render_case: RenderApiCase) -> None:
    case = render_case
    job_id = run_render(case)
    variant, path = output(case, job_id)
    expected = path.read_bytes()
    stream, _ = case.commands.editorial_renders.open_media(case.plan.campaign_id, job_id, variant)
    replacement = path.with_name("replacement.mp4")
    replacement.write_bytes(b"private-token-secret")
    with stream:
        try:
            os.replace(replacement, path)
        except PermissionError:
            assert os.name == "nt"  # Windows can lock pathname replacement for the open handle.
        assert stream.read() == expected
    assert stream.closed


def test_failed_output_is_not_downloadable(render_case: RenderApiCase) -> None:
    case = render_case
    case.plan.outputs[0].manifest.captions.enabled = True
    case.plan.outputs[0].manifest.captions.font = case.plan.outputs[0].manifest.headline.font
    refresh_plan(case.plan)
    publish_test_plan(case.plan, case.settings.work_root)
    case.request = case.request.model_copy(update={"plan_hash": case.plan.plan_hash})
    job_id = run_render(case)
    view = case.commands.editorial_renders.get(case.plan.campaign_id, job_id)
    assert view.render_status == "partial_failure"
    with pytest.raises(QueryError) as error:
        case.commands.editorial_renders.open_media(case.plan.campaign_id, job_id, case.plan.outputs[0].output_variant_id)
    assert error.value.code == "operation_not_allowed"


def test_symlink_or_missing_master_never_serves_private_bytes(render_case: RenderApiCase) -> None:
    case = render_case
    job_id = run_render(case)
    variant, path = output(case, job_id)
    private = case.settings.project_root / "private.mp4"
    private.write_bytes(b"private-token-secret")
    path.unlink()
    try:
        path.symlink_to(private)
    except OSError:
        assert os.name == "nt" and not path.exists()
    with pytest.raises(QueryError) as error:
        case.commands.editorial_renders.open_media(case.plan.campaign_id, job_id, variant)
    assert error.value.code == "artifact_invalid"
