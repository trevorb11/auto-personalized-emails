"""
UCC Filing Scraper via Apify Playwright.

Scrapes UCC filings from secretary of state websites for FL, CA, NY, TX
using Apify's playwright-scraper actor. Falls back to direct requests
where possible.

Each state has its own search URL and page-scraping logic.
Results are inserted into the SQLite filings table.

Usage:
    # Scrape all target states
    python scripts/scrape_ucc.py

    # Scrape specific states
    python scripts/scrape_ucc.py --states FL,TX

    # Dry run (fetch but don't insert into DB)
    python scripts/scrape_ucc.py --dry-run

    # Use direct HTTP instead of Apify (no API token needed)
    python scripts/scrape_ucc.py --direct
"""
import argparse
import asyncio
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import APIFY_API_TOKEN, DB_PATH, MCA_LENDERS, TARGET_STATES
from data.database import init_db, get_connection, insert_filing
from scripts.download_ucc import classify_secured_party

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ucc-scraper")

APIFY_BASE = "https://api.apify.com/v2"
PLAYWRIGHT_ACTOR = "apify/playwright-scraper"

# ── State-specific scraping configurations ────────────────────
# Each state has a start URL, optional page function (JS), and
# a Python parser to normalize results into our filing schema.

STATE_CONFIGS = {
    "FL": {
        "name": "Florida",
        "source": "sunbiz.org",
        "start_urls": [
            "https://search.sunbiz.org/Inquiry/UCCFiling/SearchByName"
        ],
        "page_function": """
        async function pageFunction(context) {
            const { page, request, log } = context;
            const results = [];

            // Search for recent UCC filings by date range
            const today = new Date();
            const daysBack = 30;
            const startDate = new Date(today - daysBack * 24 * 60 * 60 * 1000);

            const formatDate = (d) => `${d.getMonth()+1}/${d.getDate()}/${d.getFullYear()}`;

            // Fill in the search form - search by secured party names
            const lenders = ['yellowstone', 'credibly', 'rapid finance', 'kapitus',
                           'forward financing', 'fundkite', 'libertas', 'fora financial',
                           'national funding', 'ondeck', 'cloudfund', 'pearl capital'];

            for (const lender of lenders) {
                try {
                    await page.goto('https://search.sunbiz.org/Inquiry/UCCFiling/SearchByName');
                    await page.waitForSelector('#SearchTerm', { timeout: 10000 });
                    await page.fill('#SearchTerm', lender);
                    await page.click('input[type="submit"]');
                    await page.waitForTimeout(2000);

                    // Extract results from the table
                    const rows = await page.$$eval('table.resulttbl tbody tr', (trs) => {
                        return trs.map(tr => {
                            const cells = tr.querySelectorAll('td');
                            if (cells.length >= 4) {
                                return {
                                    filing_number: cells[0]?.innerText?.trim() || '',
                                    filing_date: cells[1]?.innerText?.trim() || '',
                                    debtor_name: cells[2]?.innerText?.trim() || '',
                                    secured_party: cells[3]?.innerText?.trim() || '',
                                };
                            }
                            return null;
                        }).filter(Boolean);
                    });

                    results.push(...rows);
                    log.info(`Found ${rows.length} filings for ${lender}`);
                } catch (e) {
                    log.warning(`Error searching for ${lender}: ${e.message}`);
                }
            }

            return results;
        }
        """,
        "use_ftp": True,  # Florida also has free FTP; prefer that
    },
    "NY": {
        "name": "New York",
        "source": "dos.ny.gov",
        "start_urls": [
            "https://appext20.dos.ny.gov/pls/ucc_public/web_search.main_frame"
        ],
        "page_function": """
        async function pageFunction(context) {
            const { page, request, log } = context;
            const results = [];

            const lenders = ['yellowstone', 'credibly', 'rapid finance', 'kapitus',
                           'forward financing', 'fundkite', 'libertas', 'fora financial',
                           'national funding', 'ondeck', 'cloudfund', 'pearl capital'];

            for (const lender of lenders) {
                try {
                    await page.goto('https://appext20.dos.ny.gov/pls/ucc_public/web_search.main_frame');
                    await page.waitForTimeout(2000);

                    // NY uses frames - navigate to the search frame
                    const frames = page.frames();
                    let searchFrame = frames.find(f => f.url().includes('web_search'));
                    if (!searchFrame) searchFrame = page;

                    // Fill secured party name search
                    const input = await searchFrame.$('input[name="p_sp_name"]') ||
                                  await searchFrame.$('input[name="SECURED_NAME"]');
                    if (input) {
                        await input.fill(lender);
                        const submit = await searchFrame.$('input[type="submit"]');
                        if (submit) await submit.click();
                        await page.waitForTimeout(3000);

                        // Extract results
                        const rows = await searchFrame.$$eval('table tr', (trs) => {
                            return trs.slice(1).map(tr => {
                                const cells = tr.querySelectorAll('td');
                                if (cells.length >= 4) {
                                    return {
                                        filing_number: cells[0]?.innerText?.trim() || '',
                                        filing_date: cells[1]?.innerText?.trim() || '',
                                        debtor_name: cells[2]?.innerText?.trim() || '',
                                        secured_party: cells[3]?.innerText?.trim() || '',
                                    };
                                }
                                return null;
                            }).filter(Boolean);
                        });

                        results.push(...rows);
                        log.info(`NY: Found ${rows.length} filings for ${lender}`);
                    }
                } catch (e) {
                    log.warning(`NY error for ${lender}: ${e.message}`);
                }
            }

            return results;
        }
        """,
    },
    "CA": {
        "name": "California",
        "source": "bizfileonline.sos.ca.gov",
        "start_urls": [
            "https://bizfileonline.sos.ca.gov/search/ucc"
        ],
        "page_function": """
        async function pageFunction(context) {
            const { page, request, log } = context;
            const results = [];

            const lenders = ['yellowstone', 'credibly', 'rapid finance', 'kapitus',
                           'forward financing', 'fundkite', 'libertas', 'fora financial',
                           'national funding', 'ondeck', 'cloudfund', 'pearl capital'];

            for (const lender of lenders) {
                try {
                    await page.goto('https://bizfileonline.sos.ca.gov/search/ucc');
                    await page.waitForTimeout(2000);

                    // Search by secured party / organization name
                    const searchInput = await page.$('#SearchCriteria') ||
                                        await page.$('input[name="SearchCriteria"]') ||
                                        await page.$('input[type="text"]');
                    if (searchInput) {
                        await searchInput.fill(lender);

                        // Select "Secured Party" search type if available
                        const typeSelect = await page.$('select');
                        if (typeSelect) {
                            await typeSelect.selectOption({ label: 'Secured Party' }).catch(() => {});
                        }

                        const submit = await page.$('button[type="submit"]') ||
                                        await page.$('input[type="submit"]');
                        if (submit) await submit.click();
                        await page.waitForTimeout(3000);

                        // Extract results
                        const rows = await page.$$eval('.search-results tr, table.results tr', (trs) => {
                            return trs.slice(1).map(tr => {
                                const cells = tr.querySelectorAll('td');
                                if (cells.length >= 3) {
                                    return {
                                        filing_number: cells[0]?.innerText?.trim() || '',
                                        filing_date: cells[1]?.innerText?.trim() || '',
                                        debtor_name: cells[2]?.innerText?.trim() || '',
                                        secured_party: cells[3]?.innerText?.trim() || '',
                                    };
                                }
                                return null;
                            }).filter(Boolean);
                        });

                        results.push(...rows);
                        log.info(`CA: Found ${rows.length} filings for ${lender}`);
                    }
                } catch (e) {
                    log.warning(`CA error for ${lender}: ${e.message}`);
                }
            }

            return results;
        }
        """,
    },
    "TX": {
        "name": "Texas",
        "source": "direct.sos.state.tx.us",
        "start_urls": [
            "https://direct.sos.state.tx.us/UCC/default.asp"
        ],
        "page_function": """
        async function pageFunction(context) {
            const { page, request, log } = context;
            const results = [];

            const lenders = ['yellowstone', 'credibly', 'rapid finance', 'kapitus',
                           'forward financing', 'fundkite', 'libertas', 'fora financial',
                           'national funding', 'ondeck', 'cloudfund', 'pearl capital'];

            for (const lender of lenders) {
                try {
                    await page.goto('https://direct.sos.state.tx.us/UCC/default.asp');
                    await page.waitForTimeout(2000);

                    // Navigate to search by secured party
                    const spLink = await page.$('a[href*="secured"]') ||
                                   await page.$('a:has-text("Secured Party")');
                    if (spLink) await spLink.click();
                    await page.waitForTimeout(2000);

                    const searchInput = await page.$('input[name="SPName"]') ||
                                        await page.$('input[type="text"]');
                    if (searchInput) {
                        await searchInput.fill(lender);
                        const submit = await page.$('input[type="submit"]') ||
                                        await page.$('input[value="Search"]');
                        if (submit) await submit.click();
                        await page.waitForTimeout(3000);

                        // Extract results
                        const rows = await page.$$eval('table tr', (trs) => {
                            return trs.slice(1).map(tr => {
                                const cells = tr.querySelectorAll('td');
                                if (cells.length >= 4) {
                                    return {
                                        filing_number: cells[0]?.innerText?.trim() || '',
                                        filing_date: cells[1]?.innerText?.trim() || '',
                                        debtor_name: cells[2]?.innerText?.trim() || '',
                                        secured_party: cells[3]?.innerText?.trim() || '',
                                    };
                                }
                                return null;
                            }).filter(Boolean);
                        });

                        results.push(...rows);
                        log.info(`TX: Found ${rows.length} filings for ${lender}`);
                    }
                } catch (e) {
                    log.warning(`TX error for ${lender}: ${e.message}`);
                }
            }

            return results;
        }
        """,
    },
}


# ── Apify actor runner ────────────────────────────────────────

async def run_apify_actor(
    state: str,
    config: dict,
    timeout_secs: int = 300,
) -> list[dict]:
    """
    Run the Apify playwright-scraper actor for a given state.
    Returns a list of raw filing dicts from the page function.
    """
    if not APIFY_API_TOKEN or APIFY_API_TOKEN == "your-apify-token":
        logger.warning(f"APIFY_API_TOKEN not set — skipping {state} Apify scrape")
        return []

    headers = {"Authorization": f"Bearer {APIFY_API_TOKEN}"}

    actor_input = {
        "startUrls": [{"url": u} for u in config["start_urls"]],
        "pageFunction": config["page_function"],
        "proxyConfiguration": {"useApifyProxy": True},
        "maxRequestRetries": 3,
        "requestHandlerTimeoutSecs": 120,
        "maxConcurrency": 1,  # Be gentle on gov sites
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # Start the actor run
        logger.info(f"Starting Apify playwright-scraper for {state} ({config['name']})")
        resp = await client.post(
            f"{APIFY_BASE}/acts/{PLAYWRIGHT_ACTOR}/runs",
            headers=headers,
            json=actor_input,
        )

        if resp.status_code != 201:
            logger.error(f"Failed to start actor for {state}: {resp.status_code} {resp.text}")
            return []

        run_data = resp.json()["data"]
        run_id = run_data["id"]
        dataset_id = run_data["defaultDatasetId"]
        logger.info(f"  Actor run started: {run_id}")

        # Poll for completion
        start_time = time.time()
        while time.time() - start_time < timeout_secs:
            await asyncio.sleep(10)

            status_resp = await client.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                headers=headers,
            )
            status = status_resp.json()["data"]["status"]
            logger.info(f"  {state} run status: {status}")

            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break

        if status != "SUCCEEDED":
            logger.error(f"Actor run for {state} ended with status: {status}")
            return []

        # Fetch results from dataset
        items_resp = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=headers,
            params={"format": "json"},
        )

        if items_resp.status_code != 200:
            logger.error(f"Failed to fetch dataset for {state}: {items_resp.status_code}")
            return []

        items = items_resp.json()
        # Flatten: page function may return arrays nested in items
        results = []
        for item in items:
            if isinstance(item, list):
                results.extend(item)
            elif isinstance(item, dict):
                # If item has a nested array of results
                if "results" in item and isinstance(item["results"], list):
                    results.extend(item["results"])
                else:
                    results.append(item)

        logger.info(f"  {state}: {len(results)} raw filings from Apify")
        return results


# ── Florida FTP fallback ──────────────────────────────────────

async def scrape_florida_ftp() -> list[dict]:
    """Use the existing FTP downloader for Florida."""
    from scripts.download_ucc import download_florida_ucc
    logger.info("Using Florida FTP (free) instead of Apify")
    return download_florida_ucc()


# ── Normalize raw results into filing schema ──────────────────

def normalize_filing(raw: dict, state: str) -> dict | None:
    """
    Normalize a raw scraped filing into the schema expected by
    insert_filing(). Returns None if the filing is invalid.
    """
    filing_number = (raw.get("filing_number") or "").strip()
    debtor_name = (raw.get("debtor_name") or "").strip()
    secured_party = (raw.get("secured_party") or "").strip()

    # Must have at least a filing number and one name
    if not filing_number or (not debtor_name and not secured_party):
        return None

    # Normalize the filing date
    filing_date = (raw.get("filing_date") or "").strip()
    filing_date = _parse_date(filing_date)

    # Try to extract address components
    address = (raw.get("debtor_address") or raw.get("address") or "").strip()
    city = (raw.get("debtor_city") or raw.get("city") or "").strip()
    debtor_state = (raw.get("debtor_state") or state).strip().upper()
    zip_code = (raw.get("debtor_zip") or raw.get("zip") or "").strip()

    secured_party_type = classify_secured_party(secured_party)

    return {
        "filing_number": filing_number,
        "filing_date": filing_date,
        "debtor_name": debtor_name,
        "debtor_address": address,
        "debtor_city": city,
        "debtor_state": debtor_state,
        "debtor_zip": zip_code,
        "secured_party": secured_party,
        "secured_party_type": secured_party_type,
        "source_state": state,
    }


def _parse_date(date_str: str) -> str:
    """Try multiple date formats and return YYYY-MM-DD."""
    if not date_str:
        return ""

    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
        "%Y%m%d",
        "%B %d, %Y",
        "%b %d, %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Try to extract a date-like pattern
    match = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", date_str)
    if match:
        m, d, y = match.groups()
        if len(y) == 2:
            y = f"20{y}"
        try:
            return datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return date_str


# ── Main scrape pipeline ─────────────────────────────────────

async def scrape_state(state: str, dry_run: bool = False, use_direct: bool = False) -> dict:
    """
    Scrape UCC filings for a single state.
    Returns stats dict with counts.
    """
    state = state.upper()
    config = STATE_CONFIGS.get(state)

    if not config:
        logger.warning(f"No scraping config for state: {state}")
        return {"state": state, "raw": 0, "inserted": 0, "skipped": 0, "error": "no config"}

    # Florida: prefer FTP (free, reliable)
    if state == "FL" and config.get("use_ftp"):
        raw_filings = await scrape_florida_ftp()
    else:
        raw_filings = await run_apify_actor(state, config)

    if not raw_filings:
        logger.info(f"  {state}: No filings scraped")
        return {"state": state, "raw": 0, "inserted": 0, "skipped": 0}

    # Normalize and deduplicate
    normalized = []
    seen_numbers = set()
    for raw in raw_filings:
        filing = normalize_filing(raw, state)
        if filing and filing["filing_number"] not in seen_numbers:
            seen_numbers.add(filing["filing_number"])
            normalized.append(filing)

    logger.info(f"  {state}: {len(normalized)} normalized filings (from {len(raw_filings)} raw)")

    if dry_run:
        # Print sample filings
        for f in normalized[:5]:
            logger.info(f"    [DRY RUN] {f['filing_number']} | {f['debtor_name']} | {f['secured_party']} | {f['secured_party_type']}")
        return {"state": state, "raw": len(raw_filings), "normalized": len(normalized), "inserted": 0, "skipped": 0}

    # Insert into database
    conn = get_connection()
    inserted = 0
    skipped = 0

    for filing in normalized:
        result = insert_filing(conn, filing)
        if result:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    logger.info(f"  {state}: {inserted} inserted, {skipped} duplicates/skipped")

    return {
        "state": state,
        "raw": len(raw_filings),
        "normalized": len(normalized),
        "inserted": inserted,
        "skipped": skipped,
    }


async def scrape_all_states(
    states: list[str],
    dry_run: bool = False,
    use_direct: bool = False,
) -> dict:
    """Scrape UCC filings for all specified states."""
    logger.info(f"Starting UCC scrape for states: {', '.join(states)}")
    logger.info(f"Dry run: {dry_run}")

    init_db()

    results = {}
    total_inserted = 0
    total_raw = 0

    for state in states:
        try:
            result = await scrape_state(state, dry_run=dry_run, use_direct=use_direct)
            results[state] = result
            total_inserted += result.get("inserted", 0)
            total_raw += result.get("raw", 0)
        except Exception as e:
            logger.error(f"Error scraping {state}: {e}")
            results[state] = {"state": state, "error": str(e)}

    logger.info(f"\n{'='*60}")
    logger.info(f"UCC Scrape Complete")
    logger.info(f"  States: {', '.join(states)}")
    logger.info(f"  Total raw filings: {total_raw}")
    logger.info(f"  Total inserted: {total_inserted}")
    logger.info(f"{'='*60}")

    return {
        "states": results,
        "total_raw": total_raw,
        "total_inserted": total_inserted,
    }


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape UCC filings via Apify")
    parser.add_argument(
        "--states",
        default=",".join(TARGET_STATES),
        help="Comma-separated state codes (default: from TARGET_STATES)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch filings but don't insert into database",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Use direct HTTP requests instead of Apify",
    )
    args = parser.parse_args()

    states = [s.strip().upper() for s in args.states.split(",")]
    asyncio.run(scrape_all_states(states, dry_run=args.dry_run, use_direct=args.direct))


if __name__ == "__main__":
    main()
