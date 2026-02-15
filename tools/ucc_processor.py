"""
UCC Filing Processor Tool.

Reads UCC filing data from the local SQLite database
and identifies MCA renewal/consolidation opportunities.

Enriches each filing with:
  - Funder competitive intelligence (rates, terms, weaknesses)
  - Estimated advance amount and payoff date
  - Estimated current daily payment
  - Urgency level for outreach timing
  - Multi-filing history per business (stacking detection)
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH, MCA_LENDERS, MCA_LENDER_PROFILES
from data.database import get_connection, get_unprocessed_filings


def _get_funder_profile(secured_party: str) -> dict:
    """Look up competitive intelligence for a funder from the secured party name."""
    secured_lower = secured_party.lower()
    for lender_key, profile in MCA_LENDER_PROFILES.items():
        if lender_key in secured_lower:
            return profile
    return {}


def _estimate_advance_and_payments(
    funder_profile: dict,
    filing_age_months: float,
    monthly_revenue: float = 0,
) -> dict:
    """
    Reverse-engineer what the merchant is likely paying based on
    the funder's known typical terms and filing age.
    """
    if not funder_profile:
        return {}

    # Estimate original advance amount (typical = 1-1.5x monthly revenue)
    # If we don't have revenue, use funder's min_revenue * 3 as rough estimate
    if monthly_revenue > 0:
        est_advance = monthly_revenue * 1.2
    else:
        est_advance = funder_profile.get("min_revenue", 15000) * 3

    # Estimate factor rate (midpoint of funder's range)
    rate_low, rate_high = funder_profile.get("typical_factor_range", [1.30, 1.45])
    est_factor = (rate_low + rate_high) / 2

    # Estimate term (midpoint)
    term_low, term_high = funder_profile.get("typical_term_months", [6, 12])
    est_term = (term_low + term_high) / 2

    # Estimated total payback and daily payment
    total_payback = est_advance * est_factor
    daily_payment = total_payback / (est_term * 22)  # 22 business days/mo
    monthly_obligation = daily_payment * 22

    # Estimated remaining balance
    if filing_age_months and filing_age_months > 0:
        months_paid = min(filing_age_months, est_term)
        pct_paid = months_paid / est_term
        est_remaining = total_payback * (1 - pct_paid)
        est_months_left = max(0, est_term - months_paid)
    else:
        est_remaining = total_payback
        est_months_left = est_term

    # Estimated payoff date
    payoff_date = (datetime.now() + timedelta(days=est_months_left * 30)).strftime("%Y-%m-%d")

    return {
        "estimated_advance": round(est_advance, -2),
        "estimated_factor_rate": round(est_factor, 3),
        "estimated_total_payback": round(total_payback, 2),
        "estimated_daily_payment": round(daily_payment, 2),
        "estimated_monthly_obligation": round(monthly_obligation, 2),
        "estimated_remaining_balance": round(max(est_remaining, 0), 2),
        "estimated_months_remaining": round(max(est_months_left, 0), 1),
        "estimated_payoff_date": payoff_date,
        "estimated_term_months": round(est_term, 0),
    }


def _get_urgency_level(filing_age_months: float) -> dict:
    """
    Classify urgency for outreach timing.
    Tighter window = more urgent = more specific messaging.
    """
    if filing_age_months is None:
        return {"level": "unknown", "label": "Unknown", "priority": 5}

    if 10 <= filing_age_months <= 12:
        return {
            "level": "critical",
            "label": "Expiring within weeks",
            "priority": 1,
            "message": "deal is maturing right now — they'll be looking for next steps",
        }
    elif 8 <= filing_age_months < 10:
        return {
            "level": "high",
            "label": "Prime renewal window",
            "priority": 2,
            "message": "classic renewal window — merchant is thinking about their options",
        }
    elif 6 <= filing_age_months < 8:
        return {
            "level": "medium",
            "label": "Approaching renewal",
            "priority": 3,
            "message": "a few months out — good time for an intro before competitors reach out",
        }
    elif filing_age_months > 12:
        return {
            "level": "likely_paid_off",
            "label": "Likely paid off",
            "priority": 4,
            "message": "probably done paying — they know MCA works, ripe for next round",
        }
    else:
        return {
            "level": "early",
            "label": "Too early for renewal",
            "priority": 6,
            "message": "still in active repayment — nurture for later",
        }


def process_ucc_filings(
    state: str,
    days_back: int = 7,
    min_filing_age_months: float = 6,
    limit: int = 100,
) -> dict:
    """
    Process UCC filing data to identify MCA renewal and consolidation
    opportunities. Enriches each filing with funder intelligence,
    estimated payments, and urgency scoring.
    """
    conn = get_connection()

    try:
        filings = get_unprocessed_filings(
            conn, state=state, days_back=days_back, limit=limit
        )

        # Group filings by debtor name to detect stacking
        debtor_filings = {}
        for filing in filings:
            name = (filing.get("debtor_name") or "").strip().lower()
            if name:
                debtor_filings.setdefault(name, []).append(filing)

        results = []
        for filing in filings:
            secured = (filing.get("secured_party") or "").lower()
            is_mca = any(lender in secured for lender in MCA_LENDERS)

            filing_date = filing.get("filing_date")
            age_months = None
            if filing_date:
                try:
                    dt = datetime.fromisoformat(filing_date)
                    age_months = round((datetime.now() - dt).days / 30, 1)
                except (ValueError, TypeError):
                    pass

            in_renewal_window = (
                age_months is not None
                and min_filing_age_months <= age_months <= 12
            )

            # ── Funder competitive intelligence ──────────────────
            funder_profile = _get_funder_profile(filing.get("secured_party", ""))
            payment_estimates = _estimate_advance_and_payments(funder_profile, age_months)
            urgency = _get_urgency_level(age_months)

            # ── Multi-filing / stacking detection ────────────────
            debtor_name = (filing.get("debtor_name") or "").strip().lower()
            all_filings_for_debtor = debtor_filings.get(debtor_name, [])
            other_filings = [
                f for f in all_filings_for_debtor
                if f.get("id") != filing.get("id")
            ]
            position_count = 1 + len(other_filings)

            # Build history of other funders
            other_funders = []
            for other in other_filings:
                other_secured = other.get("secured_party", "")
                other_profile = _get_funder_profile(other_secured)
                other_funders.append({
                    "funder": other_secured,
                    "filing_date": other.get("filing_date"),
                    "display_name": other_profile.get("display_name", other_secured),
                })

            result = {
                "filing_id": filing.get("id"),
                "business_name": filing.get("debtor_name"),
                "secured_party": filing.get("secured_party"),
                "filing_date": filing_date,
                "filing_number": filing.get("filing_number"),
                "address": filing.get("debtor_address"),
                "city": filing.get("debtor_city"),
                "state": filing.get("debtor_state"),
                "zip": filing.get("debtor_zip"),
                "is_mca_lender": is_mca,
                "filing_age_months": age_months,
                "in_renewal_window": in_renewal_window,
                "secured_party_type": filing.get("secured_party_type"),
                # ── New competitive intelligence fields ──────────
                "funder_display_name": funder_profile.get("display_name", ""),
                "funder_tier": funder_profile.get("tier"),
                "funder_weakness": funder_profile.get("weakness", ""),
                "funder_positioning": funder_profile.get("positioning", ""),
                "funder_sweet_spot": funder_profile.get("sweet_spot", ""),
                "urgency": urgency,
                # ── Payment estimates ────────────────────────────
                **payment_estimates,
                # ── Stacking intelligence ────────────────────────
                "position_count": position_count,
                "other_funders": other_funders,
                "is_stacked": position_count > 1,
            }

            results.append(result)

        # Sort: urgency first, then MCA in renewal window, then by age
        results.sort(
            key=lambda r: (
                r["urgency"]["priority"],
                not r["in_renewal_window"],
                not r["is_mca_lender"],
                -(r["filing_age_months"] or 0),
            )
        )

        mca_count = sum(1 for r in results if r["is_mca_lender"])
        renewal_count = sum(1 for r in results if r["in_renewal_window"])
        stacked_count = sum(1 for r in results if r["is_stacked"])

        return {
            "state": state,
            "days_back": days_back,
            "total_filings": len(results),
            "mca_filings": mca_count,
            "in_renewal_window": renewal_count,
            "stacked_merchants": stacked_count,
            "filings": results,
        }

    finally:
        conn.close()


def get_filing_summary(states: list[str], days_back: int = 7) -> dict:
    """Get a summary across multiple states."""
    summary = {
        "states": {},
        "total_filings": 0,
        "total_mca": 0,
        "total_renewal": 0,
    }

    for state in states:
        result = process_ucc_filings(state, days_back=days_back)
        summary["states"][state] = {
            "total": result["total_filings"],
            "mca": result["mca_filings"],
            "renewal": result["in_renewal_window"],
        }
        summary["total_filings"] += result["total_filings"]
        summary["total_mca"] += result["mca_filings"]
        summary["total_renewal"] += result["in_renewal_window"]

    return summary


# ── Standalone usage ───────────────────────────────────────────
if __name__ == "__main__":
    from data.database import init_db
    init_db()
    result = process_ucc_filings("FL", days_back=30)
    print(json.dumps(result, indent=2))
