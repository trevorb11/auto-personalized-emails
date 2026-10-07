"""
Daily API budget enforcement.

The DAILY_* caps in config.py are enforced here, with counts persisted in
the api_usage table so re-running the pipeline the same day (or a crash
and restart) can't blow through a paid quota.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from data.database import get_connection, get_api_usage, record_api_usage

logger = logging.getLogger(__name__)


def _caps() -> dict:
    return {
        "google_places": config.DAILY_GOOGLE_LOOKUPS,
        "google_cse": config.DAILY_CSE_SEARCHES,
        "hunter": config.DAILY_HUNTER_LOOKUPS,
        "clearbit": config.DAILY_CLEARBIT_LOOKUPS,
        "ghl_create": config.DAILY_GHL_CREATES,
        "claude_tokens": config.DAILY_CLAUDE_TOKENS,
    }


def remaining(api: str) -> int:
    """Units left today for an API (unlimited APIs return a large number)."""
    cap = _caps().get(api)
    if cap is None:
        return 10**9
    conn = get_connection()
    try:
        return max(0, cap - get_api_usage(conn, api))
    finally:
        conn.close()


def try_consume(api: str, units: int = 1) -> bool:
    """
    Reserve `units` of today's budget for `api`. Returns False (and logs
    once per call) if the cap would be exceeded — the caller should skip
    the request.
    """
    if remaining(api) < units:
        logger.warning(f"Daily cap reached for {api}; skipping call")
        return False
    record(api, units)
    return True


def record(api: str, units: int) -> None:
    """Record usage that was measured after the fact (e.g. Claude tokens)."""
    conn = get_connection()
    try:
        record_api_usage(conn, api, units)
    finally:
        conn.close()
