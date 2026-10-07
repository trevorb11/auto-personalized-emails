import asyncio

import config
from tools import api_budget
from tools.deal_calculator import factor_rate_to_apr
from tools.ghl_client import create_contact
from tools.inbound_leads import get_mca_search_targets
from tools.llm_personalizer import _validate


def test_dry_run_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    result = asyncio.run(create_contact({"business_name": "X", "phone": None}))
    assert result["dry_run"]


def test_api_budget_enforces_cap(monkeypatch):
    monkeypatch.setattr(config, "DAILY_HUNTER_LOOKUPS", 2)
    assert api_budget.try_consume("hunter")
    assert api_budget.try_consume("hunter")
    assert not api_budget.try_consume("hunter")
    assert api_budget.remaining("hunter") == 0


def test_apr_accounts_for_amortization():
    info = factor_rate_to_apr(1.35, 8)
    assert info["simple_annualized_rate"] == 52.5
    assert 90 < info["estimated_apr"] < 100


def test_search_targets_rotate_without_overlap():
    day1 = get_mca_search_targets(["FL", "TX"], cities_per_state=2, rotation_seed=100, per_day=4)[:4]
    day2 = get_mca_search_targets(["FL", "TX"], cities_per_state=2, rotation_seed=101, per_day=4)[:4]
    assert not {tuple(t.values()) for t in day1} & {tuple(t.values()) for t in day2}


def test_llm_rewrite_rejects_invented_numbers():
    draft = {"subject": "s", "body": "around $680/day, maybe $544/day", "sms_variant": "hi"}
    ok = {"subject": "s", "body": "roughly $680/day today", "sms_variant": "hi"}
    bad = {"subject": "s", "body": "we can save you $5,000/mo", "sms_variant": "hi"}
    assert _validate(draft, ok) is None
    assert "introduced figures" in _validate(draft, bad)
