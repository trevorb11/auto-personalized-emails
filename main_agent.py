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

import config
from config import (
    TARGET_STATES, LOGS_DIR, GHL_API_KEY, UCC_FILINGS_PER_STATE,
)
from data.database import (
    init_db, get_connection, insert_lead,
    mark_lead_exported,
    start_daily_run, complete_daily_run,
    is_domain_known, record_domain,
    save_ghl_snapshot, sync_opportunities,
)
from tools import api_budget
from tools.ucc_processor import process_ucc_filings
from tools.lead_scorer import score_mca_lead
from tools.google_maps_enricher import enrich_business_google
from tools.email_generator import generate_outreach_email
from tools.funder_matrix import get_best_offer
from tools.conversation_analyzer import score_conversation_urgency
from tools.ghl_client import (
    create_contact, add_contact_note, pull_inbound_snapshot,
    get_conversation_messages,
)
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
    snapshot["hot_conversations"] = await find_hot_conversations(
        snapshot.get("recent_conversations", [])
    )
    return snapshot


async def find_hot_conversations(
    conversations: list[dict],
    min_urgency: int = 6,
    max_checked: int = 30,
) -> list[dict]:
    """
    Score recent GHL conversations with the free keyword scorer and return
    the ones where a merchant is showing buying intent, so reps see them
    in the morning report before any new outbound.
    """
    hot = []
    for conv in conversations[:max_checked]:
        # Only conversations where the merchant spoke last need a reply
        if conv.get("lastMessageDirection") == "outbound":
            continue
        messages = await get_conversation_messages(conv.get("id", ""), limit=20)
        if not messages:
            continue
        urgency = score_conversation_urgency(messages)
        if urgency >= min_urgency:
            hot.append({
                "conversation_id": conv.get("id"),
                "contact_id": conv.get("contactId"),
                "contact_name": conv.get("contactName") or conv.get("fullName") or "",
                "urgency": urgency,
                "last_message": (conv.get("lastMessageBody") or "")[:140],
            })
    hot.sort(key=lambda c: c["urgency"], reverse=True)
    if hot:
        logger.info(f"  {len(hot)} hot conversations need a reply")
    return hot


# ═══════════════════════════════════════════════════════════════
# PHASE 1: UCC FILING PROCESSING
# ═══════════════════════════════════════════════════════════════

# Fields from the UCC processor that scoring, outreach and GHL need.
_UCC_INTEL_FIELDS = [
    "secured_party", "filing_date", "funder_display_name", "funder_tier",
    "funder_weakness", "funder_positioning", "funder_sweet_spot", "urgency",
    "estimated_advance", "estimated_factor_rate", "estimated_daily_payment",
    "estimated_monthly_obligation", "estimated_remaining_balance",
    "estimated_months_remaining", "estimated_payoff_date",
    "position_count", "other_funders", "is_stacked",
]


def finalize_lead(lead: dict) -> dict:
    """
    Score a lead, attach the best-fit funder, and draft outreach for
    A/B tiers. Mutates and returns the lead.
    """
    score_result = score_mca_lead(lead)
    lead.update({
        "lead_score": score_result["score"],
        "lead_tier": score_result["tier"],
        "score_reasons": json.dumps(score_result["reasons"]),
        "business_focus": score_result["business_focus"],
        "estimated_funding_amount": score_result["estimated_funding_amount"],
    })

    if score_result["tier"] not in ("A", "B"):
        return lead

    best = get_best_offer({
        **lead,
        "existing_positions": lead.get("position_count") or (1 if lead.get("has_existing_mca") else 0),
    })
    if best:
        lead["best_funder_match"] = (
            f"{best['funder']} (~${best['estimated_amount']:,.0f} @ "
            f"{best['estimated_factor_rate']:.2f})"
        )

    # Claude rewrites are reserved for A-tier to keep spend focused
    email = generate_outreach_email(lead, use_llm=score_result["tier"] == "A")
    lead.update({
        "email_subject": email["subject"],
        "email_body": email["body"],
        "sms_body": email["sms_variant"],
        "sales_notes": "\n".join(email["personalization_notes"]),
        "personalized_message": (
            f"Subject: {email['subject']}\n\n{email['body']}"
            f"\n\n---\nSMS: {email['sms_variant']}"
        ),
    })
    return lead


async def process_state_filings(state: str, batch_date: str) -> list[dict]:
    """
    Process UCC filings for a single state:
      - Pull filings that have aged into the renewal window
      - Enrich via Google Maps (daily cap enforced in the enricher)
      - Score, match funders, and draft outreach for A/B-tier leads
    """
    logger.info(f"Processing UCC filings: {state}")
    leads = []
    lookups_used = 0

    result = process_ucc_filings(state=state, limit=UCC_FILINGS_PER_STATE)
    filings = result.get("filings", [])
    logger.info(
        f"  {state}: {result['total_filings']} filings, "
        f"{result['mca_filings']} MCA, "
        f"{result['in_renewal_window']} in renewal window, "
        f"{result['stacked_merchants']} stacked"
    )

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
            **{k: filing.get(k) for k in _UCC_INTEL_FIELDS if k in filing},
        }

        try:
            enrichment = await enrich_business_google(
                business_name=filing.get("business_name") or "",
                city=filing.get("city") or "",
                state=filing.get("state") or "",
            )
            if enrichment.get("found"):
                lookups_used += 1
                lead_data.update({
                    "phone": enrichment.get("phone"),
                    "website": enrichment.get("website"),
                    "google_rating": enrichment.get("rating"),
                    "google_review_count": enrichment.get("review_count", 0),
                    "monthly_revenue": enrichment.get("estimated_monthly_revenue", ""),
                    "revenue_is_estimate": True,
                    "industry": _infer_industry(enrichment.get("categories", [])),
                })
            elif enrichment.get("reason") == "closed_permanently":
                logger.info(f"  Skipping closed business: {filing.get('business_name')}")
                continue
        except Exception as e:
            logger.warning(f"  Enrichment failed for {filing.get('business_name')}: {e}")

        finalize_lead(lead_data)

        conn = get_connection()
        lead_id = insert_lead(conn, lead_data)
        conn.commit()
        conn.close()

        if lead_id:
            lead_data["id"] = lead_id
            leads.append(lead_data)

    logger.info(f"  {state}: {len(leads)} leads scored, {lookups_used} Google matches")
    return leads


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
    Known domains are skipped before any paid enrichment. Search targets
    rotate daily so each run covers new industry/city combinations.
    """
    logger.info("=== Phase 2: Inbound Lead Discovery ===")
    all_inbound_leads = []

    targets = get_mca_search_targets(states, cities_per_state=2, per_day=max_targets)
    targets = targets[:max_targets]
    logger.info(
        f"  Running {len(targets)} discovery searches: "
        + ", ".join(f"{t['industry']}/{t['city']}" for t in targets)
    )

    conn = get_connection()

    for target in targets:
        try:
            raw_leads = await discover_and_enrich(
                industry=target["industry"],
                city=target["city"],
                state=target["state"],
                max_leads=5,
                enrich=True,
                skip_domain=lambda d: is_domain_known(conn, d),
            )

            for lead in raw_leads:
                domain = lead.get("domain", "")
                lead["source"] = "inbound-discovery"
                lead["batch_date"] = batch_date

                finalize_lead(lead)

                lead_id = insert_lead(conn, lead)
                conn.commit()

                if lead_id:
                    lead["id"] = lead_id
                    all_inbound_leads.append(lead)

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

async def export_to_ghl(leads: list[dict]) -> int:
    """Export A-tier leads to GoHighLevel, best first, within the daily cap."""
    created = 0
    a_tier = sorted(
        (l for l in leads if l.get("lead_tier") == "A"),
        key=lambda l: l.get("lead_score", 0),
        reverse=True,
    )

    for lead in a_tier:
        if not config.DRY_RUN and not api_budget.try_consume("ghl_create"):
            break
        try:
            result = await create_contact(lead)

            if result.get("success"):
                contact_id = result.get("contact_id", "")
                if not result.get("dry_run"):
                    conn = get_connection()
                    mark_lead_exported(conn, lead["id"], contact_id)
                    conn.commit()
                    conn.close()
                    if lead.get("sales_notes"):
                        await add_contact_note(
                            contact_id, f"AI prospecting notes:\n{lead['sales_notes']}"
                        )
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
    max_targets: int = 4,
) -> None:
    """Main daily prospecting workflow."""
    if states is None:
        states = TARGET_STATES
    if dry_run is not None:
        config.DRY_RUN = dry_run

    batch_date = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"{'='*60}")
    logger.info(f"MCA Prospecting Agent - {batch_date}")
    logger.info(f"States: {states} | Dry run: {config.DRY_RUN}")
    logger.info(f"{'='*60}")

    init_db()

    conn = get_connection()
    run_id = start_daily_run(conn, batch_date)
    conn.close()

    all_leads = []
    inbound_leads = []
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
                leads = await process_state_filings(state=state, batch_date=batch_date)
                all_leads.extend(leads)
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
                    max_targets=max_targets,
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
            contacts_created = await export_to_ghl(all_leads)
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
            "dry_run": config.DRY_RUN,
            "ucc_leads": ucc_count,
            "inbound_leads": inbound_count,
            "ghl_open_opps": len(ghl_snapshot.get("open_opportunities", [])),
            "ghl_recent_contacts": len(ghl_snapshot.get("new_contacts", [])),
            "errors": "; ".join(errors) if errors else None,
        }

        top_leads = sorted(
            tier_a, key=lambda l: l.get("lead_score", 0), reverse=True
        )[:5]

        await send_morning_summary(
            stats, top_leads,
            hot_conversations=ghl_snapshot.get("hot_conversations", []),
        )

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
        "--max-targets",
        type=int,
        default=4,
        help="Inbound discovery searches per run (default: 4)",
    )

    args = parser.parse_args()

    states = [st.strip().upper() for st in args.states.split(",")] if args.states else None
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
        max_targets=args.max_targets,
    ))


if __name__ == "__main__":
    main()
