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
GHL_API_KEY = os.environ.get("GHL_API_KEY", "")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "")
GHL_BASE_URL = os.environ.get("GHL_BASE_URL", "https://services.leadconnectorhq.com")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# ── Agent settings ─────────────────────────────────────────────
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "8192"))

# Dry run mode: logs actions without writing to GHL
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# ── Daily caps (safety limits) ─────────────────────────────────
DAILY_GHL_CREATES = int(os.environ.get("DAILY_GHL_CREATES", "100"))
DAILY_GOOGLE_LOOKUPS = int(os.environ.get("DAILY_GOOGLE_LOOKUPS", "200"))
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

# ── GHL MCP server configuration ──────────────────────────────
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
