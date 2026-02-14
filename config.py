"""
Configuration for MCA Prospecting Agent.
All secrets loaded from environment variables.
"""
import os
from pathlib import Path

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
DAILY_HUNTER_LOOKUPS = int(os.environ.get("DAILY_HUNTER_LOOKUPS", "25"))
DAILY_CLEARBIT_LOOKUPS = int(os.environ.get("DAILY_CLEARBIT_LOOKUPS", "50"))
DAILY_CLAUDE_TOKENS = int(os.environ.get("DAILY_CLAUDE_TOKENS", "500000"))

# ── Target states for UCC processing ──────────────────────────
TARGET_STATES = os.environ.get("TARGET_STATES", "FL,CA,NY,TX").split(",")

# ── Known MCA lenders for UCC classification ──────────────────
MCA_LENDERS = [
    "yellowstone capital", "credibly", "rapid finance", "kapitus",
    "forward financing", "libertas funding", "fundkite", "cloudfund",
    "pearl capital", "mantis funding", "greenbox capital",
    "clear finance technology", "unique funding solutions",
    "fora financial", "national funding", "on deck", "ondeck",
    "kabbage", "bluevine", "can capital", "bizfi",
    "strategic funding source", "merchant cash and capital",
    "delta bridge", "king trade", "unique funding",
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
