"""
UCC Filing Processor Tool.

Reads UCC filing data from the local SQLite database
and identifies MCA renewal/consolidation opportunities.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH, MCA_LENDERS
from data.database import get_connection, get_unprocessed_filings


def process_ucc_filings(
    state: str,
    days_back: int = 7,
    min_filing_age_months: float = 6,
    limit: int = 100,
) -> dict:
    """
    Process UCC filing data to identify MCA renewal and consolidation
    opportunities.

    Args:
        state: Two-letter state abbreviation (e.g., "FL").
        days_back: How many days back to look for filings.
        min_filing_age_months: Minimum age for renewal targeting.
        limit: Maximum number of filings to return.

    Returns:
        dict with total counts and a list of processed filing records.
    """
    conn = get_connection()

    try:
        # Get filings that haven't been turned into leads yet
        filings = get_unprocessed_filings(
            conn, state=state, days_back=days_back, limit=limit
        )

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

            results.append({
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
            })

        # Sort: MCA filings in renewal window first, then by age
        results.sort(
            key=lambda r: (
                not r["in_renewal_window"],
                not r["is_mca_lender"],
                -(r["filing_age_months"] or 0),
            )
        )

        mca_count = sum(1 for r in results if r["is_mca_lender"])
        renewal_count = sum(1 for r in results if r["in_renewal_window"])

        return {
            "state": state,
            "days_back": days_back,
            "total_filings": len(results),
            "mca_filings": mca_count,
            "in_renewal_window": renewal_count,
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
