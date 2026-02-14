"""
GoHighLevel CRM Writer Tool.

Creates and updates contacts in GHL with MCA-relevant fields.
Supports dry-run mode for safe testing.
Deduplicates by phone number and business name before creating.
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import GHL_API_KEY, GHL_LOCATION_ID, GHL_BASE_URL, DRY_RUN

logger = logging.getLogger(__name__)

GHL_HEADERS = {
    "Authorization": f"Bearer {GHL_API_KEY}",
    "Content-Type": "application/json",
    "Version": "2021-07-28",
}


async def search_ghl_contact(
    phone: str = "",
    business_name: str = "",
) -> dict | None:
    """
    Search GHL for an existing contact by phone or company name.
    Returns the first matching contact or None.
    """
    if not GHL_API_KEY:
        logger.warning("GHL_API_KEY not set")
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Search by phone first (most reliable dedup key)
        if phone:
            try:
                resp = await client.get(
                    f"{GHL_BASE_URL}/contacts/search",
                    headers=GHL_HEADERS,
                    params={
                        "locationId": GHL_LOCATION_ID,
                        "query": phone,
                    },
                )
                if resp.status_code == 200:
                    contacts = resp.json().get("contacts", [])
                    if contacts:
                        return contacts[0]
            except httpx.HTTPError as e:
                logger.error(f"GHL search by phone failed: {e}")

        # Fallback: search by company name
        if business_name:
            try:
                resp = await client.get(
                    f"{GHL_BASE_URL}/contacts/search",
                    headers=GHL_HEADERS,
                    params={
                        "locationId": GHL_LOCATION_ID,
                        "query": business_name,
                    },
                )
                if resp.status_code == 200:
                    contacts = resp.json().get("contacts", [])
                    if contacts:
                        return contacts[0]
            except httpx.HTTPError as e:
                logger.error(f"GHL search by name failed: {e}")

    return None


async def create_ghl_contact(lead: dict) -> dict:
    """
    Create a new contact in GoHighLevel with all MCA-relevant fields.

    In DRY_RUN mode, logs what would be created without calling the API.

    Args:
        lead: dict with keys like first_name, last_name, business_name,
              phone, email, website, industry, lead_score, lead_tier, etc.

    Returns:
        dict with success (bool), contact_id (if created), or error info.
    """
    batch_date = lead.get("batch_date", datetime.now().strftime("%Y-%m-%d"))

    payload = {
        "locationId": GHL_LOCATION_ID,
        "firstName": lead.get("first_name", ""),
        "lastName": lead.get("last_name", ""),
        "companyName": lead.get("business_name", ""),
        "phone": lead.get("phone", ""),
        "email": lead.get("email", ""),
        "website": lead.get("website", ""),
        "address1": lead.get("address", ""),
        "city": lead.get("city", ""),
        "state": lead.get("state", ""),
        "source": "AI Prospecting Agent",
        "tags": [
            f"tier-{lead.get('lead_tier', 'D')}",
            "agent-sourced",
            f"batch-{batch_date}",
        ],
        "customFields": [
            {"key": "industry", "value": lead.get("industry", "")},
            {"key": "monthly_revenue", "value": str(lead.get("monthly_revenue", ""))},
            {"key": "years_in_business", "value": str(lead.get("years_in_business", ""))},
            {"key": "qualified_amount", "value": lead.get("estimated_funding_amount", "TBD")},
            {"key": "ucc_filings", "value": lead.get("ucc_filings", "")},
            {"key": "most_recent_filing_date", "value": lead.get("most_recent_filing_date", "")},
            {
                "key": "personalized_message__multi_line",
                "value": lead.get("personalized_message", ""),
            },
            {"key": "business_focus", "value": lead.get("business_focus", "New Funding")},
            {"key": "lead_batch", "value": f"agent_{batch_date}"},
            {"key": "pipeline_selection", "value": "Main Pipeline"},
            {"key": "contact.source", "value": "AI Prospecting Agent"},
        ],
    }

    # ── Dry run mode ───────────────────────────────────────────
    if DRY_RUN:
        logger.info(f"DRY RUN: Would create GHL contact: {lead.get('business_name')}")
        return {
            "success": True,
            "dry_run": True,
            "contact_id": f"dry-run-{batch_date}",
            "business": lead.get("business_name"),
            "tier": lead.get("lead_tier"),
            "payload_preview": {
                "name": f"{payload['firstName']} {payload['lastName']}".strip(),
                "company": payload["companyName"],
                "tags": payload["tags"],
            },
        }

    # ── Deduplicate ────────────────────────────────────────────
    existing = await search_ghl_contact(
        phone=lead.get("phone", ""),
        business_name=lead.get("business_name", ""),
    )
    if existing:
        logger.info(
            f"Contact already exists in GHL: {existing.get('id')} "
            f"({lead.get('business_name')})"
        )
        return {
            "success": False,
            "duplicate": True,
            "existing_contact_id": existing.get("id"),
            "business": lead.get("business_name"),
        }

    # ── Create contact ─────────────────────────────────────────
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                f"{GHL_BASE_URL}/contacts/",
                headers=GHL_HEADERS,
                json=payload,
            )

            if resp.status_code in (200, 201):
                contact = resp.json().get("contact", {})
                logger.info(
                    f"Created GHL contact: {contact.get('id')} "
                    f"({lead.get('business_name')})"
                )
                return {
                    "success": True,
                    "contact_id": contact.get("id"),
                    "name": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
                    "business": lead.get("business_name"),
                    "tier": lead.get("lead_tier"),
                }
            else:
                logger.error(f"GHL create failed ({resp.status_code}): {resp.text}")
                return {
                    "success": False,
                    "error": resp.text,
                    "status": resp.status_code,
                }

        except httpx.HTTPError as e:
            logger.error(f"GHL create request failed: {e}")
            return {"success": False, "error": str(e)}


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    import anyio

    async def _test():
        result = await create_ghl_contact({
            "business_name": "Test Trucking LLC",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "555-555-0100",
            "industry": "trucking",
            "lead_tier": "A",
            "lead_score": 85,
        })
        print(json.dumps(result, indent=2))

    anyio.run(_test)
