#!/usr/bin/env python3
"""
MCA Prospecting Agent - Daily Orchestrator.

This is the main entry point. It runs the full daily prospecting workflow:
  1. Pull new UCC filings from the database
  2. Enrich leads via Google Maps
  3. Score each lead
  4. Generate personalized outreach for top prospects
  5. Export qualified leads to GoHighLevel
  6. Send a morning summary to Slack

Triggered daily via cron job (e.g., 6:00 AM PT).
Can also be run manually: python main_agent.py [--dry-run] [--state FL]
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    TARGET_STATES, DRY_RUN, DAILY_GHL_CREATES,
    DAILY_GOOGLE_LOOKUPS, LOGS_DIR, DB_PATH,
)
from data.database import (
    init_db, get_connection, insert_lead,
    get_leads_by_tier, mark_lead_exported,
    start_daily_run, complete_daily_run, get_daily_stats,
)
from tools.ucc_processor import process_ucc_filings
from tools.lead_scorer import score_mca_lead
from tools.google_maps_enricher import enrich_business_google
from tools.email_generator import generate_outreach_email
from tools.ghl_writer import create_ghl_contact
from tools.slack_notifier import send_morning_summary, send_alert

# ── Logging setup ──────────────────────────────────────────────
log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(str(log_file)),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("mca-agent")


async def process_state_filings(
    state: str,
    batch_date: str,
    google_lookups_remaining: int,
) -> tuple[list[dict], int]:
    """
    Process UCC filings for a single state:
      - Pull unprocessed filings
      - Enrich via Google Maps (if API key configured)
      - Score each lead
      - Generate outreach for A/B-tier leads

    Returns:
        Tuple of (list of scored lead dicts, google lookups used).
    """
    logger.info(f"Processing state: {state}")
    leads = []
    lookups_used = 0

    # Pull filings from the database
    result = process_ucc_filings(state=state, days_back=7)
    filings = result.get("filings", [])
    logger.info(
        f"  {state}: {result['total_filings']} filings, "
        f"{result['mca_filings']} MCA, "
        f"{result['in_renewal_window']} in renewal window"
    )

    if not filings:
        return leads, lookups_used

    for filing in filings:
        lead_data = {
            "filing_id": filing.get("filing_id"),
            "business_name": filing.get("business_name"),
            "address": filing.get("address"),
            "city": filing.get("city"),
            "state": filing.get("state"),
            "zip_code": filing.get("zip"),
            "has_existing_mca": filing.get("is_mca_lender", False),
            "ucc_filing_age_months": filing.get("filing_age_months"),
            "batch_date": batch_date,
        }

        # ── Enrich via Google Maps (rate-limited) ──────────────
        if lookups_used < google_lookups_remaining:
            try:
                enrichment = await enrich_business_google(
                    business_name=filing.get("business_name", ""),
                    city=filing.get("city", ""),
                    state=filing.get("state", ""),
                )
                if enrichment.get("found"):
                    lead_data.update({
                        "phone": enrichment.get("phone"),
                        "website": enrichment.get("website"),
                        "google_rating": enrichment.get("rating"),
                        "google_review_count": enrichment.get("review_count", 0),
                        "monthly_revenue": enrichment.get("estimated_monthly_revenue", ""),
                        "industry": _infer_industry(enrichment.get("categories", [])),
                    })
                    lookups_used += 1
            except Exception as e:
                logger.warning(f"  Enrichment failed for {filing.get('business_name')}: {e}")

        # ── Score the lead ─────────────────────────────────────
        score_result = score_mca_lead(lead_data)
        lead_data.update({
            "lead_score": score_result["score"],
            "lead_tier": score_result["tier"],
            "score_reasons": json.dumps(score_result["reasons"]),
            "business_focus": score_result["business_focus"],
            "estimated_funding_amount": score_result["estimated_funding_amount"],
        })

        # ── Generate outreach for A/B-tier leads ───────────────
        if score_result["tier"] in ("A", "B"):
            lead_data["secured_party"] = filing.get("secured_party", "")
            email = generate_outreach_email(lead_data)
            lead_data["personalized_message"] = (
                f"Subject: {email['subject']}\n\n{email['body']}"
                f"\n\n---\nSMS: {email['sms_variant']}"
            )

        # ── Save to database ──────────────────────────────────
        conn = get_connection()
        lead_id = insert_lead(conn, lead_data)
        conn.commit()
        conn.close()

        if lead_id:
            lead_data["id"] = lead_id
            leads.append(lead_data)

    logger.info(
        f"  {state}: Processed {len(leads)} leads, "
        f"used {lookups_used} Google lookups"
    )
    return leads, lookups_used


async def export_to_ghl(leads: list[dict], max_creates: int) -> int:
    """Export qualified leads to GoHighLevel. Returns count of contacts created."""
    created = 0

    # Only export A-tier leads (expand to B-tier once you trust the system)
    a_tier = [l for l in leads if l.get("lead_tier") == "A"]

    for lead in a_tier[:max_creates]:
        try:
            result = await create_ghl_contact(lead)

            if result.get("success"):
                contact_id = result.get("contact_id", "")
                if not result.get("dry_run"):
                    conn = get_connection()
                    mark_lead_exported(conn, lead["id"], contact_id)
                    conn.commit()
                    conn.close()
                created += 1

            elif result.get("duplicate"):
                logger.info(f"  Skipping duplicate: {lead.get('business_name')}")

        except Exception as e:
            logger.error(f"  GHL export failed for {lead.get('business_name')}: {e}")

    return created


def _infer_industry(google_types: list[str]) -> str:
    """Map Google Places types to MCA-relevant industry names."""
    type_map = {
        "restaurant": "restaurant",
        "food": "restaurant",
        "meal_delivery": "restaurant",
        "car_repair": "auto repair",
        "car_dealer": "auto dealer",
        "general_contractor": "construction",
        "plumber": "plumbing",
        "electrician": "electrical",
        "roofing_contractor": "roofing",
        "moving_company": "transportation",
        "trucking_company": "trucking",
        "dentist": "dental",
        "doctor": "healthcare",
        "health": "healthcare",
        "pharmacy": "healthcare",
        "store": "retail",
        "shopping_mall": "retail",
        "beauty_salon": "beauty",
        "hair_care": "beauty",
        "gym": "fitness",
        "lodging": "hospitality",
        "real_estate_agency": "real estate",
        "lawyer": "legal",
        "accounting": "accounting",
        "gas_station": "gas station",
    }

    for gtype in google_types:
        if gtype in type_map:
            return type_map[gtype]

    return ""


async def run_daily_prospecting(
    states: list[str] | None = None,
    dry_run: bool | None = None,
) -> None:
    """Main daily prospecting workflow."""
    if states is None:
        states = TARGET_STATES
    if dry_run is not None:
        import config
        config.DRY_RUN = dry_run

    batch_date = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"=== MCA Prospecting Agent - {batch_date} ===")
    logger.info(f"States: {states}")
    logger.info(f"Dry run: {DRY_RUN}")

    # Initialize database
    init_db()

    # Record run start
    conn = get_connection()
    run_id = start_daily_run(conn, batch_date)
    conn.close()

    all_leads = []
    total_google_lookups = 0
    errors = []

    try:
        # ── Process each state ─────────────────────────────────
        for state in states:
            try:
                remaining_lookups = DAILY_GOOGLE_LOOKUPS - total_google_lookups
                leads, lookups = await process_state_filings(
                    state=state,
                    batch_date=batch_date,
                    google_lookups_remaining=remaining_lookups,
                )
                all_leads.extend(leads)
                total_google_lookups += lookups
            except Exception as e:
                error_msg = f"State {state} processing failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        # ── Tier breakdown ─────────────────────────────────────
        tier_a = [l for l in all_leads if l.get("lead_tier") == "A"]
        tier_b = [l for l in all_leads if l.get("lead_tier") == "B"]
        tier_c = [l for l in all_leads if l.get("lead_tier") == "C"]
        tier_d = [l for l in all_leads if l.get("lead_tier") == "D"]

        logger.info(f"\nTier breakdown:")
        logger.info(f"  A: {len(tier_a)} | B: {len(tier_b)} | C: {len(tier_c)} | D: {len(tier_d)}")

        # ── Export to GHL ──────────────────────────────────────
        contacts_created = 0
        if tier_a:
            contacts_created = await export_to_ghl(all_leads, DAILY_GHL_CREATES)
            logger.info(f"GHL contacts created: {contacts_created}")

        # ── Send Slack summary ─────────────────────────────────
        stats = {
            "total_leads": len(all_leads),
            "tier_a": len(tier_a),
            "tier_b": len(tier_b),
            "tier_c": len(tier_c),
            "tier_d": len(tier_d),
            "avg_score": (
                sum(l.get("lead_score", 0) for l in all_leads) / len(all_leads)
                if all_leads else 0
            ),
            "contacts_created": contacts_created,
            "dry_run": DRY_RUN,
            "errors": "; ".join(errors) if errors else None,
        }

        # Top leads for the summary
        top_leads = sorted(
            tier_a, key=lambda l: l.get("lead_score", 0), reverse=True
        )[:5]

        await send_morning_summary(stats, top_leads)

        # ── Record run completion ──────────────────────────────
        conn = get_connection()
        complete_daily_run(conn, run_id, {
            "total_filings": len(all_leads),
            "total_leads": len(all_leads),
            "tier_a": len(tier_a),
            "tier_b": len(tier_b),
            "tier_c": len(tier_c),
            "tier_d": len(tier_d),
            "contacts_created": contacts_created,
            "emails_drafted": len(tier_a) + len(tier_b),
            "errors": "; ".join(errors) if errors else None,
        })
        conn.close()

        logger.info(f"=== Run complete: {len(all_leads)} leads processed ===")

    except Exception as e:
        logger.error(f"Agent run failed: {e}")
        await send_alert("Agent Run Failed", str(e))
        raise


def main():
    """CLI entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="MCA Prospecting Agent - Daily lead prospecting pipeline"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Run without writing to GHL (overrides DRY_RUN env var)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Force live mode (writes to GHL)",
    )
    parser.add_argument(
        "--states",
        type=str,
        default=None,
        help="Comma-separated state codes (e.g., FL,CA,NY)",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="How many days of filings to look back (default: 7)",
    )

    args = parser.parse_args()

    states = args.states.split(",") if args.states else None
    dry_run = None
    if args.dry_run:
        dry_run = True
    elif args.no_dry_run:
        dry_run = False

    asyncio.run(run_daily_prospecting(states=states, dry_run=dry_run))


if __name__ == "__main__":
    main()
