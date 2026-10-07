import config
from tools.email_generator import generate_outreach_email
from tools.lead_scorer import score_mca_lead


def test_scorer_handles_plus_revenue_strings():
    result = score_mca_lead({"monthly_revenue": "$100K+/mo"})
    assert any("Strong revenue" in r for r in result["reasons"])


def test_scorer_tolerates_db_nulls():
    lead = {
        "industry": None, "monthly_revenue": None, "years_in_business": None,
        "ucc_filing_age_months": None, "urgency": None, "position_count": None,
        "google_review_count": None, "google_rating": None, "other_funders": None,
    }
    assert score_mca_lead(lead)["tier"] == "D"


def _renewal_lead(**overrides):
    lead = {
        "business_name": "Rodriguez Trucking LLC",
        "industry": "trucking",
        "business_focus": "MCA Renewal",
        "ucc_filing_age_months": 9,
        "funder_display_name": "Yellowstone Capital",
        "funder_positioning": "We consistently beat Yellowstone renewals by 15-20%.",
        "urgency": {"level": "high", "message": "renewal window"},
        "estimated_daily_payment": 680,
        "city": "Miami", "state": "FL",
    }
    lead.update(overrides)
    return lead


def test_email_has_compliance_footer_and_sms_stop(monkeypatch):
    monkeypatch.setattr(config, "SENDER_ADDRESS", "1 Main St, Miami FL 33101")
    email = generate_outreach_email(_renewal_lead())
    assert "1 Main St, Miami FL 33101" in email["body"]
    assert config.OPT_OUT_TEXT in email["body"]
    assert email["sms_variant"].endswith("Reply STOP to opt out.")
    assert not any("COMPLIANCE" in n for n in email["personalization_notes"])


def test_missing_address_is_flagged(monkeypatch):
    monkeypatch.setattr(config, "SENDER_ADDRESS", "")
    email = generate_outreach_email(_renewal_lead())
    assert any("COMPLIANCE" in n for n in email["personalization_notes"])


def test_competitor_claims_stay_internal():
    email = generate_outreach_email(_renewal_lead())
    assert "consistently beat" not in email["body"]
    assert any("consistently beat" in n for n in email["personalization_notes"])


def test_first_name_greeting():
    email = generate_outreach_email(_renewal_lead(first_name="Maria"))
    assert email["body"].startswith("Hi Maria,")


def test_all_templates_tolerate_nulls():
    for focus in ("MCA Renewal", "Consolidation", "New Funding"):
        lead = {k: None for k in (
            "business_name", "industry", "city", "state", "urgency",
            "estimated_daily_payment", "google_review_count", "position_count",
            "other_funders", "monthly_revenue", "snippet",
        )}
        lead["business_focus"] = focus
        email = generate_outreach_email(lead)
        assert email["subject"] and "None" not in email["body"]
        assert "0 months ago" not in email["body"]
