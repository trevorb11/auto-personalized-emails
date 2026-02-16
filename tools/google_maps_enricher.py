"""
Google Maps Business Enrichment Tool.

Looks up businesses on Google Maps/Places API to get:
  - Phone number
  - Website
  - Rating and review count
  - Business status (open/closed)
  - Category/type

Review count is used as a proxy for business activity and revenue estimation.
"""
import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import GOOGLE_MAPS_API_KEY
from tools.http_retry import fetch

logger = logging.getLogger(__name__)


async def enrich_business_google(
    business_name: str,
    city: str = "",
    state: str = "",
) -> dict:
    """
    Look up a business on Google Maps to get contact info, reviews, and rating.

    Args:
        business_name: Name of the business.
        city: City for narrowing results.
        state: State abbreviation for narrowing results.

    Returns:
        dict with found (bool), and if found: name, phone, website, rating,
        review_count, business_status, categories, estimated_monthly_revenue.
    """
    if not GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY not set, skipping enrichment")
        return {"found": False, "error": "API key not configured"}

    query = f"{business_name} {city} {state}".strip()

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: Find Place
        try:
            search_resp = await fetch(client, "GET",
                "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                params={
                    "input": query,
                    "inputtype": "textquery",
                    "fields": "place_id,name,formatted_address",
                    "key": GOOGLE_MAPS_API_KEY,
                },
            )
            search_resp.raise_for_status()
            candidates = search_resp.json().get("candidates", [])
        except httpx.HTTPError as e:
            logger.error(f"Google Places search failed: {e}")
            return {"found": False, "error": str(e)}

        if not candidates:
            return {"found": False, "query": query}

        # Step 2: Get Place Details
        place_id = candidates[0]["place_id"]
        try:
            details_resp = await fetch(client, "GET",
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={
                    "place_id": place_id,
                    "fields": (
                        "name,formatted_phone_number,website,rating,"
                        "user_ratings_total,business_status,opening_hours,types"
                    ),
                    "key": GOOGLE_MAPS_API_KEY,
                },
            )
            details_resp.raise_for_status()
            result = details_resp.json().get("result", {})
        except httpx.HTTPError as e:
            logger.error(f"Google Places details failed: {e}")
            return {"found": False, "error": str(e)}

    review_count = result.get("user_ratings_total", 0)

    enriched = {
        "found": True,
        "name": result.get("name"),
        "phone": result.get("formatted_phone_number"),
        "website": result.get("website"),
        "rating": result.get("rating"),
        "review_count": review_count,
        "business_status": result.get("business_status"),
        "categories": result.get("types", []),
        "estimated_monthly_revenue": _estimate_revenue(review_count),
    }

    return enriched


def _estimate_revenue(review_count: int) -> str:
    """
    Rough revenue estimate based on Google Maps review count.
    This is a heuristic — more reviews generally correlates with
    higher foot traffic / transaction volume.
    """
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


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    import anyio

    async def _test():
        result = await enrich_business_google(
            "Joe's Auto Repair", "Miami", "FL"
        )
        print(json.dumps(result, indent=2))

    anyio.run(_test)
