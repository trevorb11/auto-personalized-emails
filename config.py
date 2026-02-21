"""
Configuration for MCA Prospecting Agent.
All secrets loaded from environment variables (auto-loaded from .env).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (no-op if file doesn't exist)
load_dotenv(Path(__file__).parent / ".env")

# ── Project paths ──────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
UCC_DATA_DIR = DATA_DIR / "ucc"
ENRICHED_DIR = DATA_DIR / "enriched"
DB_PATH = DATA_DIR / "ucc_filings.db"

# Ensure directories exist
for d in [DATA_DIR, LOGS_DIR, UCC_DATA_DIR, ENRICHED_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── API Keys (loaded from environment) ─────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# GoHighLevel
GHL_API_KEY = os.environ.get("GHL_API_KEY", "")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "")
GHL_BASE_URL = os.environ.get("GHL_BASE_URL", "https://services.leadconnectorhq.com")

# Google (Maps/Places + Custom Search Engine)
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

# Inbound lead enrichment (Hunter.io + Clearbit)
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
CLEARBIT_API_KEY = os.environ.get("CLEARBIT_API_KEY", "")

# Google Ads (for campaign performance data via MCP)
GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
GOOGLE_ADS_CLIENT_ID = os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
GOOGLE_ADS_CLIENT_SECRET = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")
GOOGLE_ADS_REFRESH_TOKEN = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "")
GOOGLE_ADS_CUSTOMER_ID = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "")

# Apify (web scraping — Google Maps business extraction)
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN", "")

# Notifications
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# ── Agent settings ─────────────────────────────────────────────
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "8192"))

# Dry run mode: logs actions without writing to GHL
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# ── Daily caps (safety limits) ─────────────────────────────────
DAILY_GHL_CREATES = int(os.environ.get("DAILY_GHL_CREATES", "100"))
DAILY_GOOGLE_LOOKUPS = int(os.environ.get("DAILY_GOOGLE_LOOKUPS", "200"))
DAILY_CSE_SEARCHES = int(os.environ.get("DAILY_CSE_SEARCHES", "50"))
DAILY_MAPS_DISCOVERY = int(os.environ.get("DAILY_MAPS_DISCOVERY", "100"))
DAILY_HUNTER_LOOKUPS = int(os.environ.get("DAILY_HUNTER_LOOKUPS", "25"))
DAILY_CLEARBIT_LOOKUPS = int(os.environ.get("DAILY_CLEARBIT_LOOKUPS", "50"))
DAILY_CLAUDE_TOKENS = int(os.environ.get("DAILY_CLAUDE_TOKENS", "500000"))

# ── Target states for UCC processing ──────────────────────────
TARGET_STATES = os.environ.get("TARGET_STATES", "FL,CA,NY,TX").split(",")

# ── Known MCA lenders — competitive intelligence ─────────────
# Each lender has a profile with estimated rates, terms, and market
# position. Used by UCC processor to enrich leads with what they're
# currently paying, and by email generator for competitive positioning.
# Update these as you learn real funder appetites from deal flow.
MCA_LENDER_PROFILES = {
    "yellowstone capital": {
        "display_name": "Yellowstone Capital",
        "tier": 1,
        "typical_factor_range": [1.28, 1.48],
        "typical_term_months": [4, 12],
        "typical_daily_payment_pct": 0.07,  # ~7% of advance per month
        "max_positions": 3,
        "min_revenue": 15000,
        "approval_speed_days": 1,
        "sweet_spot": "trucking, construction, established businesses",
        "weakness": "Rates creep up on renewals. Merchants often overpay on 2nd+ position.",
        "positioning": "We consistently beat Yellowstone renewals by 15-20% on factor rate.",
    },
    "credibly": {
        "display_name": "Credibly",
        "tier": 1,
        "typical_factor_range": [1.25, 1.45],
        "typical_term_months": [4, 18],
        "typical_daily_payment_pct": 0.06,
        "max_positions": 2,
        "min_revenue": 15000,
        "approval_speed_days": 1,
        "sweet_spot": "diversified, balanced portfolio, $15K-$250K deals",
        "weakness": "Conservative on stacking. If merchant has 2+ positions they'll pass.",
        "positioning": "Credibly is solid but limited on stacking. We have partners that work with your profile.",
    },
    "rapid finance": {
        "display_name": "Rapid Finance",
        "tier": 1,
        "typical_factor_range": [1.22, 1.42],
        "typical_term_months": [6, 18],
        "typical_daily_payment_pct": 0.06,
        "max_positions": 2,
        "min_revenue": 20000,
        "approval_speed_days": 1,
        "sweet_spot": "established businesses, $50K+ deals, good credit",
        "weakness": "Strict on credit. Sub-550 FICO rarely approved.",
        "positioning": "Rapid Finance gave you a fair deal but their renewal rates don't improve. We can.",
    },
    "kapitus": {
        "display_name": "Kapitus",
        "tier": 1,
        "typical_factor_range": [1.20, 1.40],
        "typical_term_months": [6, 24],
        "typical_daily_payment_pct": 0.055,
        "max_positions": 2,
        "min_revenue": 20000,
        "approval_speed_days": 1,
        "sweet_spot": "larger deals, $75K+, strong revenue, multiple products",
        "weakness": "Slow on underwriting for complex deals. Can take 3-5 days.",
        "positioning": "Kapitus has good rates but limited flexibility. We match the rate with faster funding.",
    },
    "forward financing": {
        "display_name": "Forward Financing",
        "tier": 2,
        "typical_factor_range": [1.30, 1.55],
        "typical_term_months": [3, 12],
        "typical_daily_payment_pct": 0.08,
        "max_positions": 3,
        "min_revenue": 10000,
        "approval_speed_days": 1,
        "sweet_spot": "small businesses, newer companies, high approval rate",
        "weakness": "Higher cost. Merchants often don't realize how much they're paying.",
        "positioning": "Forward Financing gets people funded but at a premium. Let's see if we can cut that cost.",
    },
    "libertas funding": {
        "display_name": "Libertas Funding",
        "tier": 2,
        "typical_factor_range": [1.28, 1.50],
        "typical_term_months": [3, 9],
        "typical_daily_payment_pct": 0.07,
        "max_positions": 3,
        "min_revenue": 10000,
        "approval_speed_days": 1,
        "sweet_spot": "flexible on credit, will stack, fast",
        "weakness": "Shorter terms mean merchant is back looking for funding quickly.",
        "positioning": "Libertas is fast but short terms keep you on the funding treadmill. We can get you longer terms.",
    },
    "fundkite": {
        "display_name": "Fundkite",
        "tier": 2,
        "typical_factor_range": [1.30, 1.55],
        "typical_term_months": [3, 9],
        "typical_daily_payment_pct": 0.08,
        "max_positions": 4,
        "min_revenue": 8000,
        "approval_speed_days": 1,
        "sweet_spot": "will stack up to 4th position, flexible credit",
        "weakness": "High cost. Merchants in 3rd/4th position are paying steep.",
        "positioning": "If you're stacked with Fundkite, consolidation could save you serious money.",
    },
    "fora financial": {
        "display_name": "Fora Financial",
        "tier": 1,
        "typical_factor_range": [1.15, 1.35],
        "typical_term_months": [6, 15],
        "typical_daily_payment_pct": 0.055,
        "max_positions": 1,
        "min_revenue": 25000,
        "approval_speed_days": 2,
        "sweet_spot": "strong profiles, competitive rates, larger deals",
        "weakness": "Won't stack at all. First position only.",
        "positioning": "Fora gave you great terms. When you're ready for more capital, we have options that work alongside.",
    },
    "national funding": {
        "display_name": "National Funding",
        "tier": 2,
        "typical_factor_range": [1.25, 1.50],
        "typical_term_months": [4, 12],
        "typical_daily_payment_pct": 0.07,
        "max_positions": 3,
        "min_revenue": 10000,
        "approval_speed_days": 1,
        "sweet_spot": "broad appetite, $10K-$150K, most industries",
        "weakness": "Mid-tier rates. Not the cheapest, not the most flexible.",
        "positioning": "National Funding is solid middle ground. We often find 10-15% better rates for the same profile.",
    },
    "ondeck": {
        "display_name": "OnDeck",
        "tier": 1,
        "typical_factor_range": [1.15, 1.38],
        "typical_term_months": [6, 24],
        "typical_daily_payment_pct": 0.05,
        "max_positions": 1,
        "min_revenue": 25000,
        "approval_speed_days": 1,
        "sweet_spot": "term loans, strong credit, established businesses",
        "weakness": "Strict requirements. Won't work with merchants below 600 FICO.",
        "positioning": "OnDeck is premium but inflexible. If your needs have changed, we have more options.",
    },
    "bluevine": {
        "display_name": "Bluevine",
        "tier": 1,
        "typical_factor_range": [1.10, 1.30],
        "typical_term_months": [6, 12],
        "typical_daily_payment_pct": 0.05,
        "max_positions": 0,
        "min_revenue": 20000,
        "approval_speed_days": 3,
        "sweet_spot": "lines of credit, strong profiles, tech-forward businesses",
        "weakness": "Very selective. LOC only, no MCA.",
        "positioning": "Bluevine's LOC is great if you qualify. Need more flexibility or larger amount? We can help.",
    },
}

# Flat list of lender names (for backwards compatibility with UCC matching)
MCA_LENDERS = list(MCA_LENDER_PROFILES.keys()) + [
    "cloudfund", "pearl capital", "mantis funding", "greenbox capital",
    "clear finance technology", "unique funding solutions",
    "kabbage", "can capital", "bizfi",
    "strategic funding source", "merchant cash and capital",
    "delta bridge", "king trade", "unique funding",
    "on deck",  # alternate spelling
]

# ── High-value industries for scoring ─────────────────────────
HIGH_VALUE_INDUSTRIES = [
    "trucking", "transportation", "construction", "restaurant",
    "healthcare", "retail", "auto repair", "manufacturing",
    "logistics", "medical", "dental", "plumbing", "hvac",
    "landscaping", "roofing", "electrical",
]

# ═══════════════════════════════════════════════════════════════
# MCP SERVER CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════
# These can be used by any MCP-compatible client (Claude Desktop,
# Claude Code, custom agents via Agent SDK, etc.)

# GoHighLevel — CRM: contacts, conversations, calendars, opportunities
GHL_MCP_CONFIG = {
    "command": "npx",
    "args": [
        "mcp-remote",
        "https://services.leadconnectorhq.com/mcp/",
        "--header",
        f"Authorization: Bearer {GHL_API_KEY}",
        "--header",
        f"locationId: {GHL_LOCATION_ID}",
    ],
}

# Google Ads — Campaign performance, keyword ideas, GAQL queries
# Requires: google-ads-mcp-server npm package
# Setup: https://github.com/google-marketing-solutions/google-ads-mcp-server
GOOGLE_ADS_MCP_CONFIG = {
    "command": "npx",
    "args": ["google-ads-mcp-server"],
    "env": {
        "GOOGLE_ADS_DEVELOPER_TOKEN": GOOGLE_ADS_DEVELOPER_TOKEN,
        "GOOGLE_ADS_CLIENT_ID": GOOGLE_ADS_CLIENT_ID,
        "GOOGLE_ADS_CLIENT_SECRET": GOOGLE_ADS_CLIENT_SECRET,
        "GOOGLE_ADS_REFRESH_TOKEN": GOOGLE_ADS_REFRESH_TOKEN,
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": GOOGLE_ADS_CUSTOMER_ID,
    },
}

# Apify — 3,000+ web scraping actors (Google Maps, Yellow Pages, etc.)
# Key actor: apify/google-maps-scraper for business discovery
# Setup: https://github.com/apify/actors-mcp-server
APIFY_MCP_CONFIG = {
    "command": "npx",
    "args": ["-y", "@anthropic-ai/apify-mcp-server"],
    "env": {
        "APIFY_TOKEN": APIFY_API_TOKEN,
    },
}

# Playwright — Browser automation for scraping business directories
# Setup: https://github.com/microsoft/playwright-mcp
PLAYWRIGHT_MCP_CONFIG = {
    "command": "npx",
    "args": ["@anthropic-ai/playwright-mcp-server"],
}

# All MCP configs in one dict for easy iteration
MCP_SERVERS = {
    "ghl": GHL_MCP_CONFIG,
    "google_ads": GOOGLE_ADS_MCP_CONFIG,
    "apify": APIFY_MCP_CONFIG,
    "playwright": PLAYWRIGHT_MCP_CONFIG,
}
