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

Compliance:
  - Every email ends with sender identity, postal address and an opt-out
    line (CAN-SPAM); every SMS ends with STOP language (TCPA practice).
  - Funder "positioning" lines are internal talking points with
    comparative claims about named competitors. They go in the sales
    notes for reps, never into merchant-facing copy.
  - Savings figures are estimates, so they're phrased conditionally.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from tools.parsing import as_number, parse_money

SMS_OPT_OUT = "Reply STOP to opt out."


def generate_outreach_email(lead: dict, use_llm: bool = False) -> dict:
    """
    Generate a personalized first-touch email for an MCA prospect.

    Args:
        lead: dict with business data, funder intelligence, payment
              estimates, urgency, and stacking info from the pipeline.
        use_llm: if True (and EMAIL_LLM_PERSONALIZE / ANTHROPIC_API_KEY are
              set), have Claude rewrite the template draft. Falls back to
              the template on any failure.

    Returns:
        dict with subject, body, sms_variant, personalization_notes,
        and generator ("template" or "claude").
    """
    name = (lead.get("business_name") or "").strip() or "your business"
    industry = lead.get("industry") or ""
    focus = lead.get("business_focus") or "New Funding"
    city = lead.get("city") or ""
    state = lead.get("state") or ""

    location = f"{city}, {state}" if city and state else (city or state or "")

    if focus == "MCA Renewal":
        draft = _renewal_email(lead, name, industry, location)
    elif focus == "Consolidation":
        draft = _consolidation_email(lead, name, industry, location)
    else:
        draft = _new_funding_email(lead, name, industry, location)

    draft["generator"] = "template"
    if use_llm:
        from tools.llm_personalizer import personalize_with_claude
        rewritten = personalize_with_claude(lead, draft)
        if rewritten:
            draft.update(rewritten)
            draft["generator"] = "claude"

    draft["body"] = _with_greeting(lead, draft["body"]) + _email_footer()
    draft["sms_variant"] = f"{draft['sms_variant'].rstrip()} {SMS_OPT_OUT}"
    if not config.SENDER_ADDRESS:
        draft["personalization_notes"].append(
            "COMPLIANCE: SENDER_ADDRESS not set — add a postal address before sending"
        )
    return draft


def _with_greeting(lead: dict, body: str) -> str:
    """Replace the generic 'Hi,' with the contact's first name when we have it."""
    first = (lead.get("first_name") or "").strip()
    body = body.lstrip()
    if body.startswith("Hi,"):
        body = body[3:].lstrip("\n")
    return f"Hi {first},\n\n{body}" if first else f"Hi,\n\n{body}"


def _signature() -> str:
    parts = [p for p in (config.SENDER_NAME, config.SENDER_COMPANY) if p]
    if config.SENDER_PHONE:
        parts.append(config.SENDER_PHONE)
    return "— " + "\n".join(parts) if parts else ""


def _email_footer() -> str:
    lines = ["", "", _signature(), "", "---"]
    if config.SENDER_ADDRESS:
        lines.append(f"{config.SENDER_COMPANY} · {config.SENDER_ADDRESS}")
    lines.append(config.OPT_OUT_TEXT)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# RENEWAL — they have one position approaching maturity
# ═══════════════════════════════════════════════════════════════

def _renewal_email(lead: dict, name: str, industry: str, location: str) -> dict:
    """
    Template for businesses in the MCA renewal window.
    Names the funder, shows estimated payments, and offers rate comparison.
    """
    filing_age = as_number(lead.get("ucc_filing_age_months") or lead.get("filing_age_months"))
    funder_name = lead.get("funder_display_name") or lead.get("secured_party") or ""
    urgency = lead.get("urgency") or {}
    urgency_level = urgency.get("level", "")
    est_daily = as_number(lead.get("estimated_daily_payment"))
    est_monthly = as_number(lead.get("estimated_monthly_obligation"))
    reviews = int(as_number(lead.get("google_review_count")))

    # ── Funder-specific opener ──────────────────────────────────
    if funder_name:
        opener = f"I noticed {name} has a funding position with {funder_name}"
        if filing_age >= 1:
            opener += f" from about {int(filing_age)} months ago"
        opener += "."
    elif filing_age >= 1:
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
    # Estimates come from typical terms for the funder, not the merchant's
    # actual contract — so the numbers are framed as "if", not as fact.
    savings_line = ""
    if est_daily > 100:
        better_daily = est_daily * 0.80
        monthly_savings = (est_daily - better_daily) * 22
        savings_line = (
            f"\n\nDeals like that often run somewhere around "
            f"${est_daily:,.0f}/day. If we can get a renewal closer to "
            f"${better_daily:,.0f}/day, that's roughly ${monthly_savings:,.0f}/mo "
            f"back in your pocket."
        )
    elif est_monthly > 2000:
        monthly_savings = est_monthly * 0.20
        savings_line = (
            f"\n\nEven a 20% better rate on a deal that size is around "
            f"${monthly_savings:,.0f}/mo. Takes about 5 minutes to see if "
            f"the numbers work for {name}."
        )

    # ── Industry hook ───────────────────────────────────────────
    industry_hook = _get_industry_hook(industry)

    # ── Review credibility ──────────────────────────────────────
    review_note = ""
    if reviews >= 50:
        review_note = f"Your {reviews} reviews tell me you're running a solid operation."

    # ── Assemble ────────────────────────────────────────────────
    subject = f"Quick question about {name}"

    body = (
        f"Hi,\n\n"
        f"{opener}{timing_hook}\n\n"
        f"{industry_hook}{review_note}"
        f"{savings_line}\n\n"
        f"Would it make sense to do a quick comparison? No cost, no obligation — "
        f"just want to see if we can do better than what's in place."
    )

    # ── SMS variant ─────────────────────────────────────────────
    sms_parts = [f"Hey, this is {config.SENDER_COMPANY}."]
    if funder_name:
        sms_parts.append(f"I saw {name} has funding with {funder_name}.")
    else:
        sms_parts.append(f"I saw {name} might be coming up on a funding renewal.")

    sms_parts.append("We may be able to beat your renewal terms.")

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
    filing_age = as_number(lead.get("ucc_filing_age_months") or lead.get("filing_age_months"))
    funder_name = lead.get("funder_display_name") or lead.get("secured_party") or ""
    position_count = int(as_number(lead.get("position_count"), 1) or 1)
    other_funders = lead.get("other_funders") or []
    est_daily = as_number(lead.get("estimated_daily_payment"))
    est_monthly = as_number(lead.get("estimated_monthly_obligation"))
    # Review-count revenue guesses are too rough to quote back to a merchant
    monthly_revenue = (
        0 if lead.get("revenue_is_estimate") else parse_money(lead.get("monthly_revenue"))
    )

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
    elif funder_name and filing_age >= 1:
        opener = (
            f"I noticed {name} has a position with {funder_name} "
            f"from about {int(filing_age)} months ago."
        )
    elif funder_name:
        opener = f"I noticed {name} has a funding position with {funder_name}."
    else:
        opener = f"I came across {name} and wanted to reach out."

    # ── Payment strain (the money hook) ─────────────────────────
    strain_line = ""
    if est_daily > 100 and position_count >= 2:
        # Estimate combined daily across positions
        combined_daily = est_daily * position_count * 0.85  # overlap discount
        consolidated_daily = combined_daily * 0.60  # consolidation typically saves 30-40%
        daily_savings = combined_daily - consolidated_daily
        monthly_savings = daily_savings * 22

        strain_line = (
            f"\n\nWith {position_count} positions, combined debits can easily "
            f"run ${combined_daily:,.0f}/day or more. Rolling that into a single, "
            f"longer-term payment around ${consolidated_daily:,.0f}/day would "
            f"free up roughly ${monthly_savings:,.0f}/mo in cash flow."
        )
    elif est_monthly > 3000 and position_count >= 2:
        monthly_savings = est_monthly * 0.35
        strain_line = (
            f"\n\nConsolidating could free up something like "
            f"${monthly_savings:,.0f}/mo and give you one predictable payment "
            f"instead of {position_count} separate ones."
        )
    elif position_count >= 2:
        strain_line = (
            f"\n\nConsolidating {position_count} positions into one lower "
            f"payment usually frees up serious cash flow — and it's a lot "
            f"simpler to manage."
        )

    # ── Revenue strain callout ──────────────────────────────────
    revenue_line = ""
    if monthly_revenue > 0 and est_monthly > 0:
        strain_pct = (est_monthly / monthly_revenue) * 100
        if strain_pct > 30:
            revenue_line = (
                f"\n\nWhen funding payments eat up {strain_pct:.0f}%+ of revenue, "
                f"it squeezes everything else. That's fixable."
            )

    # ── Industry hook ───────────────────────────────────────────
    industry_hook = _get_industry_hook(industry)

    # ── Assemble ────────────────────────────────────────────────
    first_name = (lead.get("first_name") or "").strip()
    subject = (
        f"Simplify your payments, {first_name}?" if first_name
        else f"One payment instead of {position_count} for {name}?" if position_count >= 2
        else f"Quick question about {name}"
    )

    body = (
        f"Hi,\n\n"
        f"{opener}"
        f"{strain_line}"
        f"{revenue_line}\n\n"
        f"{industry_hook}"
        f"It takes about 5 minutes to see if the numbers work. "
        f"Want me to run a quick comparison?"
    )

    # ── SMS ─────────────────────────────────────────────────────
    sms_parts = [f"Hey, {config.SENDER_COMPANY} here."]
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
    reviews = int(as_number(lead.get("google_review_count")))
    employees = int(as_number(lead.get("employee_count")))
    rating = as_number(lead.get("google_rating"))
    snippet = lead.get("snippet") or ""
    source = lead.get("source") or ""
    funding_amount = lead.get("estimated_funding_amount") or ""

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
            f"businesses your size typically see options in the "
            f"{funding_amount} range, often with a decision in 24-48 hours.\n\n"
        )
    else:
        amount_mention = (
            "we work with options from $15K to $500K, often with a decision in 24-48 hours.\n\n"
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
        f"Want me to send over some numbers?"
    )

    # ── SMS ─────────────────────────────────────────────────────
    sms_parts = [f"Hi, {config.SENDER_COMPANY} here."]
    sms_parts.append(
        f"We help businesses"
        f"{' in ' + location if location else ''} "
        f"get fast working capital"
        f"{f' ({funding_amount})' if funding_amount and funding_amount != 'TBD' else ' ($15K-$500K)'},"
        f" with quick decisions."
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

    funder_weakness = lead.get("funder_weakness") or ""
    if funder_weakness:
        notes.append(f"Funder weakness: {funder_weakness}")

    funder_positioning = lead.get("funder_positioning") or ""
    if funder_positioning:
        notes.append(f"Talking point (verbal only): {funder_positioning}")

    position_count = int(as_number(lead.get("position_count"), 1) or 1)
    if position_count > 1:
        notes.append(f"Stacked: {position_count} positions")

    urgency = lead.get("urgency") or {}
    if urgency.get("message"):
        notes.append(f"Timing: {urgency['message']}")

    est_daily = as_number(lead.get("estimated_daily_payment"))
    if est_daily:
        notes.append(f"Est. daily payment (from typical terms): ${est_daily:,.0f}")

    est_remaining = as_number(lead.get("estimated_remaining_balance"))
    if est_remaining:
        notes.append(f"Est. remaining balance: ${est_remaining:,.0f}")

    best_match = lead.get("best_funder_match")
    if best_match:
        notes.append(f"Best-fit funder: {best_match}")

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
