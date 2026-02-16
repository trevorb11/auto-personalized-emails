"""
GoHighLevel CRM Client.

Full read/write client for GHL API v2. Covers:
  - Contacts: search, get, list, create, update, lookup by phone/email
  - Opportunities: list by pipeline/stage, get details, create
  - Pipelines: list pipelines and stages
  - Conversations: list recent, get messages
  - Calendars: list appointments

Supports dry-run mode for safe testing on write operations.
All read operations work regardless of dry-run mode.
"""
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import GHL_API_KEY, GHL_LOCATION_ID, GHL_BASE_URL, DRY_RUN
from tools.http_retry import fetch

logger = logging.getLogger(__name__)

API_VERSION = "2021-07-28"
_TIMEOUT = 20.0


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json",
        "Version": API_VERSION,
    }


def _check_api_key() -> bool:
    if not GHL_API_KEY:
        logger.warning("GHL_API_KEY not set — skipping GHL operation")
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# CONTACTS
# ═══════════════════════════════════════════════════════════════

async def search_contacts(
    query: str,
    limit: int = 20,
) -> list[dict]:
    """
    Search GHL contacts by name, phone, email, or company.
    Returns a list of matching contact dicts.
    """
    if not _check_api_key():
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/contacts/",
                headers=_headers(),
                params={
                    "locationId": GHL_LOCATION_ID,
                    "query": query,
                    "limit": limit,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("contacts", [])
            logger.error(f"GHL search failed ({resp.status_code}): {resp.text[:200]}")
        except httpx.HTTPError as e:
            logger.error(f"GHL search request failed: {e}")
    return []


async def get_contact(contact_id: str) -> Optional[dict]:
    """Get a single contact by ID with all fields."""
    if not _check_api_key():
        return None

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/contacts/{contact_id}",
                headers=_headers(),
            )
            if resp.status_code == 200:
                return resp.json().get("contact")
            logger.error(f"GHL get contact failed ({resp.status_code})")
        except httpx.HTTPError as e:
            logger.error(f"GHL get contact failed: {e}")
    return None


async def list_contacts(
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "dateAdded",
    sort_order: str = "desc",
) -> dict:
    """
    List contacts with pagination.
    Returns dict with 'contacts' list and 'meta' (total, nextOffset).
    """
    if not _check_api_key():
        return {"contacts": [], "meta": {}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/contacts/",
                headers=_headers(),
                params={
                    "locationId": GHL_LOCATION_ID,
                    "limit": limit,
                    "skip": offset,
                    "sortBy": sort_by,
                    "sortOrder": sort_order,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "contacts": data.get("contacts", []),
                    "meta": data.get("meta", {}),
                }
            logger.error(f"GHL list contacts failed ({resp.status_code})")
        except httpx.HTTPError as e:
            logger.error(f"GHL list contacts failed: {e}")
    return {"contacts": [], "meta": {}}


async def get_contacts_by_tag(tag: str, limit: int = 100) -> list[dict]:
    """Get all contacts with a specific tag."""
    if not _check_api_key():
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/contacts/",
                headers=_headers(),
                params={
                    "locationId": GHL_LOCATION_ID,
                    "query": tag,
                    "limit": limit,
                },
            )
            if resp.status_code == 200:
                contacts = resp.json().get("contacts", [])
                # Filter to only contacts that actually have this tag
                return [
                    c for c in contacts
                    if tag in (c.get("tags") or [])
                ]
        except httpx.HTTPError as e:
            logger.error(f"GHL tag search failed: {e}")
    return []


async def lookup_contact(
    phone: str = "",
    email: str = "",
    business_name: str = "",
) -> Optional[dict]:
    """
    Find an existing contact by phone, email, or business name.
    Tries phone first (most unique), then email, then company name.
    Returns the first match or None.
    """
    if not _check_api_key():
        return None

    for query in [phone, email, business_name]:
        if query:
            results = await search_contacts(query, limit=5)
            if results:
                return results[0]
    return None


async def create_contact(lead: dict) -> dict:
    """
    Create a new contact in GHL with MCA-relevant fields.
    Deduplicates by phone/email/name before creating.
    Respects DRY_RUN mode.
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
        "source": lead.get("source", "AI Prospecting Agent"),
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

    # Deduplicate
    existing = await lookup_contact(
        phone=lead.get("phone", ""),
        email=lead.get("email", ""),
        business_name=lead.get("business_name", ""),
    )
    if existing:
        logger.info(f"Duplicate in GHL: {existing.get('id')} ({lead.get('business_name')})")
        return {
            "success": False,
            "duplicate": True,
            "existing_contact_id": existing.get("id"),
            "business": lead.get("business_name"),
        }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "POST",
                f"{GHL_BASE_URL}/contacts/",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code in (200, 201):
                contact = resp.json().get("contact", {})
                logger.info(f"Created GHL contact: {contact.get('id')}")
                return {
                    "success": True,
                    "contact_id": contact.get("id"),
                    "business": lead.get("business_name"),
                    "tier": lead.get("lead_tier"),
                }
            else:
                logger.error(f"GHL create failed ({resp.status_code}): {resp.text[:300]}")
                return {"success": False, "error": resp.text, "status": resp.status_code}
        except httpx.HTTPError as e:
            logger.error(f"GHL create request failed: {e}")
            return {"success": False, "error": str(e)}


async def update_contact(contact_id: str, fields: dict) -> dict:
    """Update specific fields on an existing GHL contact."""
    if DRY_RUN:
        logger.info(f"DRY RUN: Would update GHL contact {contact_id}: {list(fields.keys())}")
        return {"success": True, "dry_run": True, "contact_id": contact_id}

    if not _check_api_key():
        return {"success": False, "error": "API key not set"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "PUT",
                f"{GHL_BASE_URL}/contacts/{contact_id}",
                headers=_headers(),
                json=fields,
            )
            if resp.status_code == 200:
                return {"success": True, "contact_id": contact_id}
            logger.error(f"GHL update failed ({resp.status_code}): {resp.text[:300]}")
            return {"success": False, "error": resp.text, "status": resp.status_code}
        except httpx.HTTPError as e:
            logger.error(f"GHL update request failed: {e}")
            return {"success": False, "error": str(e)}


async def add_contact_tags(contact_id: str, tags: list[str]) -> dict:
    """Add tags to an existing contact."""
    return await update_contact(contact_id, {"tags": tags})


async def add_contact_note(contact_id: str, body: str) -> dict:
    """Add a note to a contact."""
    if DRY_RUN:
        logger.info(f"DRY RUN: Would add note to {contact_id}")
        return {"success": True, "dry_run": True}

    if not _check_api_key():
        return {"success": False, "error": "API key not set"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "POST",
                f"{GHL_BASE_URL}/contacts/{contact_id}/notes",
                headers=_headers(),
                json={"body": body, "userId": GHL_LOCATION_ID},
            )
            if resp.status_code in (200, 201):
                return {"success": True, "note_id": resp.json().get("note", {}).get("id")}
            return {"success": False, "error": resp.text, "status": resp.status_code}
        except httpx.HTTPError as e:
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# OPPORTUNITIES / PIPELINES
# ═══════════════════════════════════════════════════════════════

async def list_pipelines() -> list[dict]:
    """List all pipelines and their stages in the GHL location."""
    if not _check_api_key():
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/opportunities/pipelines",
                headers=_headers(),
                params={"locationId": GHL_LOCATION_ID},
            )
            if resp.status_code == 200:
                return resp.json().get("pipelines", [])
            logger.error(f"GHL list pipelines failed ({resp.status_code})")
        except httpx.HTTPError as e:
            logger.error(f"GHL list pipelines failed: {e}")
    return []


async def list_opportunities(
    pipeline_id: str = "",
    stage_id: str = "",
    limit: int = 50,
    offset: int = 0,
    status: str = "open",
) -> dict:
    """
    List opportunities with optional pipeline/stage filtering.
    Returns dict with 'opportunities' list and 'meta'.
    """
    if not _check_api_key():
        return {"opportunities": [], "meta": {}}

    params = {
        "locationId": GHL_LOCATION_ID,
        "limit": limit,
        "skip": offset,
        "status": status,
    }
    if pipeline_id:
        params["pipelineId"] = pipeline_id
    if stage_id:
        params["pipelineStageId"] = stage_id

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/opportunities/search",
                headers=_headers(),
                params=params,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "opportunities": data.get("opportunities", []),
                    "meta": data.get("meta", {}),
                }
            logger.error(f"GHL list opps failed ({resp.status_code})")
        except httpx.HTTPError as e:
            logger.error(f"GHL list opps failed: {e}")
    return {"opportunities": [], "meta": {}}


async def get_opportunity(opportunity_id: str) -> Optional[dict]:
    """Get full details of a single opportunity."""
    if not _check_api_key():
        return None

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/opportunities/{opportunity_id}",
                headers=_headers(),
            )
            if resp.status_code == 200:
                return resp.json().get("opportunity")
        except httpx.HTTPError as e:
            logger.error(f"GHL get opp failed: {e}")
    return None


async def create_opportunity(
    contact_id: str,
    pipeline_id: str,
    stage_id: str,
    name: str,
    monetary_value: float = 0,
    source: str = "AI Prospecting Agent",
) -> dict:
    """Create an opportunity linked to a contact."""
    if DRY_RUN:
        logger.info(f"DRY RUN: Would create opp '{name}' for contact {contact_id}")
        return {"success": True, "dry_run": True}

    if not _check_api_key():
        return {"success": False, "error": "API key not set"}

    payload = {
        "locationId": GHL_LOCATION_ID,
        "contactId": contact_id,
        "pipelineId": pipeline_id,
        "pipelineStageId": stage_id,
        "name": name,
        "monetaryValue": monetary_value,
        "source": source,
        "status": "open",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "POST",
                f"{GHL_BASE_URL}/opportunities/",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code in (200, 201):
                opp = resp.json().get("opportunity", {})
                return {"success": True, "opportunity_id": opp.get("id")}
            return {"success": False, "error": resp.text, "status": resp.status_code}
        except httpx.HTTPError as e:
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# CONVERSATIONS
# ═══════════════════════════════════════════════════════════════

async def list_conversations(
    limit: int = 50,
    sort_by: str = "last_message_date",
    sort_order: str = "desc",
) -> list[dict]:
    """List recent conversations across all contacts."""
    if not _check_api_key():
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/conversations/search",
                headers=_headers(),
                params={
                    "locationId": GHL_LOCATION_ID,
                    "limit": limit,
                    "sortBy": sort_by,
                    "sortOrder": sort_order,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("conversations", [])
            logger.error(f"GHL list conversations failed ({resp.status_code})")
        except httpx.HTTPError as e:
            logger.error(f"GHL list conversations failed: {e}")
    return []


async def get_conversation_messages(
    conversation_id: str,
    limit: int = 50,
) -> list[dict]:
    """Get messages from a specific conversation."""
    if not _check_api_key():
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/conversations/{conversation_id}/messages",
                headers=_headers(),
                params={"limit": limit},
            )
            if resp.status_code == 200:
                return resp.json().get("messages", {}).get("messages", [])
        except httpx.HTTPError as e:
            logger.error(f"GHL get messages failed: {e}")
    return []


async def get_contact_conversations(contact_id: str) -> list[dict]:
    """Get all conversations for a specific contact."""
    if not _check_api_key():
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/conversations/search",
                headers=_headers(),
                params={
                    "locationId": GHL_LOCATION_ID,
                    "contactId": contact_id,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("conversations", [])
        except httpx.HTTPError as e:
            logger.error(f"GHL contact conversations failed: {e}")
    return []


# ═══════════════════════════════════════════════════════════════
# CALENDARS / APPOINTMENTS
# ═══════════════════════════════════════════════════════════════

async def list_calendars() -> list[dict]:
    """List all calendars in the location."""
    if not _check_api_key():
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/calendars/",
                headers=_headers(),
                params={"locationId": GHL_LOCATION_ID},
            )
            if resp.status_code == 200:
                return resp.json().get("calendars", [])
        except httpx.HTTPError as e:
            logger.error(f"GHL list calendars failed: {e}")
    return []


async def list_appointments(
    calendar_id: str = "",
    start_date: str = "",
    end_date: str = "",
) -> list[dict]:
    """List appointments, optionally filtered by calendar and date range."""
    if not _check_api_key():
        return []

    if not start_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
    if not end_date:
        end_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    params = {
        "locationId": GHL_LOCATION_ID,
        "startDate": start_date,
        "endDate": end_date,
    }
    if calendar_id:
        params["calendarId"] = calendar_id

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"{GHL_BASE_URL}/calendars/events",
                headers=_headers(),
                params=params,
            )
            if resp.status_code == 200:
                return resp.json().get("events", [])
        except httpx.HTTPError as e:
            logger.error(f"GHL list appointments failed: {e}")
    return []


# ═══════════════════════════════════════════════════════════════
# INBOUND DATA SYNC (pull everything useful from GHL)
# ═══════════════════════════════════════════════════════════════

async def pull_inbound_snapshot() -> dict:
    """
    Pull a full snapshot of actionable inbound data from GHL:
      - Recent contacts (last 24h)
      - Open opportunities across all pipelines
      - Recent conversations with unread messages
      - Upcoming appointments

    This gives the agent a complete picture of what's happening
    in the CRM before it starts its daily outbound work.
    """
    logger.info("Pulling GHL inbound snapshot...")

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "new_contacts": [],
        "pipelines": [],
        "open_opportunities": [],
        "recent_conversations": [],
        "upcoming_appointments": [],
    }

    # 1. Recent contacts (newest first)
    result = await list_contacts(limit=50, sort_by="dateAdded", sort_order="desc")
    snapshot["new_contacts"] = result.get("contacts", [])
    logger.info(f"  Pulled {len(snapshot['new_contacts'])} recent contacts")

    # 2. Pipelines + stages (needed to understand opportunity flow)
    snapshot["pipelines"] = await list_pipelines()
    logger.info(f"  Pulled {len(snapshot['pipelines'])} pipelines")

    # 3. Open opportunities
    for pipeline in snapshot["pipelines"]:
        pid = pipeline.get("id", "")
        if pid:
            opp_result = await list_opportunities(pipeline_id=pid, limit=50)
            opps = opp_result.get("opportunities", [])
            for opp in opps:
                opp["_pipeline_name"] = pipeline.get("name", "")
            snapshot["open_opportunities"].extend(opps)
    logger.info(f"  Pulled {len(snapshot['open_opportunities'])} open opportunities")

    # 4. Recent conversations
    snapshot["recent_conversations"] = await list_conversations(limit=30)
    logger.info(f"  Pulled {len(snapshot['recent_conversations'])} recent conversations")

    # 5. Upcoming appointments
    snapshot["upcoming_appointments"] = await list_appointments()
    logger.info(f"  Pulled {len(snapshot['upcoming_appointments'])} upcoming appointments")

    return snapshot


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    import anyio

    async def _test():
        print("=== GHL Inbound Snapshot ===\n")
        snapshot = await pull_inbound_snapshot()
        print(f"New contacts: {len(snapshot['new_contacts'])}")
        print(f"Pipelines: {len(snapshot['pipelines'])}")
        print(f"Open opps: {len(snapshot['open_opportunities'])}")
        print(f"Conversations: {len(snapshot['recent_conversations'])}")
        print(f"Appointments: {len(snapshot['upcoming_appointments'])}")

        # Show pipeline structure
        for p in snapshot["pipelines"]:
            print(f"\nPipeline: {p.get('name')}")
            for stage in p.get("stages", []):
                print(f"  -> {stage.get('name')}")

    anyio.run(_test)
