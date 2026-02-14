"""
MCA Lead Scoring Tool.

Scores business leads for MCA qualification on a 0-100 scale.
Weights reflect actual MCA deal patterns:
  - Revenue is the strongest predictor
  - UCC filing age indicates renewal timing
  - Industry correlates with approval rates
  - Time in business affects available products
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import HIGH_VALUE_INDUSTRIES


def score_mca_lead(args: dict) -> dict:
    """
    Score a business lead for MCA qualification.

    Args:
        args: dict with keys like business_name, industry,
              monthly_revenue, years_in_business, etc.

    Returns:
        dict with score (0-100), tier (A-D), reasons, and recommendation.
    """
    score = 0
    reasons = []

    # ── Revenue scoring (biggest weight: up to 25 pts) ─────────
    rev = args.get("monthly_revenue", 0)
    if isinstance(rev, str):
        # Handle strings like "$50K-$100K/mo" or "50000"
        rev = _parse_revenue(rev)
    if rev >= 50000:
        score += 25
        reasons.append(f"Strong revenue: ${rev:,.0f}/mo")
    elif rev >= 15000:
        score += 20
        reasons.append(f"Good revenue: ${rev:,.0f}/mo")
    elif rev >= 10000:
        score += 10
        reasons.append(f"Meets minimum revenue: ${rev:,.0f}/mo")
    elif rev > 0:
        reasons.append(f"Below minimum revenue: ${rev:,.0f}/mo")

    # ── Industry scoring (up to 15 pts) ────────────────────────
    industry = args.get("industry", "").lower()
    if any(ind in industry for ind in HIGH_VALUE_INDUSTRIES):
        score += 15
        reasons.append(f"High-value industry: {industry}")
    elif industry:
        score += 5

    # ── Time in business (up to 15 pts) ────────────────────────
    yib = args.get("years_in_business", 0) or 0
    if yib >= 2:
        score += 15
        reasons.append(f"{yib} years in business")
    elif yib >= 1:
        score += 8
        reasons.append(f"{yib} year(s) in business")
    elif yib > 0:
        score += 3
        reasons.append("Less than 1 year - limited options")

    # ── UCC / MCA renewal opportunity (up to 20 pts) ──────────
    ucc_age = args.get("ucc_filing_age_months", 0) or 0
    has_existing_mca = args.get("has_existing_mca", False)
    urgency_level = args.get("urgency", {}).get("level", "")

    if urgency_level == "critical":
        score += 20
        reasons.append(f"UCC filing {ucc_age:.0f}mo old - expiring NOW, immediate outreach")
    elif 6 <= ucc_age <= 12:
        score += 20
        reasons.append(f"UCC filing {ucc_age:.0f}mo old - prime renewal window")
    elif ucc_age > 12:
        score += 10
        reasons.append(f"UCC filing {ucc_age:.0f}mo old - likely paid off, refi candidate")
    elif has_existing_mca:
        score += 15
        reasons.append("Has existing MCA - consolidation opportunity")

    # ── Stacking distress (up to 15 pts) ─────────────────────
    position_count = args.get("position_count", 1) or 1
    est_monthly_obligation = args.get("estimated_monthly_obligation", 0) or 0

    if position_count >= 3:
        score += 15
        reasons.append(f"Stacked {position_count} positions - high consolidation urgency")
    elif position_count == 2:
        score += 8
        reasons.append("2 positions - consolidation opportunity")

    # Obligation-to-revenue strain
    if est_monthly_obligation > 0 and rev > 0:
        strain_pct = (est_monthly_obligation / rev) * 100
        if strain_pct > 35:
            score += 10
            reasons.append(f"Paying ~{strain_pct:.0f}% of revenue to MCA - actively strained")
        elif strain_pct > 20:
            score += 5
            reasons.append(f"MCA obligation ~{strain_pct:.0f}% of revenue")

    # ── Funder switching / shopping signals (up to 8 pts) ─────
    other_funders = args.get("other_funders", [])
    if other_funders and has_existing_mca:
        funder_names = set(
            f.get("funder", "").lower() for f in other_funders
        )
        current_funder = (args.get("secured_party") or "").lower()
        if current_funder and funder_names - {current_funder}:
            score += 8
            reasons.append("Funder-switching history - actively shopping for better terms")

    # ── High-cost funder bonus (up to 5 pts) ──────────────────
    funder_tier = args.get("funder_tier")
    if funder_tier and funder_tier >= 2 and has_existing_mca:
        score += 5
        reasons.append(f"Currently with tier-{funder_tier} funder - likely overpaying")

    # ── Discovery intent signals (up to 8 pts) ───────────────
    snippet = (args.get("snippet") or "").lower()
    source = args.get("source", "")
    if source == "inbound-discovery" and snippet:
        intent_keywords = [
            "hiring", "expanding", "new location", "equipment",
            "growing", "franchise", "capital", "funding",
        ]
        found_signals = [kw for kw in intent_keywords if kw in snippet]
        if found_signals:
            score += min(len(found_signals) * 4, 8)
            reasons.append(f"Growth signals: {', '.join(found_signals)}")

    # ── Google reviews as activity proxy (up to 10 pts) ────────
    reviews = args.get("google_review_count", 0) or 0
    if reviews >= 100:
        score += 10
        reasons.append(f"{reviews} reviews suggests established business")
    elif reviews >= 50:
        score += 7
        reasons.append(f"{reviews} reviews - solid customer base")
    elif reviews >= 20:
        score += 3

    # ── Employee count as revenue proxy (up to 5 pts) ──────────
    employees = args.get("employee_count", 0) or 0
    if employees >= 10:
        score += 5
        reasons.append(f"{employees} employees")
    elif employees >= 5:
        score += 3

    # ── Google rating bonus (up to 5 pts) ──────────────────────
    rating = args.get("google_rating", 0) or 0
    if rating >= 4.5 and reviews >= 20:
        score += 5
        reasons.append(f"{rating} star rating - well-regarded business")
    elif rating >= 4.0 and reviews >= 10:
        score += 3

    # ── Cap and classify ───────────────────────────────────────
    score = min(score, 100)

    if score >= 70:
        tier = "A"
    elif score >= 50:
        tier = "B"
    elif score >= 30:
        tier = "C"
    else:
        tier = "D"

    recommendations = {
        "A": "Immediate outreach - high-value prospect. Personalized email + call within 24hr.",
        "B": "Strong prospect. Add to priority email sequence.",
        "C": "Moderate prospect. Add to nurture sequence.",
        "D": "Low priority. Monitor for changes.",
    }

    # Determine business focus (more nuanced)
    if position_count >= 3:
        business_focus = "Consolidation"  # stacked = consolidation pitch
    elif position_count == 2 and est_monthly_obligation > 0 and rev > 0 and (est_monthly_obligation / rev) > 0.30:
        business_focus = "Consolidation"  # strained = consolidation pitch
    elif 6 <= ucc_age <= 12:
        business_focus = "MCA Renewal"
    elif has_existing_mca or ucc_age > 12:
        business_focus = "Consolidation"
    else:
        business_focus = "New Funding"

    return {
        "score": score,
        "tier": tier,
        "reasons": reasons,
        "recommendation": recommendations[tier],
        "business_focus": business_focus,
        "estimated_funding_amount": _estimate_funding_amount(rev),
    }


def _parse_revenue(rev_str: str) -> float:
    """Parse revenue strings like '$50K-$100K/mo' into a numeric value."""
    cleaned = rev_str.replace("$", "").replace(",", "").replace("/mo", "").strip()
    # Take the first number in a range
    if "-" in cleaned:
        cleaned = cleaned.split("-")[0].strip()
    cleaned = cleaned.upper().replace("K", "000").replace("M", "000000")
    try:
        return float(cleaned)
    except ValueError:
        return 0


def _estimate_funding_amount(monthly_revenue: float) -> str:
    """Estimate appropriate funding amount based on monthly revenue."""
    if monthly_revenue >= 100000:
        return "$200K-$500K"
    elif monthly_revenue >= 50000:
        return "$100K-$250K"
    elif monthly_revenue >= 25000:
        return "$50K-$150K"
    elif monthly_revenue >= 15000:
        return "$25K-$75K"
    elif monthly_revenue >= 10000:
        return "$15K-$50K"
    else:
        return "TBD"


# ── Standalone usage for testing ───────────────────────────────
if __name__ == "__main__":
    test_lead = {
        "business_name": "Joe's Trucking LLC",
        "industry": "trucking",
        "monthly_revenue": 65000,
        "years_in_business": 4,
        "has_existing_mca": True,
        "ucc_filing_age_months": 8,
        "google_review_count": 45,
        "employee_count": 12,
        "google_rating": 4.3,
    }
    result = score_mca_lead(test_lead)
    print(json.dumps(result, indent=2))
