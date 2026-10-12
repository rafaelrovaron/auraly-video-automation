from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auraly_pipeline.api.app import create_app
from tests.render_job_helpers import RenderApiCase, render_case as render_case
from tests.test_api_final_review import target, run_qc, review_request


def approved(client: TestClient, prefix: str, body: dict[str, Any]) -> dict[str, Any]:
    assert client.post(prefix + "/qc", json=body).status_code == 202
    run_qc(cast(FastAPI, client.app).state.commands)
    state = client.get(prefix + "/review-state").json()
    response = client.post(prefix + "/review", json=review_request(state))
    assert response.status_code == 200
    record = response.json()
    return {key: record[key] for key in ("schemaVersion", "campaignId", "videoId",
        "planHash", "outputVariantId", "renderKey", "reviewKey")}


def test_api_delivery_approval_replay_reload(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        request = approved(client, prefix, body)
        absent = client.get(prefix + "/delivery")
        assert absent.status_code == 200 and absent.headers["cache-control"] == "no-store"
        assert absent.json() == {"request": request, "record": None}
        response = client.post(prefix + "/delivery", json=request)
        assert response.status_code == 200, response.text
        record = response.json()
        assert client.post(prefix + "/delivery", json=request).json() == record
        root = cast(FastAPI, client.app).state.commands.editorial_reviews.deliveries.delivery_root
        assert (root / record["path"]).stat().st_size == record["sizeBytes"]
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.get(prefix + "/delivery").json()["record"] == record


def test_api_delivery_gate_foreign_and_corruption(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.get(prefix + "/delivery").status_code == 409
        request = approved(client, prefix, body)
        for key, value in (("reviewKey", "a" * 64), ("planHash", "a" * 64), ("videoId", "other"),
                           ("destination", "private-path-marker")):
            assert client.post(prefix + "/delivery", json={**request, key: value}).status_code == 422
        assert client.get(prefix + "/delivery?unknown=x").status_code == 422
        record = client.post(prefix + "/delivery", json=request).json()
        root = cast(FastAPI, client.app).state.commands.editorial_reviews.deliveries.delivery_root
        destination = root / record["path"]
        destination.write_bytes(b"private-marker")
        for response in (client.get(prefix + "/delivery"), client.post(prefix + "/delivery", json=request)):
            assert response.status_code == 409 and response.json()["error"]["code"] == "artifact_invalid"
            assert "private-" not in response.text and str(root) not in response.text
        assert destination.read_bytes() == b"private-marker"


def test_api_rejected_review_cannot_deliver(render_case: RenderApiCase) -> None:
    prefix, body = target(render_case)
    with TestClient(create_app(render_case.settings), base_url="http://127.0.0.1") as client:
        assert client.post(prefix + "/qc", json=body).status_code == 202
        run_qc(cast(FastAPI, client.app).state.commands)
        state = client.get(prefix + "/review-state").json()
        response = client.post(prefix + "/review", json=review_request(state, decision="rejected", comment="Timing"))
        record = response.json()
        request = {key: record[key] for key in ("schemaVersion", "campaignId", "videoId",
            "planHash", "outputVariantId", "renderKey", "reviewKey")}
        assert client.get(prefix + "/delivery").status_code == 409
        assert client.post(prefix + "/delivery", json=request).status_code == 409
        assert not cast(FastAPI, client.app).state.commands.editorial_reviews.deliveries.delivery_root.exists()
