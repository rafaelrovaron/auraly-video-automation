from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auraly_pipeline.api.app import create_app
from auraly_pipeline.api.commands import ApiCommands
from tests.render_job_helpers import RenderApiCase, render_case as provide_render_case, run_render  # noqa: F401


def target(case: RenderApiCase) -> tuple[str, dict[str, Any]]:
    job_id = run_render(case)
    view = case.commands.editorial_renders.get(case.plan.campaign_id, job_id)
    assert view.result is not None
    output = view.result.outputs[0]
    body = {"schemaVersion": "1.0", "operation": "edit_qc", "campaignId": case.plan.campaign_id,
        "jobId": job_id, "videoId": case.plan.video_id, "planHash": case.plan.plan_hash,
        "outputVariantId": output.output_variant_id, "renderKey": output.render_key,
        "executionId": str(uuid4())}
    prefix = f"/api/v1/campaigns/{case.plan.campaign_id}/editing/renders/{job_id}/outputs/{output.output_variant_id}"
    return prefix, body


def run_qc(commands: ApiCommands) -> None:
    commands.jobs.worker_once("qc-test-worker", campaign_id="campaign-one", job_type="api.local.operation")


def review_request(state: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**{key: state[key] for key in ("schemaVersion", "campaignId", "videoId", "planHash", "outputVariantId", "renderKey")},
        "qcKey": state["reports"][0]["qcKey"], "decision": "approved", "reviewedBy": "Tester",
        "comment": None, "watchedAndListened": True, **changes}


def test_qc_post_only_enqueues(render_case: RenderApiCase, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        commands = cast(FastAPI, client.app).state.commands
        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("HTTP must not analyze")
        monkeypatch.setattr(commands.editorial_reviews.qc, "run", forbidden)
        sent = client.post(prefix + "/qc", json=body)
        assert sent.status_code == 202
        assert client.post(prefix + "/qc", json=body).json() == sent.json()
        state = client.get(prefix + "/review-state")
        assert state.status_code == 200 and state.headers["cache-control"] == "no-store"
        assert state.json()["reports"] == [] and state.json()["review"] is None
        assert state.json()["runs"][0]["status"] == "queued"
        assert client.post(prefix + "/qc", json={**body, "privatePath": "secret"}).status_code == 422
        assert client.get(prefix + "/review-state?unknown=x").status_code == 422


def test_qc_execution_identity_survives_restart(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        sent = client.post(prefix + "/qc", json=body)
        assert sent.status_code == 202
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.post(prefix + "/qc", json=body).json() == sent.json()
        commands = cast(FastAPI, client.app).state.commands
        run_qc(commands)
        assert client.get(prefix + "/review-state").json()["runs"][0]["request"]["executionId"] == body["executionId"]
        assert client.post(prefix + "/qc", json={**body, "executionId": str(uuid4())}).json()["jobId"] != sent.json()["jobId"]
        run_qc(commands)
        state = client.get(prefix + "/review-state").json()
        assert len(state["reports"]) == 1
        assert any(run["result"]["result"]["outputs"][0]["reused"] for run in state["runs"])


def test_state_empty_then_qc_then_review(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.get(prefix + "/review-state").json()["reports"] == []
        assert client.post(prefix + "/qc", json=body).status_code == 202
        run_qc(cast(FastAPI, client.app).state.commands)
        state = client.get(prefix + "/review-state").json()
        assert state["reports"][0]["status"] == "passed"
        saved = client.post(prefix + "/review", json=review_request(state))
        assert saved.status_code == 200
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.get(prefix + "/review-state").json()["review"] == saved.json()


def test_completed_qc_error_not_passed(render_case: RenderApiCase, monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing.domain import EditingError
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        commands = cast(FastAPI, client.app).state.commands
        def failure(*args: object, **kwargs: object) -> None:
            raise EditingError("artifact", "private-path-secret")
        monkeypatch.setattr(commands.editorial_reviews.qc, "_run_one", failure)
        assert client.post(prefix + "/qc", json=body).status_code == 202
        run_qc(commands)
        state = client.get(prefix + "/review-state")
        assert state.status_code == 200
        assert state.json()["reports"] == [] and state.json()["review"] is None
        run = state.json()["runs"][0]
        assert run["status"] == "completed" and run["result"]["result"]["outputs"][0]["status"] == "error"
        assert "private-path-secret" not in state.text


def test_foreign_failed_stale_target_rejected(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        for field, value in (("videoId", "other"), ("planHash", "a" * 64), ("renderKey", "b" * 64), ("outputVariantId", "other")):
            assert client.post(prefix + "/qc", json={**body, field: value}).status_code == 422
        assert client.get(prefix.replace(body["outputVariantId"], "missing") + "/review-state").status_code == 404
        stream, _ = render_case.commands.editorial_renders.open_media(body["campaignId"], body["jobId"], body["outputVariantId"])
        path = stream.name
        stream.close()
        from pathlib import Path
        Path(path).write_bytes(b"changed")
        response = client.post(prefix + "/qc", json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "artifact_invalid"


def test_review_rules_and_definitive_conflict(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.post(prefix + "/qc", json=body).status_code == 202
        run_qc(cast(FastAPI, client.app).state.commands)
        state = client.get(prefix + "/review-state").json()
        request = review_request(state)
        assert client.post(prefix + "/review", json={**request, "watchedAndListened": False}).status_code == 422
        assert client.post(prefix + "/review", json={**request, "decision": "rejected"}).status_code == 422
        saved = client.post(prefix + "/review", json=request)
        assert saved.status_code == 200
        assert client.post(prefix + "/review", json=request).json() == saved.json()
        conflict = client.post(prefix + "/review", json={**request, "decision": "rejected", "comment": "Wrong headline"})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "operation_conflict"
        assert client.get(prefix + "/review-state").json()["review"] == saved.json()


def test_review_concurrent_client_winner(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as first, TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as second:
        assert first.post(prefix + "/qc", json=body).status_code == 202
        run_qc(cast(FastAPI, first.app).state.commands)
        state = first.get(prefix + "/review-state").json()
        winner = second.post(prefix + "/review", json=review_request(state, decision="rejected", comment="Timing"))
        assert winner.status_code == 200
        assert first.post(prefix + "/review", json=review_request(state)).status_code == 409
        assert first.get(prefix + "/review-state").json()["review"] == winner.json()


def test_review_state_invalid_evidence_errors(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.post(prefix + "/qc", json=body).status_code == 202
        run_qc(cast(FastAPI, client.app).state.commands)
        state = client.get(prefix + "/review-state").json()
        report = state["reports"][0]
        path = cast(FastAPI, client.app).state.commands.editorial_reviews.qc.work_root / "campaigns" / body["campaignId"] / "editing" / "qc" / body["outputVariantId"] / body["renderKey"] / report["qcKey"] / "report.json"
        path.write_bytes(b"private-secret")
        response = client.get(prefix + "/review-state")
        assert response.status_code == 409 and response.json()["error"]["code"] == "artifact_invalid"
        assert "private-secret" not in response.text


def test_final_review_schema_export(tmp_path: Any) -> None:
    from auraly_pipeline.editing import schema
    paths = schema.export_final_review_schemas(tmp_path)
    assert {path.name for path in paths} == {"final-qc-operation.schema.json", "final-qc-run-view.schema.json", "final-review-state.schema.json"}
