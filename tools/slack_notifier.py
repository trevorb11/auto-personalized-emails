"""
Slack Notification Module.

Sends morning summaries and alerts to Slack via webhook.
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SLACK_WEBHOOK_URL

logger = logging.getLogger(__name__)

SLACK_MAX_LENGTH = 3900  # Slack blocks have a 3000 char limit; text has 40K


async def send_slack_message(text: str) -> bool:
    """Send a plain text message to Slack via webhook."""
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL not set, skipping notification")
        return False

    if len(text) > SLACK_MAX_LENGTH:
        text = text[:SLACK_MAX_LENGTH] + "\n\n... (truncated, see full log)"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                SLACK_WEBHOOK_URL,
                json={"text": text},
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Slack notification failed: {e}")
            return False


async def send_morning_summary(stats: dict, top_leads: list[dict]) -> bool:
    """
    Send the formatted morning summary to the sales team.

    Args:
        stats: dict with total_leads, tier_a, tier_b, tier_c, tier_d,
               avg_score, contacts_created, etc.
        top_leads: list of top A-tier lead dicts with business_name,
                   lead_score, industry, business_focus.
    """
    date_str = datetime.now().strftime("%A, %B %d, %Y")

    # Build the summary
    lines = [
        f"*MCA Prospecting Agent - Morning Report*",
        f"_{date_str}_\n",
        f"*Leads Processed:* {stats.get('total_leads', 0)}",
    ]

    # Source breakdown
    ucc = stats.get("ucc_leads", 0)
    inbound = stats.get("inbound_leads", 0)
    if ucc or inbound:
        lines.append(f"  UCC Filings: {ucc} | Inbound Discovery: {inbound}")

    lines.extend([
        "",
        "*Tier Breakdown:*",
        f"  A (70+): {stats.get('tier_a', 0)} leads",
        f"  B (50-69): {stats.get('tier_b', 0)} leads",
        f"  C (30-49): {stats.get('tier_c', 0)} leads",
        f"  D (<30): {stats.get('tier_d', 0)} leads",
        f"  Avg Score: {stats.get('avg_score', 0):.0f}",
        "",
    ])

    # GHL sync info
    ghl_opps = stats.get("ghl_open_opps", 0)
    ghl_contacts = stats.get("ghl_recent_contacts", 0)
    if ghl_opps or ghl_contacts:
        lines.append("*GHL CRM Status:*")
        if ghl_contacts:
            lines.append(f"  Recent contacts: {ghl_contacts}")
        if ghl_opps:
            lines.append(f"  Open opportunities: {ghl_opps}")
        lines.append("")

    # GHL export info
    created = stats.get("contacts_created", 0)
    if created > 0:
        lines.append(f"*GHL Contacts Created:* {created}")
    elif stats.get("dry_run"):
        lines.append("*Mode:* Dry run (no GHL writes)")

    # Top leads
    if top_leads:
        lines.append("\n*Top Prospects:*")
        for i, lead in enumerate(top_leads[:5], 1):
            name = lead.get("business_name", "Unknown")
            score = lead.get("lead_score", 0)
            industry = lead.get("industry", "")
            focus = lead.get("business_focus", "")
            source = lead.get("source", "")
            source_tag = f" [{source}]" if source else ""
            lines.append(
                f"  {i}. {name} (Score: {score}) "
                f"- {industry} - {focus}{source_tag}"
            )

    # Errors
    errors = stats.get("errors")
    if errors:
        lines.append(f"\n*Errors:* {errors}")

    message = "\n".join(lines)
    return await send_slack_message(message)


async def send_alert(title: str, message: str) -> bool:
    """Send an alert/warning message to Slack."""
    text = f"*[ALERT] {title}*\n{message}"
    return await send_slack_message(text)


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    import anyio

    async def _test():
        result = await send_morning_summary(
            stats={
                "total_leads": 47,
                "tier_a": 5,
                "tier_b": 12,
                "tier_c": 18,
                "tier_d": 12,
                "avg_score": 42.3,
                "contacts_created": 0,
                "dry_run": True,
            },
            top_leads=[
                {"business_name": "ABC Trucking", "lead_score": 85, "industry": "trucking", "business_focus": "MCA Renewal"},
                {"business_name": "XYZ Construction", "lead_score": 78, "industry": "construction", "business_focus": "Consolidation"},
            ],
        )
        print(f"Sent: {result}")

    anyio.run(_test)
