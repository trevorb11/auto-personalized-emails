"""
Personalized Email Generator Tool.

Generates first-touch outreach emails for MCA prospects using
competitive funder intelligence, estimated payment data, and
urgency-calibrated messaging.

Key differentiators vs. generic outreach:
  - Names the specific funder ("I saw you're with Yellowstone...")
  - Shows estimated payment comparison ("~$650/day → ~$520/day")
  - Calculates monthly savings numbers
  - Calibrates urgency based on filing age / payoff timing
  - Uses funder-specific competitive hooks

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
        lead: dict with business data, funder intelligence, payment
              estimates, urgency, and stacking info from the pipeline.

    Returns:
        dict with subject, body, sms_variant, and personalization_notes.
    """
    name = lead.get("business_name", "your business")
    industry = lead.get("industry", "")
    focus = lead.get("business_focus", "New Funding")
    city = lead.get("city", "")
    state = lead.get("state", "")

    location = f"{city}, {state}" if city and state else (city or state or "")

    if focus == "MCA Renewal":
        return _renewal_email(lead, name, industry, location)
    elif focus == "Consolidation":
        return _consolidation_email(lead, name, industry, location)
    else:
        return _new_funding_email(lead, name, industry, location)


# ═══════════════════════════════════════════════════════════════
# RENEWAL — they have one position approaching maturity
# ═══════════════════════════════════════════════════════════════

def _renewal_email(lead: dict, name: str, industry: str, location: str) -> dict:
    """
    Template for businesses in the MCA renewal window.
    Names the funder, shows estimated payments, and offers rate comparison.
    """
    filing_age = lead.get("ucc_filing_age_months") or lead.get("filing_age_months") or 0
    funder_name = lead.get("funder_display_name") or lead.get("secured_party", "")
    funder_positioning = lead.get("funder_positioning", "")
    funder_weakness = lead.get("funder_weakness", "")
    urgency = lead.get("urgency", {})
    urgency_level = urgency.get("level", "")
    est_daily = lead.get("estimated_daily_payment", 0)
    est_monthly = lead.get("estimated_monthly_obligation", 0)
    est_remaining = lead.get("estimated_remaining_balance", 0)
    est_months_left = lead.get("estimated_months_remaining", 0)
    reviews = lead.get("google_review_count", 0)
    funding_amount = lead.get("estimated_funding_amount", "")

    # ── Funder-specific opener ──────────────────────────────────
    if funder_name:
        opener = f"I noticed {name} has a funding position with {funder_name}"
        if filing_age:
            opener += f" from about {int(filing_age)} months ago"
        opener += "."
    elif filing_age:
        opener = f"I noticed {name} took on some business funding about {int(filing_age)} months ago."
    else:
        opener = f"I came across {name} and wanted to reach out."

    # ── Urgency-calibrated hook ─────────────────────────────────
    if urgency_level == "critical":
        timing_hook = (
            " That deal is likely wrapping up right now — which means "
            "you're probably either getting renewal calls or thinking about next steps."
        )
    elif urgency_level == "high":
        timing_hook = (
            " You're in the sweet spot where it makes sense to see what's "
            "out there before your current funder locks you into another round at the same rate."
        )
    elif urgency_level == "medium":
        timing_hook = (
            " You've still got a few months, which is actually the best time "
            "to line up better terms — before everyone starts calling."
        )
    elif urgency_level == "likely_paid_off":
        timing_hook = (
            " If you've already paid that off, you already know how the "
            "process works. Next time around, the terms should be better."
        )
    else:
        timing_hook = ""

    # ── Payment comparison (the real differentiator) ────────────
    savings_line = ""
    if est_daily and est_daily > 100:
        # Estimate 20% savings (conservative)
        better_daily = est_daily * 0.80
        monthly_savings = (est_daily - better_daily) * 22
        savings_line = (
            f"\n\nRight now you're probably paying somewhere around "
            f"${est_daily:,.0f}/day. Based on what we're seeing for "
            f"{'businesses in ' + location if location else 'similar businesses'}, "
            f"we could likely get that closer to ${better_daily:,.0f}/day — "
            f"that's roughly ${monthly_savings:,.0f}/mo back in your pocket."
        )
    elif est_monthly and est_monthly > 2000:
        better_monthly = est_monthly * 0.80
        monthly_savings = est_monthly - better_monthly
        savings_line = (
            f"\n\nWe've been saving businesses like yours around "
            f"${monthly_savings:,.0f}/mo on their funding costs. "
            f"Takes about 5 minutes to see if the numbers work for {name}."
        )

    # ── Funder-specific competitive hook ────────────────────────
    competitive_line = ""
    if funder_positioning:
        competitive_line = f"\n\n{funder_positioning}"
    elif funder_weakness:
        competitive_line = f"\n\nJust so you know — {funder_weakness.lower()}"

    # ── Industry hook ───────────────────────────────────────────
    industry_hook = _get_industry_hook(industry)

    # ── Review credibility ──────────────────────────────────────
    review_note = ""
    if reviews >= 50:
        review_note = f" Your {reviews} reviews tell me you're running a solid operation."

    # ── Assemble ────────────────────────────────────────────────
    subject = f"Quick question about {name}"

    body = (
        f"Hi,\n\n"
        f"{opener}{timing_hook}\n\n"
        f"{industry_hook}{review_note}"
        f"{savings_line}"
        f"{competitive_line}\n\n"
        f"Would it make sense to do a quick comparison? No cost, no obligation — "
        f"just want to see if we can do better than what's in place.\n\n"
        f"— Today Capital Group"
    )

    # ── SMS variant ─────────────────────────────────────────────
    sms_parts = [f"Hey, this is TCG."]
    if funder_name:
        sms_parts.append(f"I saw {name} has funding with {funder_name}.")
    else:
        sms_parts.append(f"I saw {name} might be coming up on a funding renewal.")

    if est_daily and est_daily > 100:
        better_daily = est_daily * 0.80
        sms_parts.append(f"We're getting businesses like yours from ~${est_daily:,.0f}/day down to ~${better_daily:,.0f}/day.")
    else:
        sms_parts.append("We've been getting better terms for businesses like yours lately.")

    sms_parts.append("Worth a quick look?")

    return {
        "subject": subject,
        "body": body,
        "sms_variant": " ".join(sms_parts),
        "personalization_notes": _build_notes(lead),
    }


# ═══════════════════════════════════════════════════════════════
# CONSOLIDATION — stacked positions, high daily obligation
# ═══════════════════════════════════════════════════════════════

def _consolidation_email(lead: dict, name: str, industry: str, location: str) -> dict:
    """
    Template for stacked merchants. Shows combined obligation,
    names funders, and pitches single-payment consolidation.
    """
    filing_age = lead.get("ucc_filing_age_months") or lead.get("filing_age_months") or 0
    funder_name = lead.get("funder_display_name") or lead.get("secured_party", "")
    position_count = lead.get("position_count", 1) or 1
    other_funders = lead.get("other_funders", [])
    est_daily = lead.get("estimated_daily_payment", 0)
    est_monthly = lead.get("estimated_monthly_obligation", 0)
    est_remaining = lead.get("estimated_remaining_balance", 0)
    reviews = lead.get("google_review_count", 0)
    urgency = lead.get("urgency", {})
    monthly_revenue = lead.get("monthly_revenue", 0)

    # ── Stacking-aware opener ───────────────────────────────────
    if position_count >= 3:
        all_funders = _collect_funder_names(funder_name, other_funders)
        if all_funders:
            opener = (
                f"I noticed {name} has {position_count} active funding positions"
                f" — {', '.join(all_funders[:3])}."
                f" That's a lot of daily debits hitting your account."
            )
        else:
            opener = (
                f"I noticed {name} has {position_count} active funding positions. "
                f"That's a lot of daily debits hitting your account."
            )
    elif position_count == 2:
        all_funders = _collect_funder_names(funder_name, other_funders)
        if all_funders:
            opener = (
                f"I saw {name} has a couple of funding positions "
                f"— looks like {' and '.join(all_funders[:2])}."
            )
        else:
            opener = (
                f"I saw {name} has a couple of active funding positions."
            )
    elif funder_name:
        opener = (
            f"I noticed {name} has a position with {funder_name} "
            f"from about {int(filing_age)} months ago."
        )
    else:
        opener = f"I work with businesses that have multiple funding positions."

    # ── Payment strain (the money hook) ─────────────────────────
    strain_line = ""
    if est_daily and est_daily > 100 and position_count >= 2:
        # Estimate combined daily across positions
        combined_daily = est_daily * position_count * 0.85  # overlap discount
        consolidated_daily = combined_daily * 0.60  # consolidation typically saves 30-40%
        daily_savings = combined_daily - consolidated_daily
        monthly_savings = daily_savings * 22

        strain_line = (
            f"\n\nWith {position_count} positions, you're probably looking at "
            f"somewhere around ${combined_daily:,.0f}/day in combined debits. "
            f"We can typically roll that into a single payment around "
            f"${consolidated_daily:,.0f}/day — "
            f"that's about ${monthly_savings:,.0f}/mo back in cash flow."
        )
    elif est_monthly and est_monthly > 3000:
        consolidated_monthly = est_monthly * 0.65
        monthly_savings = est_monthly - consolidated_monthly
        strain_line = (
            f"\n\nConsolidating could save you roughly ${monthly_savings:,.0f}/mo "
            f"and give you one predictable payment instead of "
            f"{position_count} separate ones."
        )
    elif position_count >= 2:
        strain_line = (
            f"\n\nConsolidating {position_count} positions into one lower "
            f"payment usually frees up serious cash flow — and it's a lot "
            f"simpler to manage."
        )

    # ── Revenue strain callout ──────────────────────────────────
    revenue_line = ""
    if monthly_revenue and est_monthly and monthly_revenue > 0:
        strain_pct = (est_monthly / monthly_revenue) * 100
        if strain_pct > 30:
            revenue_line = (
                f"\n\nWhen funding payments eat up {strain_pct:.0f}%+ of revenue, "
                f"it squeezes everything else. That's fixable."
            )

    # ── Industry hook ───────────────────────────────────────────
    industry_hook = _get_industry_hook(industry)

    # ── Assemble ────────────────────────────────────────────────
    first_word = name.split()[0] if name else ""
    subject = f"Simplify your payments, {first_word}?" if first_word else "Simplify your funding payments?"

    body = (
        f"Hi,\n\n"
        f"{opener}"
        f"{strain_line}"
        f"{revenue_line}\n\n"
        f"{industry_hook}"
        f"It takes about 5 minutes to see if the numbers work. "
        f"Want me to run a quick comparison?\n\n"
        f"— Today Capital Group"
    )

    # ── SMS ─────────────────────────────────────────────────────
    sms_parts = [f"Hey, TCG here."]
    if position_count >= 2:
        sms_parts.append(
            f"I saw {name} has {position_count} funding positions."
            f" We consolidate those into one lower payment."
        )
    else:
        sms_parts.append(
            f"If {name} is making multiple daily payments on funding, "
            f"we can usually consolidate into one lower payment."
        )
    sms_parts.append("Want me to check what's possible?")

    return {
        "subject": subject,
        "body": body,
        "sms_variant": " ".join(sms_parts),
        "personalization_notes": _build_notes(lead),
    }


# ═══════════════════════════════════════════════════════════════
# NEW FUNDING — no existing MCA detected
# ═══════════════════════════════════════════════════════════════

def _new_funding_email(lead: dict, name: str, industry: str, location: str) -> dict:
    """Template for new funding prospects with intent signals."""
    reviews = lead.get("google_review_count", 0)
    employees = lead.get("employee_count", 0)
    rating = lead.get("google_rating", 0)
    snippet = lead.get("snippet", "")
    source = lead.get("source", "")
    funding_amount = lead.get("estimated_funding_amount", "")

    # ── Intent-signal opener ────────────────────────────────────
    # If we found them through search, reference what we saw
    growth_signals = _detect_growth_signals(snippet)

    if growth_signals and source == "inbound-discovery":
        signal_str = growth_signals[0]
        opener = (
            f"I came across {name}"
            f"{f' in {location}' if location else ''}"
            f" — it looks like you're {signal_str}."
            f" That usually means capital is on the mind."
        )
    elif reviews >= 100:
        opener = (
            f"I saw {name} has {reviews} reviews"
            f"{f' in {location}' if location else ''}"
            f" — clearly you're running a busy operation."
        )
    elif rating and rating >= 4.5 and reviews >= 20:
        opener = (
            f"{name} has a {rating}-star rating with {reviews} reviews"
            f"{f' in {location}' if location else ''}"
            f" — that kind of reputation means you're doing things right."
        )
    elif employees and employees >= 10:
        opener = (
            f"I came across {name}"
            f"{f' in {location}' if location else ''}"
            f" — a {employees}-person operation has real capital needs."
        )
    else:
        opener = (
            f"I came across {name}"
            f"{f' in {location}' if location else ''}"
            f" and wanted to reach out."
        )

    # ── Industry hook ───────────────────────────────────────────
    industry_hook = _get_industry_hook(industry)

    # ── Funding amount range ────────────────────────────────────
    amount_mention = ""
    if funding_amount and funding_amount != "TBD":
        amount_mention = (
            f"Based on what I can see, you'd likely qualify for "
            f"somewhere in the {funding_amount} range with approval "
            f"in 24-48 hours.\n\n"
        )
    else:
        amount_mention = (
            "We do $15K to $500K with approvals in 24-48 hours.\n\n"
        )

    # ── Assemble ────────────────────────────────────────────────
    subject = f"Business funding for {name}"

    body = (
        f"Hi,\n\n"
        f"{opener}\n\n"
        f"{industry_hook}"
        f"If {name} ever needs working capital — equipment, payroll, "
        f"inventory, expansion — {amount_mention}"
        f"No obligation to see what you qualify for. "
        f"Want me to send over some numbers?\n\n"
        f"— Today Capital Group"
    )

    # ── SMS ─────────────────────────────────────────────────────
    sms_parts = [f"Hi, TCG here."]
    sms_parts.append(
        f"We help businesses"
        f"{' in ' + location if location else ''} "
        f"get fast working capital"
        f"{f' ({funding_amount})' if funding_amount and funding_amount != 'TBD' else ' ($15K-$500K)'},"
        f" 24hr approval."
    )
    sms_parts.append(f"Would {name} ever need something like that?")

    return {
        "subject": subject,
        "body": body,
        "sms_variant": " ".join(sms_parts),
        "personalization_notes": _build_notes(lead),
    }


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def _get_industry_hook(industry: str) -> str:
    """Return an industry-specific empathy line."""
    ind = industry.lower() if industry else ""
    if "truck" in ind or "transport" in ind or "logistic" in ind:
        return "Cash flow in trucking is unpredictable — fuel costs, broker delays, maintenance. I get it. "
    elif "construct" in ind or "roofing" in ind or "plumb" in ind or "electric" in ind or "hvac" in ind:
        return "Construction ties up capital for weeks before you see a dime. That's just the reality. "
    elif "restaurant" in ind or "food" in ind:
        return "Restaurants mean constant reinvestment — equipment, inventory, staffing, rent. "
    elif "auto" in ind or "mechanic" in ind:
        return "Auto shops need parts and equipment upfront but don't get paid until the job's done. "
    elif "medical" in ind or "dental" in ind or "health" in ind:
        return "Healthcare billing cycles mean cash flow doesn't always match when bills are due. "
    elif "landscap" in ind:
        return "Landscaping is seasonal — you need capital when the work comes in, not months later. "
    elif "retail" in ind:
        return "Retail means keeping inventory stocked and rent paid whether sales are up or down. "
    elif "manufactur" in ind:
        return "Manufacturing runs on materials and payroll upfront, with payment 30-90 days out. "
    else:
        return "Running a business means there's always something that needs capital. "


def _detect_growth_signals(snippet: str) -> list[str]:
    """Detect growth/intent signals from search snippets or descriptions."""
    if not snippet:
        return []
    snippet_lower = snippet.lower()
    signals = []
    signal_map = {
        "hiring": "hiring and growing your team",
        "expanding": "expanding",
        "new location": "opening a new location",
        "second location": "opening another location",
        "equipment": "investing in equipment",
        "franchise": "looking at franchise opportunities",
        "renovation": "doing renovations",
        "growing": "in growth mode",
        "construction project": "taking on new projects",
        "fleet": "growing your fleet",
        "new truck": "adding to your fleet",
    }
    for keyword, description in signal_map.items():
        if keyword in snippet_lower:
            signals.append(description)
    return signals


def _collect_funder_names(primary_funder: str, other_funders: list) -> list[str]:
    """Collect unique funder display names from primary + other funders list."""
    names = []
    if primary_funder:
        names.append(primary_funder)
    for f in (other_funders or []):
        display = f.get("display_name") or f.get("funder", "")
        if display and display not in names:
            names.append(display)
    return names


def _build_notes(lead: dict) -> list[str]:
    """Build a list of personalization notes for sales team context."""
    notes = []
    funder = lead.get("funder_display_name") or lead.get("secured_party", "")
    if funder:
        notes.append(f"Current funder: {funder}")

    funder_weakness = lead.get("funder_weakness", "")
    if funder_weakness:
        notes.append(f"Funder weakness: {funder_weakness}")

    position_count = lead.get("position_count", 1) or 1
    if position_count > 1:
        notes.append(f"Stacked: {position_count} positions")

    urgency = lead.get("urgency", {})
    if urgency.get("message"):
        notes.append(f"Timing: {urgency['message']}")

    est_daily = lead.get("estimated_daily_payment", 0)
    if est_daily:
        notes.append(f"Est. daily payment: ${est_daily:,.0f}")

    est_remaining = lead.get("estimated_remaining_balance", 0)
    if est_remaining:
        notes.append(f"Est. remaining balance: ${est_remaining:,.0f}")

    funder_tier = lead.get("funder_tier")
    if funder_tier and funder_tier >= 2:
        notes.append(f"Tier-{funder_tier} funder — likely overpaying")

    return notes


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    test_leads = [
        {
            "business_name": "Rodriguez Trucking LLC",
            "industry": "trucking",
            "business_focus": "MCA Renewal",
            "ucc_filing_age_months": 9,
            "secured_party": "Yellowstone Capital",
            "funder_display_name": "Yellowstone Capital",
            "funder_tier": 1,
            "funder_weakness": "Rates creep up on renewals. Merchants often overpay on 2nd+ position.",
            "funder_positioning": "We consistently beat Yellowstone renewals by 15-20% on factor rate.",
            "urgency": {"level": "high", "label": "Prime renewal window", "priority": 2,
                        "message": "classic renewal window — merchant is thinking about their options"},
            "estimated_daily_payment": 680,
            "estimated_monthly_obligation": 14960,
            "estimated_remaining_balance": 18700,
            "estimated_months_remaining": 3.0,
            "estimated_funding_amount": "$100K-$250K",
            "city": "Miami",
            "state": "FL",
            "google_review_count": 62,
            "monthly_revenue": 65000,
        },
        {
            "business_name": "Tony's Pizza Kitchen",
            "industry": "restaurant",
            "business_focus": "Consolidation",
            "ucc_filing_age_months": 14,
            "secured_party": "Forward Financing",
            "funder_display_name": "Forward Financing",
            "funder_tier": 2,
            "position_count": 3,
            "other_funders": [
                {"funder": "Libertas Funding", "display_name": "Libertas Funding"},
                {"funder": "Fundkite", "display_name": "Fundkite"},
            ],
            "urgency": {"level": "likely_paid_off", "priority": 4,
                        "message": "probably done paying — they know MCA works, ripe for next round"},
            "estimated_daily_payment": 520,
            "estimated_monthly_obligation": 11440,
            "city": "Brooklyn",
            "state": "NY",
            "google_review_count": 230,
            "monthly_revenue": 35000,
        },
        {
            "business_name": "Apex Plumbing Services",
            "industry": "plumbing",
            "business_focus": "New Funding",
            "city": "Dallas",
            "state": "TX",
            "google_review_count": 35,
            "google_rating": 4.7,
            "employee_count": 8,
            "estimated_funding_amount": "$50K-$150K",
            "snippet": "Now hiring experienced plumbers. Expanding to Fort Worth area.",
            "source": "inbound-discovery",
        },
    ]

    for lead in test_leads:
        result = generate_outreach_email(lead)
        print(f"\n{'='*60}")
        print(f"Business: {lead['business_name']} ({lead.get('business_focus', 'N/A')})")
        print(f"Subject:  {result['subject']}")
        print(f"\n{result['body']}")
        print(f"\nSMS: {result['sms_variant']}")
        if result.get("personalization_notes"):
            print(f"\nSales Notes: {result['personalization_notes']}")
