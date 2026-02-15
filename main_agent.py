#!/usr/bin/env python3
"""
MCA Prospecting Agent - Daily Orchestrator.

This is the main entry point. It runs the full daily prospecting workflow:

  Phase 0: Pull GHL inbound snapshot (contacts, opps, conversations)
  Phase 1: Process UCC filings → enrich → score → outreach
  Phase 2: Inbound discovery (Google CSE → Hunter.io → Clearbit)
  Phase 3: Score and generate outreach for discovered leads
  Phase 4: Export qualified leads to GoHighLevel
  Phase 5: Send morning summary to Slack

Triggered daily via cron job (e.g., 6:00 AM PT).
Can also be run manually: python main_agent.py [--dry-run] [--states FL]
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
    DAILY_GOOGLE_LOOKUPS, DAILY_CSE_SEARCHES,
    DAILY_HUNTER_LOOKUPS, DAILY_CLEARBIT_LOOKUPS,
    LOGS_DIR, GHL_API_KEY,
)
from data.database import (
    init_db, get_connection, insert_lead,
    get_leads_by_tier, mark_lead_exported,
    start_daily_run, complete_daily_run,
    is_domain_known, record_domain,
    save_ghl_snapshot, sync_opportunities,
)
from tools.ucc_processor import process_ucc_filings
from tools.lead_scorer import score_mca_lead
from tools.google_maps_enricher import enrich_business_google
from tools.email_generator import generate_outreach_email
from tools.ghl_client import create_contact, pull_inbound_snapshot
from tools.inbound_leads import discover_and_enrich, get_mca_search_targets
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


# ═══════════════════════════════════════════════════════════════
# PHASE 0: GHL INBOUND SYNC
# ═══════════════════════════════════════════════════════════════

async def sync_ghl_inbound() -> dict:
    """
    Pull a snapshot of current GHL state:
      - Recent contacts, open opportunities, conversations, appointments
      - Syncs opportunities into local tracking table
      - Saves snapshot for historical analysis
    """
    if not GHL_API_KEY:
        logger.info("GHL not configured, skipping inbound sync")
        return {}

    logger.info("=== Phase 0: GHL Inbound Sync ===")
    snapshot = await pull_inbound_snapshot()

    # Persist the snapshot and sync opportunities locally
    conn = get_connection()
    snapshot_id = save_ghl_snapshot(conn, snapshot)

    opps = snapshot.get("open_opportunities", [])
    if opps:
        synced = sync_opportunities(conn, opps)
        logger.info(f"  Synced {synced} opportunities to local DB")

    conn.close()

    # Log a readable summary
    logger.info(
        f"  GHL Snapshot: {len(snapshot.get('new_contacts', []))} recent contacts, "
        f"{len(opps)} open opps, "
        f"{len(snapshot.get('recent_conversations', []))} conversations, "
        f"{len(snapshot.get('upcoming_appointments', []))} appointments"
    )

    snapshot["_snapshot_id"] = snapshot_id
    return snapshot


# ═══════════════════════════════════════════════════════════════
# PHASE 1: UCC FILING PROCESSING
# ═══════════════════════════════════════════════════════════════

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
    """
    logger.info(f"Processing UCC filings: {state}")
    leads = []
    lookups_used = 0

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
            "source": "ucc",
            "business_name": filing.get("business_name"),
            "address": filing.get("address"),
            "city": filing.get("city"),
            "state": filing.get("state"),
            "zip_code": filing.get("zip"),
            "has_existing_mca": filing.get("is_mca_lender", False),
            "ucc_filing_age_months": filing.get("filing_age_months"),
            "batch_date": batch_date,
        }

        # Enrich via Google Maps (rate-limited)
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

        # Score
        score_result = score_mca_lead(lead_data)
        lead_data.update({
            "lead_score": score_result["score"],
            "lead_tier": score_result["tier"],
            "score_reasons": json.dumps(score_result["reasons"]),
            "business_focus": score_result["business_focus"],
            "estimated_funding_amount": score_result["estimated_funding_amount"],
        })

        # Generate outreach for A/B-tier
        if score_result["tier"] in ("A", "B"):
            lead_data["secured_party"] = filing.get("secured_party", "")
            email = generate_outreach_email(lead_data)
            lead_data["personalized_message"] = (
                f"Subject: {email['subject']}\n\n{email['body']}"
                f"\n\n---\nSMS: {email['sms_variant']}"
            )

        # Save to DB
        conn = get_connection()
        lead_id = insert_lead(conn, lead_data)
        conn.commit()
        conn.close()

        if lead_id:
            lead_data["id"] = lead_id
            leads.append(lead_data)

    logger.info(f"  {state}: {len(leads)} leads scored, {lookups_used} Google lookups")
    return leads, lookups_used


# ═══════════════════════════════════════════════════════════════
# PHASE 2: INBOUND LEAD DISCOVERY
# ═══════════════════════════════════════════════════════════════

async def run_inbound_discovery(
    states: list[str],
    batch_date: str,
    max_targets: int = 4,
) -> list[dict]:
    """
    Discover new leads via Google CSE + Hunter.io + Clearbit.
    Skips domains we've already seen. Scores and generates outreach.
    """
    logger.info("=== Phase 2: Inbound Lead Discovery ===")
    all_inbound_leads = []

    # Get search targets (industry + city combos)
    targets = get_mca_search_targets(states, cities_per_state=2)

    # Limit to control API spend
    targets = targets[:max_targets]
    logger.info(f"  Running {len(targets)} discovery searches")

    conn = get_connection()

    for target in targets:
        try:
            raw_leads = await discover_and_enrich(
                industry=target["industry"],
                city=target["city"],
                state=target["state"],
                max_leads=5,
                enrich=True,
            )

            for lead in raw_leads:
                domain = lead.get("domain", "")

                # Skip already-known domains
                if domain and is_domain_known(conn, domain):
                    logger.info(f"  Skipping known domain: {domain}")
                    continue

                # Add source and batch info
                lead["source"] = "inbound-discovery"
                lead["batch_date"] = batch_date

                # Score
                score_result = score_mca_lead(lead)
                lead.update({
                    "lead_score": score_result["score"],
                    "lead_tier": score_result["tier"],
                    "score_reasons": json.dumps(score_result["reasons"]),
                    "business_focus": score_result["business_focus"],
                    "estimated_funding_amount": score_result["estimated_funding_amount"],
                })

                # Generate outreach for A/B-tier
                if score_result["tier"] in ("A", "B"):
                    email = generate_outreach_email(lead)
                    lead["personalized_message"] = (
                        f"Subject: {email['subject']}\n\n{email['body']}"
                        f"\n\n---\nSMS: {email['sms_variant']}"
                    )

                # Save lead to DB
                lead_id = insert_lead(conn, lead)
                conn.commit()

                if lead_id:
                    lead["id"] = lead_id
                    all_inbound_leads.append(lead)

                # Record the domain so we don't re-discover it
                if domain:
                    record_domain(
                        conn, domain,
                        business_name=lead.get("business_name", ""),
                        industry=lead.get("industry", ""),
                        city=lead.get("city", ""),
                        state=lead.get("state", ""),
                        lead_id=lead_id,
                    )
                    conn.commit()

        except Exception as e:
            logger.error(f"  Discovery failed for {target}: {e}")

    conn.close()
    logger.info(f"  Inbound discovery: {len(all_inbound_leads)} new leads found")
    return all_inbound_leads


# ═══════════════════════════════════════════════════════════════
# PHASE 4: GHL EXPORT
# ═══════════════════════════════════════════════════════════════

async def export_to_ghl(leads: list[dict], max_creates: int) -> int:
    """Export qualified leads to GoHighLevel."""
    created = 0
    a_tier = [l for l in leads if l.get("lead_tier") == "A"]

    for lead in a_tier[:max_creates]:
        try:
            result = await create_contact(lead)

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


# ═══════════════════════════════════════════════════════════════
# INDUSTRY INFERENCE
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

async def run_daily_prospecting(
    states: list[str] | None = None,
    dry_run: bool | None = None,
    skip_inbound: bool = False,
    skip_ghl_sync: bool = False,
) -> None:
    """Main daily prospecting workflow."""
    if states is None:
        states = TARGET_STATES
    if dry_run is not None:
        import config
        config.DRY_RUN = dry_run

    batch_date = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"{'='*60}")
    logger.info(f"MCA Prospecting Agent - {batch_date}")
    logger.info(f"States: {states} | Dry run: {DRY_RUN}")
    logger.info(f"{'='*60}")

    init_db()

    conn = get_connection()
    run_id = start_daily_run(conn, batch_date)
    conn.close()

    all_leads = []
    inbound_leads = []
    total_google_lookups = 0
    ghl_snapshot = {}
    errors = []

    try:
        # ── Phase 0: GHL Inbound Sync ─────────────────────────
        if not skip_ghl_sync:
            try:
                ghl_snapshot = await sync_ghl_inbound()
            except Exception as e:
                error_msg = f"GHL sync failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        # ── Phase 1: UCC Filing Processing ────────────────────
        logger.info("\n=== Phase 1: UCC Filing Processing ===")
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
                error_msg = f"State {state} UCC processing failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        # ── Phase 2: Inbound Discovery ────────────────────────
        if not skip_inbound:
            try:
                inbound_leads = await run_inbound_discovery(
                    states=states,
                    batch_date=batch_date,
                    max_targets=4,
                )
                all_leads.extend(inbound_leads)
            except Exception as e:
                error_msg = f"Inbound discovery failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        # ── Phase 3: Tier Breakdown ───────────────────────────
        tier_a = [l for l in all_leads if l.get("lead_tier") == "A"]
        tier_b = [l for l in all_leads if l.get("lead_tier") == "B"]
        tier_c = [l for l in all_leads if l.get("lead_tier") == "C"]
        tier_d = [l for l in all_leads if l.get("lead_tier") == "D"]

        ucc_count = sum(1 for l in all_leads if l.get("source") == "ucc")
        inbound_count = sum(1 for l in all_leads if l.get("source") == "inbound-discovery")

        logger.info(f"\nTier breakdown (total: {len(all_leads)}):")
        logger.info(f"  A: {len(tier_a)} | B: {len(tier_b)} | C: {len(tier_c)} | D: {len(tier_d)}")
        logger.info(f"  Sources: {ucc_count} UCC, {inbound_count} inbound discovery")

        # ── Phase 4: Export to GHL ────────────────────────────
        contacts_created = 0
        if tier_a:
            contacts_created = await export_to_ghl(all_leads, DAILY_GHL_CREATES)
            logger.info(f"GHL contacts created: {contacts_created}")

        # ── Phase 5: Slack Summary ────────────────────────────
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
            "ucc_leads": ucc_count,
            "inbound_leads": inbound_count,
            "ghl_open_opps": len(ghl_snapshot.get("open_opportunities", [])),
            "ghl_recent_contacts": len(ghl_snapshot.get("new_contacts", [])),
            "errors": "; ".join(errors) if errors else None,
        }

        top_leads = sorted(
            tier_a, key=lambda l: l.get("lead_score", 0), reverse=True
        )[:5]

        await send_morning_summary(stats, top_leads)

        # ── Record run completion ─────────────────────────────
        conn = get_connection()
        complete_daily_run(conn, run_id, {
            "total_filings": ucc_count,
            "total_leads": len(all_leads),
            "tier_a": len(tier_a),
            "tier_b": len(tier_b),
            "tier_c": len(tier_c),
            "tier_d": len(tier_d),
            "contacts_created": contacts_created,
            "emails_drafted": len(tier_a) + len(tier_b),
            "inbound_discovered": inbound_count,
            "inbound_enriched": sum(1 for l in inbound_leads if l.get("email")),
            "ghl_snapshot_id": ghl_snapshot.get("_snapshot_id"),
            "errors": "; ".join(errors) if errors else None,
        })
        conn.close()

        logger.info(f"\n{'='*60}")
        logger.info(f"Run complete: {len(all_leads)} leads ({ucc_count} UCC + {inbound_count} inbound)")
        logger.info(f"{'='*60}")

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
        "--skip-inbound",
        action="store_true",
        help="Skip inbound discovery (Google CSE + Hunter + Clearbit)",
    )
    parser.add_argument(
        "--skip-ghl-sync",
        action="store_true",
        help="Skip GHL inbound snapshot",
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

    asyncio.run(run_daily_prospecting(
        states=states,
        dry_run=dry_run,
        skip_inbound=args.skip_inbound,
        skip_ghl_sync=args.skip_ghl_sync,
    ))


if __name__ == "__main__":
    main()
