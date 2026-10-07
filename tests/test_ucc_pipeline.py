import asyncio
from datetime import datetime, timedelta

import data.database as database
from data.database import get_connection, insert_filing
from tools.ucc_processor import process_ucc_filings


def _date(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%m/%d/%Y")


def _seed(filings):
    conn = get_connection()
    for f in filings:
        insert_filing(conn, {"debtor_state": "FL", "source_state": "FL", **f})
    conn.commit()
    conn.close()


def test_renewal_window_and_stacking():
    _seed([
        {"filing_number": "1", "filing_date": _date(270), "debtor_name": "Joe's Trucking LLC",
         "secured_party": "Yellowstone Capital LLC", "secured_party_type": "MCA_LENDER"},
        {"filing_number": "2", "filing_date": _date(60), "debtor_name": "JOES TRUCKING INC",
         "secured_party": "Fundkite", "secured_party_type": "MCA_LENDER"},
        {"filing_number": "3", "filing_date": _date(3), "debtor_name": "Fresh Bakery",
         "secured_party": "Credibly", "secured_party_type": "MCA_LENDER"},
    ])
    result = process_ucc_filings("FL")

    # Only the 9-month-old filing is in the scan window; the 2-day-old one is not
    assert [f["filing_number"] for f in result["filings"]] == ["1"]
    filing = result["filings"][0]
    assert filing["in_renewal_window"]
    assert filing["funder_display_name"] == "Yellowstone Capital"
    assert filing["urgency"]["level"] == "high"
    # Stacking found via full history, across name variants
    assert filing["position_count"] == 2
    assert filing["other_funders"][0]["display_name"] == "Fundkite"


def test_bank_filing_does_not_count_as_position():
    _seed([
        {"filing_number": "1", "filing_date": _date(240), "debtor_name": "Acme Roofing",
         "secured_party": "Rapid Finance", "secured_party_type": "MCA_LENDER"},
        {"filing_number": "2", "filing_date": _date(100), "debtor_name": "Acme Roofing",
         "secured_party": "Chase Bank", "secured_party_type": "BANK"},
    ])
    filing = process_ucc_filings("FL")["filings"][0]
    assert filing["position_count"] == 1


def test_main_agent_carries_intel_into_lead(monkeypatch):
    import main_agent
    _seed([
        {"filing_number": "1", "filing_date": _date(270), "debtor_name": "Joe's Trucking LLC",
         "secured_party": "Yellowstone Capital LLC", "secured_party_type": "MCA_LENDER"},
    ])
    leads = asyncio.run(main_agent.process_state_filings("FL", "2026-01-01"))
    assert len(leads) == 1
    lead = leads[0]
    assert lead["funder_display_name"] == "Yellowstone Capital"
    assert lead["business_focus"] == "MCA Renewal"

    conn = get_connection()
    row = dict(conn.execute("SELECT * FROM leads").fetchone())
    conn.close()
    assert row["funder_display_name"] == "Yellowstone Capital"
    assert row["urgency_level"] == "high"

    # Converted filings aren't re-processed the next day
    assert process_ucc_filings("FL")["total_filings"] == 0
