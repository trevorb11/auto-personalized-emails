"""
Inbound Lead Discovery & Enrichment Tool.

Discovers new MCA prospects using external data sources:
  - Google Custom Search Engine (find businesses by industry + location)
  - Hunter.io (find email addresses for a domain)
  - Clearbit (company enrichment: revenue, employee count, industry)

Follows the pattern from the inbound-mcp server but integrated
directly into our pipeline with MCA-specific search strategies.

Setup:
  1. Google CSE: console.cloud.google.com → Custom Search JSON API
     Create a search engine at cse.google.com → restrict to business sites
  2. Hunter.io: hunter.io/api → free tier = 25 searches/mo
  3. Clearbit: clearbit.com/docs → company enrichment API
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import HIGH_VALUE_INDUSTRIES
from tools.http_retry import fetch

logger = logging.getLogger(__name__)

# API keys loaded from environment at import time
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
CLEARBIT_API_KEY = os.environ.get("CLEARBIT_API_KEY", "")

_TIMEOUT = 15.0

# ── MCA-specific search queries ────────────────────────────────
# These are designed to surface businesses with funding intent signals.
# Three tiers: (1) direct funding intent, (2) growth/expansion signals,
# (3) cash-flow-intensive operations by industry.
MCA_SEARCH_STRATEGIES = [
    # ── Tier 1: Direct funding intent ─────────────────────────
    '"{industry}" "{city}" "looking for funding" OR "need capital" OR "business loan"',
    '"{industry}" "{city}" "working capital" OR "cash advance" OR "business financing"',
    '"{industry}" "{city}" "equipment financing" OR "equipment lease" OR "fleet financing"',

    # ── Tier 2: Growth / expansion signals ────────────────────
    # Businesses hiring = growing = needing capital
    '"{industry}" "{city}" "now hiring" OR "we are hiring" OR "join our team"',
    # Expansion signals = new locations, franchises, renovations
    '"{industry}" "{city}" "new location" OR "expanding" OR "grand opening" OR "coming soon"',
    '"{industry}" "{city}" "franchise" OR "second location" OR "renovation"',

    # ── Tier 3: Industry-specific high-cash-flow searches ─────
    # Trucking (your #1 industry) — DOT/MC numbers = active carriers
    'trucking company "{city}" "{state}" DOT number MC authority',
    'freight carrier "{city}" "{state}" hiring drivers OR "owner operator"',
    '"{city}" "{state}" trucking "new trucks" OR "fleet" OR "expanding fleet"',

    # Construction — licensed/bonded = active, bidding = need capital
    'general contractor "{city}" "{state}" licensed bonded insured',
    'construction company "{city}" "{state}" "hiring" OR "new project" OR "bidding"',

    # Restaurant — DoorDash/UberEats presence = active
    'restaurant "{city}" "{state}" yelp OR doordash OR ubereats',
    'restaurant "{city}" "{state}" "grand opening" OR "new menu" OR "renovation"',

    # Auto repair — high parts cost = cash flow need
    'auto repair "{city}" "{state}" "ASE certified" OR "now hiring mechanics"',

    # Healthcare / dental — insurance billing gaps = cash flow strain
    'medical practice "{city}" "{state}" "accepting patients" OR "now open"',
    'dental office "{city}" "{state}" "new patients" OR "expanding"',

    # Landscaping — seasonal + equipment-heavy
    'landscaping company "{city}" "{state}" "hiring" OR "new equipment" OR "commercial"',

    # ── Tier 4: General business by industry + location ───────
    '"{industry}" business "{city}" "{state}"',
]

# ── Google Maps Text Search query templates ───────────────────
# These return ACTUAL BUSINESSES (not web pages) from Google Maps.
# Much higher signal-to-noise ratio than CSE for lead discovery.
MAPS_SEARCH_QUERIES = {
    "trucking": [
        "trucking companies in {city}, {state}",
        "freight carriers in {city}, {state}",
        "logistics companies in {city}, {state}",
    ],
    "construction": [
        "construction companies in {city}, {state}",
        "general contractors in {city}, {state}",
    ],
    "restaurant": [
        "restaurants in {city}, {state}",
    ],
    "auto repair": [
        "auto repair shops in {city}, {state}",
        "auto body shops in {city}, {state}",
    ],
    "healthcare": [
        "medical clinics in {city}, {state}",
        "medical practices in {city}, {state}",
    ],
    "dental": [
        "dental offices in {city}, {state}",
        "dentists in {city}, {state}",
    ],
    "retail": [
        "retail stores in {city}, {state}",
    ],
    "manufacturing": [
        "manufacturing companies in {city}, {state}",
    ],
    "landscaping": [
        "landscaping companies in {city}, {state}",
    ],
    "plumbing": [
        "plumbing companies in {city}, {state}",
    ],
    "hvac": [
        "HVAC companies in {city}, {state}",
    ],
    "roofing": [
        "roofing companies in {city}, {state}",
    ],
}


# ═══════════════════════════════════════════════════════════════
# GOOGLE MAPS — PLACES TEXT SEARCH (PRIMARY DISCOVERY)
# ═══════════════════════════════════════════════════════════════

async def search_google_maps_places(
    query: str,
    max_pages: int = 1,
) -> list[dict]:
    """
    Search Google Maps via Places Text Search API.

    Returns actual businesses (not web pages) with:
    name, address, place_id, rating, review_count, types, business_status.

    Each page returns up to 20 results. max_pages=3 yields up to 60 results.
    Note: Google requires ~2s delay between page requests.
    """
    if not GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY not configured for discovery")
        return []

    all_results = []
    next_page_token = None

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for page in range(max_pages):
            try:
                params = {"key": GOOGLE_MAPS_API_KEY}

                if next_page_token:
                    params["pagetoken"] = next_page_token
                    # Google requires ~2s delay before using next_page_token
                    await asyncio.sleep(2.0)
                else:
                    params["query"] = query

                resp = await fetch(client, "GET",
                    "https://maps.googleapis.com/maps/api/place/textsearch/json",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") not in ("OK", "ZERO_RESULTS"):
                    logger.error(f"Maps Text Search status: {data.get('status')} - {data.get('error_message', '')}")
                    break

                for r in data.get("results", []):
                    all_results.append({
                        "name": r.get("name", ""),
                        "address": r.get("formatted_address", ""),
                        "place_id": r.get("place_id", ""),
                        "rating": r.get("rating", 0),
                        "review_count": r.get("user_ratings_total", 0),
                        "types": r.get("types", []),
                        "business_status": r.get("business_status", ""),
                    })

                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    break

            except httpx.HTTPError as e:
                logger.error(f"Google Maps Text Search failed: {e}")
                break

    return all_results


async def get_place_contact_details(place_id: str) -> dict:
    """
    Get phone number and website for a business via Google Places Details API.

    This is a separate API call per business — use selectively.
    """
    if not GOOGLE_MAPS_API_KEY:
        return {}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={
                    "place_id": place_id,
                    "fields": "formatted_phone_number,website",
                    "key": GOOGLE_MAPS_API_KEY,
                },
            )
            resp.raise_for_status()
            result = resp.json().get("result", {})
            return {
                "phone": result.get("formatted_phone_number", ""),
                "website": result.get("website", ""),
            }
        except httpx.HTTPError as e:
            logger.error(f"Place details failed for {place_id}: {e}")
            return {}


async def _discover_via_maps(
    industry: str,
    city: str,
    state: str,
    max_results: int,
) -> list[dict]:
    """
    Discover businesses via Google Maps Places Text Search, then
    fetch contact details (phone/website) for each result.

    Returns list of dicts in the same format as CSE discovery
    (title, link, snippet, domain) plus Maps-specific fields
    (phone, rating, review_count, types, place_id, source_method).
    """
    ind_lower = industry.lower()
    templates = MAPS_SEARCH_QUERIES.get(ind_lower)
    if not templates:
        templates = [f"{industry} companies in {{city}}, {{state}}"]

    all_results = []
    seen_place_ids = set()

    for template in templates[:2]:  # Max 2 queries per industry+city
        query = template.format(city=city, state=state)

        pages_needed = min(3, max(1, (max_results - len(all_results) + 19) // 20))
        raw_results = await search_google_maps_places(query, max_pages=pages_needed)

        for r in raw_results:
            place_id = r.get("place_id", "")
            if place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            # Skip permanently closed businesses
            if r.get("business_status") == "CLOSED_PERMANENTLY":
                continue

            all_results.append(r)
            if len(all_results) >= max_results:
                break

        if len(all_results) >= max_results:
            break

    if not all_results:
        return []

    # Fetch contact details (phone + website) for each business
    normalized = []
    for r in all_results:
        details = await get_place_contact_details(r["place_id"])

        website = details.get("website", "")
        domain = urlparse(website).netloc if website else ""

        normalized.append({
            # Standard fields (compatible with CSE format)
            "title": r["name"],
            "link": website,
            "snippet": r.get("address", ""),
            "domain": domain,
            # Maps-specific fields
            "phone": details.get("phone", ""),
            "rating": r.get("rating", 0),
            "review_count": r.get("review_count", 0),
            "types": r.get("types", []),
            "place_id": r.get("place_id", ""),
            "business_status": r.get("business_status", ""),
            "source_method": "google_maps",
        })

    logger.info(
        f"Maps discovery: {len(normalized)} businesses "
        f"for {industry} in {city}, {state}"
    )
    return normalized


# ═══════════════════════════════════════════════════════════════
# GOOGLE CUSTOM SEARCH ENGINE (FALLBACK)
# ═══════════════════════════════════════════════════════════════

async def search_google_cse(
    query: str,
    num_results: int = 10,
) -> list[dict]:
    """
    Search Google Custom Search Engine for business leads.

    Returns list of dicts with: title, link, snippet, domain.
    """
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_ID:
        logger.warning("Google CSE not configured (GOOGLE_CSE_API_KEY / GOOGLE_CSE_ID)")
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": GOOGLE_CSE_API_KEY,
                    "cx": GOOGLE_CSE_ID,
                    "q": query,
                    "num": min(num_results, 10),
                },
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])

            results = []
            for item in items:
                link = item.get("link", "")
                domain = urlparse(link).netloc if link else ""
                results.append({
                    "title": item.get("title", ""),
                    "link": link,
                    "snippet": item.get("snippet", ""),
                    "domain": domain,
                })
            return results

        except httpx.HTTPError as e:
            logger.error(f"Google CSE search failed: {e}")
            return []


async def discover_businesses(
    industry: str,
    city: str,
    state: str,
    max_results: int = 20,
) -> list[dict]:
    """
    Discover businesses in a specific industry and location.

    Primary: Google Maps Places Text Search (returns real businesses
    with structured data — name, address, phone, rating, reviews).
    Fallback: Google CSE (returns web pages to be parsed).

    Returns a list of business leads with available contact info.
    """
    # ── Primary: Google Maps Text Search ──────────────────────
    if GOOGLE_MAPS_API_KEY:
        maps_results = await _discover_via_maps(industry, city, state, max_results)
        if maps_results:
            return maps_results
        logger.info("Maps returned 0 results, falling back to CSE")

    # ── Fallback: Google CSE ──────────────────────────────────
    return await _discover_via_cse(industry, city, state, max_results)


async def _discover_via_cse(
    industry: str,
    city: str,
    state: str,
    max_results: int,
) -> list[dict]:
    """Fallback discovery via Google Custom Search Engine (web pages)."""
    all_results = []
    seen_domains = set()

    # Pick relevant search strategies
    strategies = []
    ind_lower = industry.lower()

    industry_matches = {
        "truck": ["trucking", "freight", "carrier"],
        "construct": ["contractor", "construction"],
        "restaurant": ["restaurant"],
        "auto": ["auto repair"],
        "medical": ["medical practice"],
        "dental": ["dental"],
        "landscap": ["landscaping"],
    }

    for template in MCA_SEARCH_STRATEGIES:
        if "{industry}" in template:
            strategies.append(
                template.format(industry=industry, city=city, state=state)
            )
        else:
            for ind_key, template_keywords in industry_matches.items():
                if ind_key in ind_lower:
                    if any(kw in template.lower() for kw in template_keywords):
                        strategies.append(template.format(city=city, state=state))
                        break

    if not strategies:
        strategies = [f'"{industry}" business "{city}" "{state}"']

    for query in strategies[:4]:
        results = await search_google_cse(query, num_results=10)

        for r in results:
            domain = r.get("domain", "")
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                all_results.append(r)

        if len(all_results) >= max_results:
            break

    logger.info(f"CSE fallback: {len(all_results)} results for {industry} in {city}, {state}")
    return all_results[:max_results]


# ═══════════════════════════════════════════════════════════════
# HUNTER.IO — EMAIL FINDER
# ═══════════════════════════════════════════════════════════════

async def find_emails_hunter(domain: str) -> dict:
    """
    Use Hunter.io to find email addresses associated with a domain.

    Returns dict with:
      - emails: list of {value, type, confidence, first_name, last_name, position}
      - organization: company name
      - pattern: email pattern (e.g., {first}@domain.com)
    """
    if not HUNTER_API_KEY:
        logger.warning("HUNTER_API_KEY not configured")
        return {"emails": [], "organization": "", "pattern": ""}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain,
                    "api_key": HUNTER_API_KEY,
                    "limit": 10,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})

            emails = []
            for e in data.get("emails", []):
                emails.append({
                    "value": e.get("value", ""),
                    "type": e.get("type", ""),
                    "confidence": e.get("confidence", 0),
                    "first_name": e.get("first_name", ""),
                    "last_name": e.get("last_name", ""),
                    "position": e.get("position", ""),
                })

            return {
                "emails": emails,
                "organization": data.get("organization", ""),
                "pattern": data.get("pattern", ""),
            }

        except httpx.HTTPError as e:
            logger.error(f"Hunter.io search failed for {domain}: {e}")
            return {"emails": [], "organization": "", "pattern": ""}


async def verify_email_hunter(email: str) -> dict:
    """
    Verify if an email address is valid using Hunter.io.

    Returns dict with: result (deliverable/undeliverable/risky),
    score (0-100), and details.
    """
    if not HUNTER_API_KEY:
        return {"result": "unknown", "score": 0}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                "https://api.hunter.io/v2/email-verifier",
                params={
                    "email": email,
                    "api_key": HUNTER_API_KEY,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {
                "result": data.get("result", "unknown"),
                "score": data.get("score", 0),
                "status": data.get("status", ""),
            }
        except httpx.HTTPError as e:
            logger.error(f"Hunter.io verify failed for {email}: {e}")
            return {"result": "unknown", "score": 0}


# ═══════════════════════════════════════════════════════════════
# CLEARBIT — COMPANY ENRICHMENT
# ═══════════════════════════════════════════════════════════════

async def enrich_company_clearbit(domain: str) -> dict:
    """
    Enrich a company using Clearbit's Company API.

    Returns structured data: name, industry, sub_industry,
    employee_count, estimated_annual_revenue, location, description,
    tech stack, social profiles, etc.
    """
    if not CLEARBIT_API_KEY:
        logger.warning("CLEARBIT_API_KEY not configured")
        return {"found": False}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await fetch(client, "GET",
                f"https://company.clearbit.com/v2/companies/find",
                headers={"Authorization": f"Bearer {CLEARBIT_API_KEY}"},
                params={"domain": domain},
            )

            if resp.status_code == 200:
                data = resp.json()
                metrics = data.get("metrics", {})
                geo = data.get("geo", {})
                category = data.get("category", {})

                return {
                    "found": True,
                    "name": data.get("name", ""),
                    "legal_name": data.get("legalName", ""),
                    "domain": data.get("domain", ""),
                    "description": data.get("description", ""),
                    "industry": category.get("industry", ""),
                    "sub_industry": category.get("subIndustry", ""),
                    "sector": category.get("sector", ""),
                    "employee_count": metrics.get("employees", 0),
                    "employee_range": metrics.get("employeesRange", ""),
                    "estimated_annual_revenue": metrics.get("estimatedAnnualRevenue", ""),
                    "annual_revenue_range": metrics.get("annualRevenue", ""),
                    "raised": metrics.get("raised", 0),
                    "city": geo.get("city", ""),
                    "state": geo.get("state", ""),
                    "country": geo.get("country", ""),
                    "phone": data.get("phone", ""),
                    "founded_year": data.get("foundedYear"),
                    "logo": data.get("logo", ""),
                    "linkedin_handle": (data.get("linkedin") or {}).get("handle", ""),
                    "twitter_handle": (data.get("twitter") or {}).get("handle", ""),
                    "facebook_handle": (data.get("facebook") or {}).get("handle", ""),
                    "tech": data.get("tech", []),
                }

            elif resp.status_code == 404:
                return {"found": False, "domain": domain}
            else:
                logger.error(f"Clearbit failed ({resp.status_code}) for {domain}")
                return {"found": False, "error": resp.text}

        except httpx.HTTPError as e:
            logger.error(f"Clearbit request failed for {domain}: {e}")
            return {"found": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# FULL DISCOVERY + ENRICHMENT PIPELINE
# ═══════════════════════════════════════════════════════════════

async def discover_and_enrich(
    industry: str,
    city: str,
    state: str,
    max_leads: int = 10,
    enrich: bool = True,
) -> list[dict]:
    """
    Full inbound lead discovery pipeline:
      1. Google Maps Text Search (primary) or CSE (fallback) → find businesses
      2. Hunter.io → find decision-maker emails (if domain available)
      3. Clearbit → enrich with revenue, employee count, industry data

    Returns a list of enriched lead dicts ready for scoring and GHL.
    """
    # Step 1: Discover businesses
    search_results = await discover_businesses(industry, city, state, max_results=max_leads)

    if not search_results:
        return []

    leads = []
    for result in search_results:
        domain = result.get("domain", "")
        phone = result.get("phone", "")

        # Need at least a domain or phone to be a useful lead
        if not domain and not phone:
            continue

        lead = {
            "business_name": result.get("title", "").split(" - ")[0].split(" | ")[0].strip(),
            "website": result.get("link", ""),
            "domain": domain,
            "snippet": result.get("snippet", ""),
            "city": city,
            "state": state,
            "industry": industry,
            "source": "inbound-discovery",
            "discovered_at": datetime.now().isoformat(),
        }

        # Carry over Maps-specific data if present
        if result.get("source_method") == "google_maps":
            lead["phone"] = phone
            lead["google_rating"] = result.get("rating", 0)
            lead["google_review_count"] = result.get("review_count", 0)
            lead["monthly_revenue"] = _estimate_revenue_from_reviews(
                result.get("review_count", 0)
            )
            lead["address"] = result.get("snippet", "")  # Maps snippet is address

            # Infer industry from Google types if available
            inferred = _infer_industry_from_types(result.get("types", []))
            if inferred:
                lead["industry"] = inferred

        if not enrich:
            leads.append(lead)
            continue

        # Step 2: Find emails via Hunter.io (only if we have a domain)
        if domain:
            hunter_data = await find_emails_hunter(domain)
            if hunter_data.get("emails"):
                best_email = _pick_best_email(hunter_data["emails"])
                if best_email:
                    lead["email"] = best_email["value"]
                    lead["first_name"] = best_email.get("first_name", "")
                    lead["last_name"] = best_email.get("last_name", "")
                    lead["contact_position"] = best_email.get("position", "")
                    lead["email_confidence"] = best_email.get("confidence", 0)

            if hunter_data.get("organization"):
                lead["business_name"] = hunter_data["organization"]

        # Step 3: Enrich via Clearbit (only if we have a domain)
        if domain:
            clearbit_data = await enrich_company_clearbit(domain)
            if clearbit_data.get("found"):
                lead["business_name"] = clearbit_data.get("name") or lead["business_name"]
                lead["industry"] = clearbit_data.get("sub_industry") or clearbit_data.get("industry") or lead.get("industry", industry)
                lead["employee_count"] = clearbit_data.get("employee_count", 0)
                lead["estimated_annual_revenue"] = clearbit_data.get("estimated_annual_revenue", "")
                lead["phone"] = clearbit_data.get("phone", "") or lead.get("phone", "")
                lead["description"] = clearbit_data.get("description", "")
                lead["linkedin"] = clearbit_data.get("linkedin_handle", "")
                lead["founded_year"] = clearbit_data.get("founded_year")

                # Convert annual revenue to monthly for scoring
                annual = clearbit_data.get("estimated_annual_revenue", "")
                if annual:
                    lead["monthly_revenue"] = _annual_to_monthly_str(annual)

                # Estimate years in business
                founded = clearbit_data.get("founded_year")
                if founded:
                    lead["years_in_business"] = datetime.now().year - founded

        leads.append(lead)

    logger.info(
        f"Discovered and enriched {len(leads)} leads "
        f"for {industry} in {city}, {state}"
    )
    return leads


def _pick_best_email(emails: list[dict]) -> Optional[dict]:
    """Pick the most useful email (owner/CEO > generic > other)."""
    # Priority: owner/founder/CEO/president
    owner_keywords = ["owner", "founder", "ceo", "president", "director", "principal"]
    for e in emails:
        pos = (e.get("position") or "").lower()
        if any(kw in pos for kw in owner_keywords):
            return e

    # Next: highest confidence
    sorted_emails = sorted(emails, key=lambda e: e.get("confidence", 0), reverse=True)
    if sorted_emails:
        return sorted_emails[0]

    return None


def _annual_to_monthly_str(annual_str: str) -> str:
    """Convert Clearbit annual revenue string to monthly estimate."""
    # Clearbit returns ranges like "$1M-$10M"
    annual_str = str(annual_str).upper().replace("$", "").replace(",", "").strip()
    try:
        if "-" in annual_str:
            low = annual_str.split("-")[0].strip()
        else:
            low = annual_str

        multiplier = 1
        if "B" in low:
            multiplier = 1_000_000_000
            low = low.replace("B", "")
        elif "M" in low:
            multiplier = 1_000_000
            low = low.replace("M", "")
        elif "K" in low:
            multiplier = 1_000
            low = low.replace("K", "")

        annual = float(low) * multiplier
        monthly = annual / 12
        if monthly >= 100000:
            return f"${monthly/1000:.0f}K+/mo"
        elif monthly >= 10000:
            return f"${monthly/1000:.0f}K/mo"
        else:
            return f"${monthly:,.0f}/mo"
    except (ValueError, TypeError):
        return ""


def _estimate_revenue_from_reviews(review_count: int) -> str:
    """Rough monthly revenue estimate based on Google Maps review count."""
    if review_count >= 500:
        return "$100K+/mo"
    elif review_count >= 200:
        return "$50K-$100K/mo"
    elif review_count >= 100:
        return "$25K-$50K/mo"
    elif review_count >= 50:
        return "$15K-$30K/mo"
    elif review_count >= 20:
        return "$10K-$20K/mo"
    else:
        return "Under $10K/mo"


def _infer_industry_from_types(google_types: list[str]) -> str:
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
        "beauty_salon": "beauty",
        "hair_care": "beauty",
        "gym": "fitness",
        "lodging": "hospitality",
        "gas_station": "gas station",
    }
    for gtype in google_types:
        if gtype in type_map:
            return type_map[gtype]
    return ""


# ═══════════════════════════════════════════════════════════════
# SEARCH TARGETS — MCA-SPECIFIC
# ═══════════════════════════════════════════════════════════════

def get_mca_search_targets(
    states: list[str],
    cities_per_state: int = 3,
) -> list[dict]:
    """
    Generate a list of industry + location targets for MCA prospecting.
    These are the search combinations the agent will run daily.
    """
    # Top MCA cities by deal volume
    state_cities = {
        "FL": ["Miami", "Tampa", "Orlando", "Jacksonville", "Fort Lauderdale"],
        "CA": ["Los Angeles", "San Diego", "Sacramento", "San Francisco", "Fresno"],
        "NY": ["New York", "Brooklyn", "Bronx", "Buffalo", "Albany"],
        "TX": ["Houston", "Dallas", "San Antonio", "Austin", "Fort Worth"],
        "NJ": ["Newark", "Jersey City", "Paterson", "Elizabeth", "Trenton"],
        "IL": ["Chicago", "Aurora", "Rockford", "Joliet", "Naperville"],
        "GA": ["Atlanta", "Augusta", "Savannah", "Columbus", "Macon"],
        "PA": ["Philadelphia", "Pittsburgh", "Allentown", "Erie", "Reading"],
    }

    # Top industries by MCA funding volume and intent-signal density
    top_industries = [
        "trucking", "construction", "restaurant", "auto repair",
        "healthcare", "dental", "retail", "manufacturing",
        "landscaping", "plumbing", "hvac", "roofing",
    ]

    targets = []
    for state in states:
        cities = state_cities.get(state, [f"{state} businesses"])[:cities_per_state]
        for city in cities:
            for industry in top_industries[:6]:  # Top 6 per city for broader reach
                targets.append({
                    "industry": industry,
                    "city": city,
                    "state": state,
                })

    return targets


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    import anyio

    async def _test():
        # Test the full pipeline
        leads = await discover_and_enrich(
            industry="trucking",
            city="Miami",
            state="FL",
            max_leads=5,
            enrich=True,
        )
        for lead in leads:
            print(f"\n{'='*60}")
            print(f"Business: {lead.get('business_name')}")
            print(f"Website:  {lead.get('website')}")
            print(f"Email:    {lead.get('email', 'N/A')}")
            print(f"Phone:    {lead.get('phone', 'N/A')}")
            print(f"Industry: {lead.get('industry')}")
            print(f"Revenue:  {lead.get('monthly_revenue', 'N/A')}")
            print(f"Employees: {lead.get('employee_count', 'N/A')}")

    anyio.run(_test)
