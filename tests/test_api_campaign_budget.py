from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import pytest

from tests.api_helpers import create_api_fixture, database_dump
from tests.test_api_http import client_for

PREFIX = "/api/v1/campaigns/campaign-one/budget"
BODY = {"currency": "USD", "limitCents": 1000, "confirmed": True}


def test_budget_get_is_readonly_and_save_creates_no_jobs(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with sqlite3.connect(settings.database) as connection:
        connection.execute("UPDATE campaigns SET budget_json=?", ('{"note":"keep"}',))
    before = database_dump(settings.database)
    with client_for(settings) as client:
        assert client.get(PREFIX).json() == {"state": "missing", "currency": None, "limitCents": None}
        assert database_dump(settings.database) == before
        saved = client.post(PREFIX, json=BODY)
        assert saved.status_code == 200
        assert saved.json() == {"state": "configured", "currency": "USD", "limitCents": 1000}
        assert client.get(PREFIX).json() == saved.json()
        after = database_dump(settings.database)
        assert client.post(PREFIX, json=BODY).json() == saved.json()
        assert database_dump(settings.database) == after
        assert client.get("/api/v1/campaigns/campaign-one/jobs").json() == {"items": []}
        assert not settings.work_root.exists()


@pytest.mark.parametrize("changes", [
    {"confirmed": False}, {"confirmed": 1}, {"confirmed": "true"},
    {"limitCents": True}, {"limitCents": 1.5}, {"limitCents": 0},
    {"currency": "usd"}, {"currency": "USＤ"},
])
def test_budget_origin_and_validation_boundaries(tmp_path: Path, changes: dict[str, Any]) -> None:
    settings = create_api_fixture(tmp_path)
    before = database_dump(settings.database)
    with client_for(settings) as client:
        response = client.post(PREFIX, json={**BODY, **changes})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert client.post(PREFIX, json=BODY, headers={"origin": "https://evil.invalid"}).status_code == 422
        assert client.post(PREFIX, json={"currency": "USD", "limitCents": 1000}).status_code == 422
    assert database_dump(settings.database) == before


def test_budget_missing_campaign_and_conflict_are_sanitized(tmp_path: Path) -> None:
    settings = create_api_fixture(tmp_path)
    with sqlite3.connect(settings.database) as connection:
        connection.execute("UPDATE campaigns SET budget_json=?", ('{"currency":"USD"}',))
    before = database_dump(settings.database)
    with client_for(settings) as client:
        assert client.get(PREFIX).json() == {"state": "invalid", "currency": None, "limitCents": None}
        response = client.post(PREFIX, json=BODY)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "operation_conflict"
        assert str(tmp_path) not in response.text
        assert client.get(PREFIX.replace("campaign-one", "missing")).status_code == 404
        assert client.post(PREFIX.replace("campaign-one", "missing"), json=BODY).status_code == 404
    assert database_dump(settings.database) == before
