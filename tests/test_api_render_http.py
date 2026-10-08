from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from auraly_pipeline.api.app import create_app
from tests.render_job_helpers import RenderApiCase, render_case as provide_render_case  # noqa: F401


def test_post_only_queues_and_openapi_has_typed_contracts(render_case: RenderApiCase, monkeypatch: pytest.MonkeyPatch) -> None:
    case = render_case
    with TestClient(create_app(case.settings), base_url="http://127.0.0.1") as client:
        def forbidden(*a: object, **k: object) -> None:
            pytest.fail("HTTP submission must not render")
        monkeypatch.setattr(cast(FastAPI, client.app).state.commands.renderer, "render", forbidden)
        prefix = "/api/v1/campaigns/campaign-one/editing/renders"
        response = client.post(prefix, json=case.request.model_dump(mode="json", by_alias=True))
        assert response.status_code == 202
        body = response.json()
        assert body["executionId"] == "11111111-1111-4111-8111-111111111111"
        assert case.commands.jobs.get_job(body["jobId"]).status == "queued"
        assert client.post(prefix, json=case.request.model_dump(mode="json", by_alias=True)).json() == body
        view = client.get(prefix + "/" + body["jobId"]).json()
        assert view["status"] == "queued" and view["result"] is None
        assert client.get(prefix, params={"videoId": case.plan.video_id, "planHash": case.plan.plan_hash}).json()["items"] == [view]
        assert client.get(prefix, params={"videoId": "missing"}).json()["items"] == []
        assert client.get(prefix, params={"privatePath": "x"}).status_code == 422
        assert client.get(prefix, params=[("videoId", "a"), ("videoId", "b")]).status_code == 422
        assert client.post(prefix, json={**case.request.model_dump(mode="json", by_alias=True), "campaignId": "other"}).status_code == 422
        spec = client.get("/openapi.json").json()
        assert spec["paths"][prefix.replace("campaign-one", "{campaignId}")]["post"]["responses"]["202"]["content"]["application/json"]["schema"]
    with TestClient(create_app(case.settings), base_url="http://127.0.0.1") as restarted:
        assert restarted.get(prefix + "/" + body["jobId"]).json()["executionId"] == body["executionId"]
