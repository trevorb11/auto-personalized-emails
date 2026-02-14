"""
Personalized Email Generator Tool.

Generates first-touch outreach emails for MCA prospects.
Uses lead data (industry, filing details, business context) to
create relevant, non-generic messages.

Tone: casual, direct, no corporate jargon.
Never says "merchant cash advance" in first touch.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def generate_outreach_email(lead: dict) -> dict:
    """
    Generate a personalized first-touch email for an MCA prospect.

    Args:
        lead: dict with business_name, industry, business_focus,
              ucc_filing_age_months, secured_party, monthly_revenue,
              city, state, google_review_count, etc.

    Returns:
        dict with subject, body, and sms_variant.
    """
    name = lead.get("business_name", "your business")
    industry = lead.get("industry", "")
    focus = lead.get("business_focus", "New Funding")
    filing_age = lead.get("ucc_filing_age_months", 0)
    secured_party = lead.get("secured_party", "")
    city = lead.get("city", "")
    state = lead.get("state", "")
    reviews = lead.get("google_review_count", 0)

    # Build location string
    location = f"{city}, {state}" if city and state else (city or state or "")

    # ── Select template based on business focus ────────────────
    if focus == "MCA Renewal":
        return _renewal_email(name, industry, filing_age, secured_party, location, reviews)
    elif focus == "Consolidation":
        return _consolidation_email(name, industry, filing_age, secured_party, location, reviews)
    else:
        return _new_funding_email(name, industry, location, reviews)


def _renewal_email(
    name: str, industry: str, filing_age: float,
    secured_party: str, location: str, reviews: int,
) -> dict:
    """Template for businesses in the MCA renewal window (6-12 months)."""
    # Industry-specific hook
    if "truck" in industry.lower() or "transport" in industry.lower():
        industry_hook = "I know cash flow in trucking can be unpredictable with fuel costs and payment delays."
    elif "construct" in industry.lower():
        industry_hook = "Construction projects tie up a lot of capital — I see it all the time."
    elif "restaurant" in industry.lower():
        industry_hook = "Running a restaurant means constant reinvestment — equipment, inventory, staffing."
    else:
        industry_hook = "Running a business means there's always something that needs capital."

    filing_mention = ""
    if filing_age:
        filing_mention = (
            f"I noticed you took on some business funding about "
            f"{int(filing_age)} months ago. "
        )

    lender_note = ""
    if secured_party:
        lender_note = "If your current terms aren't ideal, that's worth looking at. "

    review_note = ""
    if reviews >= 50:
        review_note = f"Your {reviews} reviews speak for themselves — clearly you're doing something right. "

    subject = f"Quick question about {name}"
    body = (
        f"Hi,\n\n"
        f"{filing_mention}{lender_note}"
        f"{industry_hook}\n\n"
        f"{review_note}"
        f"We work with businesses{f' in {location}' if location else ''} to get better "
        f"funding terms — often lower costs and more flexible payback than what's "
        f"already in place.\n\n"
        f"Would it make sense to compare what's out there right now?\n\n"
        f"— Today Capital Group"
    )

    sms = (
        f"Hey, this is TCG. I saw {name} might be coming up on a renewal for "
        f"business funding. We've been getting better terms for "
        f"{'businesses in ' + location if location else 'businesses like yours'} "
        f"lately. Worth a quick look?"
    )

    return {"subject": subject, "body": body, "sms_variant": sms}


def _consolidation_email(
    name: str, industry: str, filing_age: float,
    secured_party: str, location: str, reviews: int,
) -> dict:
    """Template for businesses that may benefit from consolidating positions."""
    subject = f"Reduce your daily payments, {name.split()[0] if name else ''}?"

    body = (
        f"Hi,\n\n"
        f"I work with businesses that have multiple funding positions and "
        f"help them consolidate into a single, lower daily payment.\n\n"
        f"{'I noticed ' + name + ' has had a few rounds of funding. ' if filing_age else ''}"
        f"If you're currently making multiple daily or weekly payments, "
        f"there's a good chance we can roll those into one and save you money.\n\n"
        f"It takes about 5 minutes to see if the numbers work. "
        f"Want me to run a quick comparison?\n\n"
        f"— Today Capital Group"
    )

    sms = (
        f"Hey, TCG here. If {name} is making multiple daily payments on "
        f"business funding, we can usually consolidate into one lower payment. "
        f"Want me to check what's possible?"
    )

    return {"subject": subject, "body": body, "sms_variant": sms}


def _new_funding_email(
    name: str, industry: str, location: str, reviews: int,
) -> dict:
    """Template for new funding prospects (no existing MCA detected)."""
    industry_mention = ""
    if industry:
        industry_mention = f"We work with a lot of {industry} businesses, so I'm familiar with how the cash flow works. "

    location_mention = ""
    if location:
        location_mention = f"We've been working with businesses in {location} and "

    subject = f"Business funding for {name}"

    body = (
        f"Hi,\n\n"
        f"{location_mention}{'I ' if not location_mention else 'I '}wanted to reach out.\n\n"
        f"{industry_mention}"
        f"If {name} ever needs working capital — for equipment, payroll gaps, "
        f"inventory, expansion, anything — we have options from $15K to $500K "
        f"with approvals in 24-48 hours.\n\n"
        f"No obligation to see what you qualify for. "
        f"Want me to send over some numbers?\n\n"
        f"— Today Capital Group"
    )

    sms = (
        f"Hi, TCG here. We help businesses"
        f"{' in ' + location if location else ''} "
        f"get fast working capital ($15K-$500K, 24hr approval). "
        f"Would {name} ever need something like that?"
    )

    return {"subject": subject, "body": body, "sms_variant": sms}


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    test_leads = [
        {
            "business_name": "Rodriguez Trucking LLC",
            "industry": "trucking",
            "business_focus": "MCA Renewal",
            "ucc_filing_age_months": 8,
            "secured_party": "Yellowstone Capital",
            "city": "Miami",
            "state": "FL",
            "google_review_count": 62,
        },
        {
            "business_name": "Tony's Pizza Kitchen",
            "industry": "restaurant",
            "business_focus": "Consolidation",
            "ucc_filing_age_months": 14,
            "secured_party": "Multiple",
            "city": "Brooklyn",
            "state": "NY",
            "google_review_count": 230,
        },
        {
            "business_name": "Apex Plumbing Services",
            "industry": "plumbing",
            "business_focus": "New Funding",
            "city": "Dallas",
            "state": "TX",
            "google_review_count": 35,
        },
    ]

    for lead in test_leads:
        result = generate_outreach_email(lead)
        print(f"\n{'='*60}")
        print(f"Business: {lead['business_name']} ({lead['business_focus']})")
        print(f"Subject: {result['subject']}")
        print(f"\n{result['body']}")
        print(f"\nSMS: {result['sms_variant']}")
