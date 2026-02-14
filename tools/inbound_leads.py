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
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import HIGH_VALUE_INDUSTRIES

logger = logging.getLogger(__name__)

# API keys loaded from environment at import time
import os

GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
CLEARBIT_API_KEY = os.environ.get("CLEARBIT_API_KEY", "")

_TIMEOUT = 15.0

# ── MCA-specific search queries ────────────────────────────────
# These are designed to surface businesses likely to need funding
MCA_SEARCH_STRATEGIES = [
    # Businesses actively seeking funding
    '"{industry}" "{city}" "looking for funding" OR "need capital" OR "business loan"',
    # Businesses in growth mode (high MCA demand)
    '"{industry}" "{city}" "now hiring" OR "expanding" OR "new location"',
    # Businesses with cash flow signals
    '"{industry}" "{city}" "equipment financing" OR "working capital"',
    # Find businesses by industry + location (broad)
    '"{industry}" business "{city}" "{state}"',
    # Trucking-specific (your #1 industry)
    'trucking company "{city}" "{state}" DOT number MC authority',
    # Construction-specific
    'general contractor "{city}" "{state}" licensed bonded',
    # Restaurant-specific
    'restaurant "{city}" "{state}" yelp OR doordash',
]


# ═══════════════════════════════════════════════════════════════
# GOOGLE CUSTOM SEARCH ENGINE
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
            resp = await client.get(
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
    Discover businesses in a specific industry and location
    using targeted Google CSE queries.

    Returns a list of business leads with available contact info.
    """
    all_results = []
    seen_domains = set()

    # Pick relevant search strategies
    strategies = []
    for template in MCA_SEARCH_STRATEGIES:
        if "{industry}" in template:
            strategies.append(
                template.format(industry=industry, city=city, state=state)
            )
        elif "trucking" in template and "truck" in industry.lower():
            strategies.append(template.format(city=city, state=state))
        elif "contractor" in template and "construct" in industry.lower():
            strategies.append(template.format(city=city, state=state))
        elif "restaurant" in template and "restaurant" in industry.lower():
            strategies.append(template.format(city=city, state=state))

    # If no industry-specific strategies matched, use the broad one
    if not strategies:
        strategies = [f'"{industry}" business "{city}" "{state}"']

    for query in strategies[:3]:  # Limit to 3 queries to control API costs
        results = await search_google_cse(query, num_results=10)

        for r in results:
            domain = r.get("domain", "")
            # Deduplicate by domain
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                all_results.append(r)

        if len(all_results) >= max_results:
            break

    logger.info(f"Discovered {len(all_results)} businesses for {industry} in {city}, {state}")
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
            resp = await client.get(
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
            resp = await client.get(
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
            resp = await client.get(
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
      1. Google CSE → find businesses by industry + location
      2. Hunter.io → find decision-maker emails for each domain
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
        if not domain:
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

        if not enrich:
            leads.append(lead)
            continue

        # Step 2: Find emails via Hunter.io
        hunter_data = await find_emails_hunter(domain)
        if hunter_data.get("emails"):
            # Prefer owner/founder/CEO, then generic emails
            best_email = _pick_best_email(hunter_data["emails"])
            if best_email:
                lead["email"] = best_email["value"]
                lead["first_name"] = best_email.get("first_name", "")
                lead["last_name"] = best_email.get("last_name", "")
                lead["contact_position"] = best_email.get("position", "")
                lead["email_confidence"] = best_email.get("confidence", 0)

        if hunter_data.get("organization"):
            lead["business_name"] = hunter_data["organization"]

        # Step 3: Enrich via Clearbit
        clearbit_data = await enrich_company_clearbit(domain)
        if clearbit_data.get("found"):
            lead["business_name"] = clearbit_data.get("name") or lead["business_name"]
            lead["industry"] = clearbit_data.get("sub_industry") or clearbit_data.get("industry") or industry
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

    # Top industries by MCA funding volume
    top_industries = [
        "trucking", "construction", "restaurant", "auto repair",
        "healthcare", "retail", "manufacturing", "landscaping",
    ]

    targets = []
    for state in states:
        cities = state_cities.get(state, [f"{state} businesses"])[:cities_per_state]
        for city in cities:
            for industry in top_industries[:4]:  # Top 4 per city to limit API costs
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
