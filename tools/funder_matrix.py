"""
Funder Matrix & Lead-to-Funder Matching Engine.

Maintains a matrix of MCA funder criteria and matches leads
to the best-fit funders based on:
  - Monthly revenue requirements
  - Time in business minimums
  - Industry restrictions
  - Credit score floors
  - Max positions (stacking tolerance)
  - Geographic restrictions
  - Product types offered (MCA, term loan, LOC, SBA)

Also supports funder-specific factor rate estimation and
multi-funder comparison for deal packaging.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# FUNDER MATRIX
# ═══════════════════════════════════════════════════════════════

# Each funder entry encodes their actual underwriting criteria.
# Update these as you learn each funder's real appetite.
FUNDER_MATRIX = [
    {
        "name": "Funder A (Tier 1 - Premium)",
        "slug": "funder_a",
        "products": ["MCA", "Term Loan"],
        "min_monthly_revenue": 25000,
        "min_years_in_business": 1.0,
        "min_credit_score": 550,
        "max_positions": 2,
        "max_funding_amount": 500000,
        "factor_rate_range": [1.15, 1.35],
        "term_months_range": [6, 18],
        "restricted_industries": ["firearms", "cannabis", "gambling", "adult"],
        "restricted_states": [],
        "approval_speed_days": 1,
        "notes": "Best rates for strong profiles. Requires 3mo bank statements.",
        "priority": 1,
    },
    {
        "name": "Funder B (Tier 1 - Volume)",
        "slug": "funder_b",
        "products": ["MCA"],
        "min_monthly_revenue": 15000,
        "min_years_in_business": 0.5,
        "min_credit_score": 500,
        "max_positions": 3,
        "max_funding_amount": 250000,
        "factor_rate_range": [1.20, 1.45],
        "term_months_range": [4, 12],
        "restricted_industries": ["firearms", "cannabis", "gambling"],
        "restricted_states": [],
        "approval_speed_days": 1,
        "notes": "High approval rate. Good for B-tier leads.",
        "priority": 2,
    },
    {
        "name": "Funder C (Tier 2 - Flexible)",
        "slug": "funder_c",
        "products": ["MCA", "Revenue-Based Financing"],
        "min_monthly_revenue": 10000,
        "min_years_in_business": 0.5,
        "min_credit_score": 450,
        "max_positions": 4,
        "max_funding_amount": 150000,
        "factor_rate_range": [1.25, 1.55],
        "term_months_range": [3, 9],
        "restricted_industries": ["cannabis", "gambling"],
        "restricted_states": [],
        "approval_speed_days": 1,
        "notes": "Flexible on credit. Good for merchants with existing positions.",
        "priority": 3,
    },
    {
        "name": "Funder D (Tier 2 - Startup Friendly)",
        "slug": "funder_d",
        "products": ["MCA"],
        "min_monthly_revenue": 10000,
        "min_years_in_business": 0.25,
        "min_credit_score": 480,
        "max_positions": 2,
        "max_funding_amount": 75000,
        "factor_rate_range": [1.30, 1.55],
        "term_months_range": [3, 6],
        "restricted_industries": ["firearms", "cannabis", "gambling", "crypto"],
        "restricted_states": [],
        "approval_speed_days": 2,
        "notes": "Will fund 3-month-old businesses. Good for newer merchants.",
        "priority": 4,
    },
    {
        "name": "Funder E (Tier 3 - Last Resort)",
        "slug": "funder_e",
        "products": ["MCA"],
        "min_monthly_revenue": 8000,
        "min_years_in_business": 0.25,
        "min_credit_score": 400,
        "max_positions": 5,
        "max_funding_amount": 50000,
        "factor_rate_range": [1.40, 1.75],
        "term_months_range": [3, 6],
        "restricted_industries": ["cannabis", "gambling"],
        "restricted_states": [],
        "approval_speed_days": 1,
        "notes": "High approval, high cost. Use when other options exhausted.",
        "priority": 5,
    },
    {
        "name": "SBA Lender (Term Loan)",
        "slug": "sba_lender",
        "products": ["SBA Loan", "Term Loan"],
        "min_monthly_revenue": 20000,
        "min_years_in_business": 2.0,
        "min_credit_score": 650,
        "max_positions": 0,
        "max_funding_amount": 350000,
        "factor_rate_range": [1.05, 1.12],
        "term_months_range": [24, 120],
        "restricted_industries": ["firearms", "cannabis", "gambling", "adult", "crypto"],
        "restricted_states": [],
        "approval_speed_days": 14,
        "notes": "Best rates but slow. For strong profiles that can wait.",
        "priority": 6,
    },
    {
        "name": "LOC Provider",
        "slug": "loc_provider",
        "products": ["Line of Credit"],
        "min_monthly_revenue": 15000,
        "min_years_in_business": 1.0,
        "min_credit_score": 600,
        "max_positions": 1,
        "max_funding_amount": 200000,
        "factor_rate_range": [1.0, 1.0],  # Interest-based, not factor
        "term_months_range": [12, 24],
        "restricted_industries": ["firearms", "cannabis", "gambling"],
        "restricted_states": [],
        "approval_speed_days": 5,
        "notes": "Revolving line. Only pay interest on drawn amount. Strong profiles only.",
        "priority": 7,
    },
]


# ═══════════════════════════════════════════════════════════════
# MATCHING ENGINE
# ═══════════════════════════════════════════════════════════════

def match_lead_to_funders(lead: dict) -> list[dict]:
    """
    Match a lead against all funders in the matrix.
    Returns a ranked list of matching funders with estimated terms.

    Args:
        lead: dict with monthly_revenue, years_in_business, industry,
              credit_score, existing_positions, state, etc.

    Returns:
        List of funder match dicts, sorted by priority (best first).
    """
    monthly_rev = _parse_revenue(lead.get("monthly_revenue", 0))
    yib = lead.get("years_in_business", 0) or 0
    credit_score = lead.get("credit_score", 0) or 0
    industry = (lead.get("industry", "") or "").lower()
    state = (lead.get("state", "") or "").upper()
    existing_positions = lead.get("existing_positions", 0) or 0

    matches = []

    for funder in FUNDER_MATRIX:
        reasons_pass = []
        reasons_fail = []

        # Revenue check
        if monthly_rev >= funder["min_monthly_revenue"]:
            reasons_pass.append(f"Revenue ${monthly_rev:,.0f}/mo meets ${funder['min_monthly_revenue']:,} min")
        else:
            reasons_fail.append(f"Revenue ${monthly_rev:,.0f}/mo below ${funder['min_monthly_revenue']:,} min")

        # Time in business
        if yib >= funder["min_years_in_business"]:
            reasons_pass.append(f"{yib}yr in business meets {funder['min_years_in_business']}yr min")
        else:
            reasons_fail.append(f"{yib}yr in business below {funder['min_years_in_business']}yr min")

        # Credit score (if we have it)
        if credit_score > 0:
            if credit_score >= funder["min_credit_score"]:
                reasons_pass.append(f"Credit {credit_score} meets {funder['min_credit_score']} min")
            else:
                reasons_fail.append(f"Credit {credit_score} below {funder['min_credit_score']} min")

        # Position count
        if existing_positions <= funder["max_positions"]:
            reasons_pass.append(f"{existing_positions} positions within {funder['max_positions']} max")
        else:
            reasons_fail.append(f"{existing_positions} positions exceeds {funder['max_positions']} max")

        # Industry restrictions
        if any(r in industry for r in funder["restricted_industries"]):
            reasons_fail.append(f"Industry '{industry}' is restricted")

        # State restrictions
        if funder["restricted_states"] and state in funder["restricted_states"]:
            reasons_fail.append(f"State {state} is restricted")

        # Determine match
        qualified = len(reasons_fail) == 0
        # Partial match = only failed on credit (which we might not have)
        partial = len(reasons_fail) == 1 and credit_score == 0 and "Credit" in reasons_fail[0]

        if qualified or partial:
            # Estimate funding amount
            est_amount = min(
                monthly_rev * 1.5,  # ~1.5x monthly rev is typical
                funder["max_funding_amount"],
            )

            # Estimate factor rate (better profiles get lower rates)
            rate_low, rate_high = funder["factor_rate_range"]
            if monthly_rev >= funder["min_monthly_revenue"] * 2 and yib >= 2:
                est_rate = rate_low
            elif monthly_rev >= funder["min_monthly_revenue"] * 1.5:
                est_rate = (rate_low + rate_high) / 2
            else:
                est_rate = rate_high

            matches.append({
                "funder": funder["name"],
                "slug": funder["slug"],
                "qualified": qualified,
                "partial_match": partial and not qualified,
                "products": funder["products"],
                "estimated_amount": round(est_amount, -2),  # Round to nearest $100
                "estimated_factor_rate": round(est_rate, 3),
                "estimated_total_payback": round(est_amount * est_rate, 2),
                "term_range_months": funder["term_months_range"],
                "approval_speed_days": funder["approval_speed_days"],
                "priority": funder["priority"],
                "reasons_pass": reasons_pass,
                "reasons_fail": reasons_fail,
                "notes": funder["notes"],
            })

    # Sort by priority (lowest = best)
    matches.sort(key=lambda m: m["priority"])

    return matches


def get_best_offer(lead: dict) -> Optional[dict]:
    """Get the single best funder match for a lead."""
    matches = match_lead_to_funders(lead)
    qualified = [m for m in matches if m["qualified"]]
    return qualified[0] if qualified else None


def generate_offer_comparison(lead: dict) -> dict:
    """
    Generate a full offer comparison table for a lead.
    Returns structured data suitable for presentation to a sales rep
    or inclusion in CRM notes.
    """
    matches = match_lead_to_funders(lead)
    qualified = [m for m in matches if m["qualified"]]
    partial = [m for m in matches if m.get("partial_match")]

    comparison = {
        "lead": {
            "business_name": lead.get("business_name", ""),
            "monthly_revenue": _parse_revenue(lead.get("monthly_revenue", 0)),
            "years_in_business": lead.get("years_in_business", 0),
            "credit_score": lead.get("credit_score", 0),
            "existing_positions": lead.get("existing_positions", 0),
            "industry": lead.get("industry", ""),
        },
        "qualified_funders": len(qualified),
        "partial_matches": len(partial),
        "offers": [],
        "recommendation": "",
    }

    for match in qualified:
        est_amount = match["estimated_amount"]
        est_rate = match["estimated_factor_rate"]
        term_low, term_high = match["term_range_months"]
        mid_term = (term_low + term_high) // 2

        # Compute estimated daily payment (business days)
        total_payback = est_amount * est_rate
        daily_payment = total_payback / (mid_term * 22)

        comparison["offers"].append({
            "funder": match["funder"],
            "products": match["products"],
            "estimated_amount": est_amount,
            "factor_rate": est_rate,
            "total_payback": round(total_payback, 2),
            "term_months": f"{term_low}-{term_high}",
            "estimated_daily_payment": round(daily_payment, 2),
            "approval_speed": f"{match['approval_speed_days']} day(s)",
            "cost_of_capital": f"{(est_rate - 1) * 100:.1f}%",
        })

    # Generate recommendation
    if len(qualified) >= 3:
        comparison["recommendation"] = (
            f"Strong profile — {len(qualified)} funders qualify. "
            f"Lead with {qualified[0]['funder']} for best rates, "
            f"use {qualified[1]['funder']} as backup."
        )
    elif len(qualified) >= 1:
        comparison["recommendation"] = (
            f"{len(qualified)} funder(s) qualify. "
            f"Best option: {qualified[0]['funder']}."
        )
    elif partial:
        comparison["recommendation"] = (
            f"No confirmed matches (credit score unknown). "
            f"{len(partial)} potential matches pending credit check."
        )
    else:
        comparison["recommendation"] = "No qualifying funders found for this profile."

    return comparison


def _parse_revenue(rev) -> float:
    """Parse revenue from various formats."""
    if isinstance(rev, (int, float)):
        return float(rev)
    if isinstance(rev, str):
        cleaned = rev.replace("$", "").replace(",", "").replace("/mo", "").strip()
        if "-" in cleaned:
            cleaned = cleaned.split("-")[0].strip()
        cleaned = cleaned.upper().replace("K", "000").replace("M", "000000")
        cleaned = cleaned.rstrip("+")
        try:
            return float(cleaned)
        except ValueError:
            return 0
    return 0


# ═══════════════════════════════════════════════════════════════
# FUNDER MATRIX MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def get_funder_matrix() -> list[dict]:
    """Return the current funder matrix."""
    return FUNDER_MATRIX


def get_funder_by_slug(slug: str) -> Optional[dict]:
    """Look up a specific funder by slug."""
    for f in FUNDER_MATRIX:
        if f["slug"] == slug:
            return f
    return None


def get_funder_summary() -> str:
    """Return a human-readable summary of the funder matrix."""
    lines = ["Funder Matrix Summary", "=" * 50]
    for f in FUNDER_MATRIX:
        lines.append(f"\n{f['name']}")
        lines.append(f"  Products: {', '.join(f['products'])}")
        lines.append(f"  Revenue min: ${f['min_monthly_revenue']:,}/mo")
        lines.append(f"  TIB min: {f['min_years_in_business']}yr")
        lines.append(f"  Credit min: {f['min_credit_score']}")
        lines.append(f"  Max positions: {f['max_positions']}")
        lines.append(f"  Max amount: ${f['max_funding_amount']:,}")
        rate_low, rate_high = f["factor_rate_range"]
        lines.append(f"  Factor rate: {rate_low:.2f} - {rate_high:.2f}")
        lines.append(f"  Speed: {f['approval_speed_days']} day(s)")
    return "\n".join(lines)


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    # Test with a sample lead
    test_lead = {
        "business_name": "Joe's Trucking LLC",
        "monthly_revenue": 65000,
        "years_in_business": 4,
        "credit_score": 580,
        "existing_positions": 1,
        "industry": "trucking",
        "state": "FL",
    }

    print("=== Funder Matching ===\n")
    comparison = generate_offer_comparison(test_lead)
    print(f"Business: {comparison['lead']['business_name']}")
    print(f"Revenue: ${comparison['lead']['monthly_revenue']:,.0f}/mo")
    print(f"Qualified funders: {comparison['qualified_funders']}")
    print(f"Recommendation: {comparison['recommendation']}\n")

    for offer in comparison["offers"]:
        print(f"  {offer['funder']}")
        print(f"    Amount: ${offer['estimated_amount']:,.0f}")
        print(f"    Rate: {offer['factor_rate']:.3f} ({offer['cost_of_capital']})")
        print(f"    Daily payment: ${offer['estimated_daily_payment']:,.2f}")
        print(f"    Speed: {offer['approval_speed']}")
        print()
