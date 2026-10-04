from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from auraly_pipeline.campaigns import domain, service as module
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from tests.test_campaign_domain import valid_campaign_data


def setup_service(tmp_path: Path, budget: dict[str, Any] | None = None) -> CampaignService:
    service = CampaignService.for_database(tmp_path / "budget.db")
    data = valid_campaign_data()
    data.update(campaignId="campaign-one", budget=budget if budget is not None else {"note": "keep"})
    service.create_campaign(CampaignCreate.model_validate(data))
    return service


def test_initial_budget_preserves_metadata_and_replay_is_noop(tmp_path: Path) -> None:
    service = setup_service(tmp_path)
    before = service.get_campaign("campaign-one")
    request = domain.CampaignBudgetSetup(currency="USD", limit_cents=1000, confirmed=True)
    result = service.configure_budget("campaign-one", request)
    assert result.model_dump(by_alias=True) == {"state": "configured", "currency": "USD", "limitCents": 1000}
    after = service.get_campaign("campaign-one")
    assert after.budget == {"note": "keep", "currency": "USD", "limitCents": 1000}
    assert after.config == before.config and after.copy_masters == before.copy_masters
    assert after.updated_at >= before.updated_at
    assert service.configure_budget("campaign-one", request) == result
    assert service.get_campaign("campaign-one").updated_at == after.updated_at
    service.close()


@pytest.mark.parametrize("budget", [
    {"currency": "USD", "limitCents": 1000}, {"currency": "USD"},
    {"limitCents": 1000}, {"currency": None, "limitCents": 1000},
])
def test_budget_setup_rejects_overwrite_or_partial_legacy(tmp_path: Path, budget: dict[str, Any]) -> None:
    service = setup_service(tmp_path, budget)
    before = service.get_campaign("campaign-one")
    with pytest.raises(module.CampaignBudgetConflictError):
        service.configure_budget("campaign-one", domain.CampaignBudgetSetup(currency="EUR", limit_cents=2000, confirmed=True))
    assert service.get_campaign("campaign-one") == before
    service.close()


def test_concurrent_initial_setup_does_not_overwrite(tmp_path: Path) -> None:
    service = setup_service(tmp_path)
    barrier = Barrier(2)

    def save(limit: int) -> str:
        barrier.wait(timeout=5)
        try:
            service.configure_budget("campaign-one", domain.CampaignBudgetSetup(currency="USD", limit_cents=limit, confirmed=True))
        except module.CampaignBudgetConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save, value) for value in (1000, 2000)]
        assert sorted(future.result(timeout=15) for future in futures) == ["conflict", "saved"]
    assert service.get_campaign("campaign-one").budget["limitCents"] in (1000, 2000)
    service.close()


@pytest.mark.parametrize("budget", [
    {"currency": "USD", "limitCents": value} for value in (True, 1.5, 0, None, "1000")
] + [{"currency": "usd", "limitCents": 1}, {"limitCents": 1}, {"currency": "USD"}])
def test_budget_projection_invalid_types(budget: dict[str, Any]) -> None:
    assert domain.campaign_budget_view(budget).model_dump(by_alias=True) == {
        "state": "invalid", "currency": None, "limitCents": None,
    }


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_budget_setup_requires_strict_confirmation(value: Any) -> None:
    with pytest.raises(ValidationError):
        domain.CampaignBudgetSetup(currency="USD", limit_cents=1000, confirmed=value)


def test_budget_projection_missing_and_readonly(tmp_path: Path) -> None:
    service = setup_service(tmp_path)
    assert domain.campaign_budget_view({"note": "keep"}).state == "missing"
    # Domain configuration must never introduce jobs or touch provider state.
    from auraly_pipeline.campaigns.persistence import create_existing_sqlite_engine
    engine = create_existing_sqlite_engine(tmp_path / "budget.db")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar() == 0
    engine.dispose()
    service.close()
