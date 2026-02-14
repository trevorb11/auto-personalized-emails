"""
MCA Deal Comparison & Offer Calculator.

Performs the financial analysis that positions brokers as advisors:
  - Factor rate → estimated APR conversion
  - Multi-position stacking analysis
  - Consolidation vs. new position comparison
  - Daily payment capacity estimation
  - Payoff timeline projections

All math is transparent and auditable — no black boxes.
"""
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

BUSINESS_DAYS_PER_MONTH = 22


def factor_rate_to_apr(
    factor_rate: float,
    term_months: int,
    holdback_pct: float = 0.0,
) -> dict:
    """
    Convert an MCA factor rate to an estimated APR.

    MCA factor rates aren't directly comparable to APR because
    they don't account for principal declining over time. This
    estimates the effective APR using the constant-payment method.

    Args:
        factor_rate: e.g., 1.35 means pay back $1.35 for every $1 borrowed
        term_months: expected repayment term
        holdback_pct: daily holdback percentage (if revenue-based), 0 for fixed

    Returns:
        dict with estimated_apr, total_cost_pct, and explanation.
    """
    if factor_rate <= 1.0:
        return {"estimated_apr": 0, "total_cost_pct": 0, "explanation": "Invalid factor rate"}

    total_cost_pct = (factor_rate - 1.0) * 100
    # Simple APR estimate: annualize the cost over the term
    # This is a rough estimate — true APR requires amortization schedule
    annual_factor = 12.0 / term_months
    estimated_apr = total_cost_pct * annual_factor

    return {
        "factor_rate": factor_rate,
        "term_months": term_months,
        "total_cost_pct": round(total_cost_pct, 2),
        "estimated_apr": round(estimated_apr, 2),
        "explanation": (
            f"Factor rate {factor_rate:.2f} over {term_months} months = "
            f"{total_cost_pct:.1f}% total cost. "
            f"Estimated APR: {estimated_apr:.1f}%"
        ),
    }


def analyze_offer(
    advance_amount: float,
    factor_rate: float,
    term_months: int,
    payment_frequency: str = "daily",
) -> dict:
    """
    Break down a single MCA offer into all relevant payment details.

    Args:
        advance_amount: How much the merchant receives
        factor_rate: e.g., 1.35
        term_months: Expected repayment period
        payment_frequency: "daily" or "weekly"

    Returns:
        dict with complete payment breakdown.
    """
    total_payback = advance_amount * factor_rate
    total_cost = total_payback - advance_amount
    cost_pct = (factor_rate - 1.0) * 100

    if payment_frequency == "daily":
        total_payments = term_months * BUSINESS_DAYS_PER_MONTH
        payment_amount = total_payback / total_payments
        payments_per_month = BUSINESS_DAYS_PER_MONTH
    else:  # weekly
        total_payments = term_months * 4
        payment_amount = total_payback / total_payments
        payments_per_month = 4

    monthly_obligation = payment_amount * payments_per_month
    apr_info = factor_rate_to_apr(factor_rate, term_months)

    return {
        "advance_amount": advance_amount,
        "factor_rate": factor_rate,
        "total_payback": round(total_payback, 2),
        "total_cost": round(total_cost, 2),
        "cost_percentage": round(cost_pct, 2),
        "estimated_apr": apr_info["estimated_apr"],
        "payment_frequency": payment_frequency,
        "payment_amount": round(payment_amount, 2),
        "payments_per_month": payments_per_month,
        "monthly_obligation": round(monthly_obligation, 2),
        "total_payments": total_payments,
        "term_months": term_months,
        "payoff_date": (datetime.now() + timedelta(days=term_months * 30)).strftime("%Y-%m-%d"),
    }


def analyze_stacking(
    existing_positions: list[dict],
    new_offer: Optional[dict] = None,
    monthly_revenue: float = 0,
) -> dict:
    """
    Analyze the full picture when a merchant has multiple MCA positions.

    Args:
        existing_positions: List of dicts, each with:
            - name: funder name
            - original_amount: original advance
            - remaining_balance: what's left to pay
            - daily_payment: current daily payment amount
            - payment_frequency: "daily" or "weekly"
        new_offer: Optional new offer dict with advance_amount, factor_rate, term_months
        monthly_revenue: Merchant's monthly revenue (for capacity analysis)

    Returns:
        dict with complete stacking analysis.
    """
    # Analyze existing positions
    total_daily_obligation = 0
    total_remaining = 0
    positions = []

    for pos in existing_positions:
        daily = pos.get("daily_payment", 0)
        freq = pos.get("payment_frequency", "daily")
        if freq == "weekly":
            daily = pos.get("daily_payment", 0) / 5  # Convert weekly to daily

        remaining = pos.get("remaining_balance", 0)
        total_daily_obligation += daily
        total_remaining += remaining

        # Estimate months to payoff
        if daily > 0:
            months_remaining = remaining / (daily * BUSINESS_DAYS_PER_MONTH)
        else:
            months_remaining = 0

        positions.append({
            "name": pos.get("name", "Unknown"),
            "original_amount": pos.get("original_amount", 0),
            "remaining_balance": remaining,
            "daily_payment": round(daily, 2),
            "monthly_obligation": round(daily * BUSINESS_DAYS_PER_MONTH, 2),
            "estimated_months_remaining": round(months_remaining, 1),
            "estimated_payoff_date": (
                datetime.now() + timedelta(days=months_remaining * 30)
            ).strftime("%Y-%m-%d"),
        })

    total_monthly_obligation = total_daily_obligation * BUSINESS_DAYS_PER_MONTH

    analysis = {
        "existing_positions": positions,
        "position_count": len(positions),
        "total_daily_obligation": round(total_daily_obligation, 2),
        "total_monthly_obligation": round(total_monthly_obligation, 2),
        "total_remaining_balance": round(total_remaining, 2),
    }

    # Revenue capacity analysis
    if monthly_revenue > 0:
        obligation_pct = (total_monthly_obligation / monthly_revenue) * 100
        analysis["revenue_analysis"] = {
            "monthly_revenue": monthly_revenue,
            "obligation_to_revenue_pct": round(obligation_pct, 1),
            "remaining_capacity_monthly": round(monthly_revenue - total_monthly_obligation, 2),
            "health": (
                "healthy" if obligation_pct < 20
                else "stretched" if obligation_pct < 35
                else "strained" if obligation_pct < 50
                else "critical"
            ),
        }

    # Analyze new offer impact
    if new_offer:
        new = analyze_offer(
            advance_amount=new_offer.get("advance_amount", 0),
            factor_rate=new_offer.get("factor_rate", 1.35),
            term_months=new_offer.get("term_months", 6),
            payment_frequency=new_offer.get("payment_frequency", "daily"),
        )

        combined_daily = total_daily_obligation + new["payment_amount"]
        combined_monthly = combined_daily * BUSINESS_DAYS_PER_MONTH

        analysis["new_offer"] = new
        analysis["combined_impact"] = {
            "combined_daily_obligation": round(combined_daily, 2),
            "combined_monthly_obligation": round(combined_monthly, 2),
            "position_count_after": len(positions) + 1,
        }

        if monthly_revenue > 0:
            new_obligation_pct = (combined_monthly / monthly_revenue) * 100
            analysis["combined_impact"]["obligation_to_revenue_pct"] = round(new_obligation_pct, 1)
            analysis["combined_impact"]["health"] = (
                "healthy" if new_obligation_pct < 20
                else "stretched" if new_obligation_pct < 35
                else "strained" if new_obligation_pct < 50
                else "critical"
            )
            analysis["combined_impact"]["recommendation"] = _stacking_recommendation(
                new_obligation_pct, len(positions) + 1
            )

    return analysis


def compare_consolidation(
    existing_positions: list[dict],
    consolidation_offer: dict,
    monthly_revenue: float = 0,
) -> dict:
    """
    Compare keeping existing positions vs. consolidating into one.

    Args:
        existing_positions: Current MCA positions (same format as analyze_stacking)
        consolidation_offer: The consolidation deal — dict with:
            - advance_amount: total payoff amount + cash out
            - factor_rate, term_months, payment_frequency
        monthly_revenue: For capacity analysis

    Returns:
        dict with side-by-side comparison and recommendation.
    """
    # Current situation
    current = analyze_stacking(existing_positions, monthly_revenue=monthly_revenue)

    # Consolidation scenario
    consol = analyze_offer(
        advance_amount=consolidation_offer.get("advance_amount", 0),
        factor_rate=consolidation_offer.get("factor_rate", 1.35),
        term_months=consolidation_offer.get("term_months", 9),
        payment_frequency=consolidation_offer.get("payment_frequency", "daily"),
    )

    # How much goes to payoff vs. cash out
    total_remaining = current["total_remaining_balance"]
    advance = consolidation_offer.get("advance_amount", 0)
    cash_out = max(0, advance - total_remaining)

    # Monthly savings
    monthly_savings = current["total_monthly_obligation"] - consol["monthly_obligation"]

    comparison = {
        "scenario_current": {
            "description": f"Keep {len(existing_positions)} existing positions",
            "daily_obligation": current["total_daily_obligation"],
            "monthly_obligation": current["total_monthly_obligation"],
            "total_remaining": total_remaining,
        },
        "scenario_consolidation": {
            "description": "Consolidate into single position",
            "advance_amount": advance,
            "payoff_existing": total_remaining,
            "cash_out_to_merchant": round(cash_out, 2),
            "daily_obligation": consol["payment_amount"],
            "monthly_obligation": consol["monthly_obligation"],
            "total_payback": consol["total_payback"],
            "factor_rate": consol["factor_rate"],
            "estimated_apr": consol["estimated_apr"],
            "term_months": consol["term_months"],
        },
        "comparison": {
            "monthly_savings": round(monthly_savings, 2),
            "daily_savings": round(monthly_savings / BUSINESS_DAYS_PER_MONTH, 2),
            "saves_money_monthly": monthly_savings > 0,
            "reduces_positions": len(existing_positions) > 1,
            "provides_cash_out": cash_out > 0,
        },
    }

    # Recommendation
    if monthly_savings > 0 and cash_out > 0:
        comparison["recommendation"] = (
            f"Consolidation saves ${monthly_savings:,.0f}/mo AND puts "
            f"${cash_out:,.0f} cash in the merchant's pocket. Strong yes."
        )
    elif monthly_savings > 0:
        comparison["recommendation"] = (
            f"Consolidation saves ${monthly_savings:,.0f}/mo with a simpler "
            f"single payment. Recommended."
        )
    elif cash_out > advance * 0.2:
        comparison["recommendation"] = (
            f"Monthly payment increases by ${abs(monthly_savings):,.0f}, but "
            f"merchant gets ${cash_out:,.0f} cash out. Present both scenarios."
        )
    else:
        comparison["recommendation"] = (
            f"Consolidation costs more (${abs(monthly_savings):,.0f}/mo increase) "
            f"with minimal cash out. Current positions may be better to keep."
        )

    return comparison


def _stacking_recommendation(obligation_pct: float, position_count: int) -> str:
    """Generate a recommendation for stacking scenarios."""
    if obligation_pct >= 50:
        return (
            "CAUTION: Combined obligations exceed 50% of revenue. "
            "High default risk. Consider consolidation instead."
        )
    elif obligation_pct >= 35:
        return (
            "Elevated risk. Merchant can handle it short-term but "
            "cash flow is tight. Monitor closely."
        )
    elif obligation_pct >= 20:
        return "Manageable. Merchant has reasonable capacity for this position."
    else:
        return "Comfortable. Low obligation-to-revenue ratio."


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    print("=== MCA Deal Calculator ===\n")

    # Test single offer
    print("--- Single Offer Analysis ---")
    offer = analyze_offer(
        advance_amount=75000,
        factor_rate=1.32,
        term_months=8,
    )
    print(f"Advance: ${offer['advance_amount']:,.0f}")
    print(f"Total payback: ${offer['total_payback']:,.0f}")
    print(f"Cost: ${offer['total_cost']:,.0f} ({offer['cost_percentage']:.1f}%)")
    print(f"Estimated APR: {offer['estimated_apr']:.1f}%")
    print(f"Daily payment: ${offer['payment_amount']:,.2f}")
    print(f"Monthly obligation: ${offer['monthly_obligation']:,.2f}")

    # Test stacking analysis
    print("\n--- Stacking Analysis ---")
    stacking = analyze_stacking(
        existing_positions=[
            {"name": "Funder A", "original_amount": 50000, "remaining_balance": 22000, "daily_payment": 380},
            {"name": "Funder B", "original_amount": 30000, "remaining_balance": 18000, "daily_payment": 250},
        ],
        new_offer={"advance_amount": 40000, "factor_rate": 1.35, "term_months": 6},
        monthly_revenue=85000,
    )
    print(f"Current daily obligation: ${stacking['total_daily_obligation']:,.2f}")
    print(f"Revenue health: {stacking['revenue_analysis']['health']}")
    if "combined_impact" in stacking:
        print(f"After new position: ${stacking['combined_impact']['combined_daily_obligation']:,.2f}/day")
        print(f"Recommendation: {stacking['combined_impact']['recommendation']}")

    # Test consolidation comparison
    print("\n--- Consolidation Comparison ---")
    consol = compare_consolidation(
        existing_positions=[
            {"name": "Funder A", "original_amount": 50000, "remaining_balance": 22000, "daily_payment": 380},
            {"name": "Funder B", "original_amount": 30000, "remaining_balance": 18000, "daily_payment": 250},
        ],
        consolidation_offer={"advance_amount": 60000, "factor_rate": 1.28, "term_months": 9},
        monthly_revenue=85000,
    )
    print(f"Monthly savings: ${consol['comparison']['monthly_savings']:,.0f}")
    print(f"Cash out: ${consol['scenario_consolidation']['cash_out_to_merchant']:,.0f}")
    print(f"Recommendation: {consol['recommendation']}")
