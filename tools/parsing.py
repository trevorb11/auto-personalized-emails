"""
Shared parsing / normalization helpers.

One implementation of the fiddly string handling that several tools
need (revenue strings, lender matching, dates, domains, business names),
so they can't drift apart.
"""
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMB])?", re.IGNORECASE)


def parse_money(value) -> float:
    """
    Parse a money value into a float.

    Handles numbers and strings like "50000", "$50K", "$1.5M",
    "$50K-$100K/mo" (takes the low end), "$100K+/mo", "Under $10K/mo" (→ 0).
    Returns 0 when nothing parseable is found.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text or text.lower().startswith("under"):
        return 0.0
    match = _NUMBER_RE.search(text)
    if not match:
        return 0.0
    number = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    return number * _MULTIPLIERS.get(suffix, 1)


def as_number(value, default: float = 0) -> float:
    """Coerce a possibly-None / possibly-string value to a number."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── Lender matching ────────────────────────────────────────────

def _lender_pattern(lender: str) -> re.Pattern:
    # Word boundaries so "can capital" doesn't match "AmeriCAN CAPITAL".
    return re.compile(r"\b" + re.escape(lender.lower()) + r"\b")


def match_lender(secured_party: str, lenders) -> Optional[str]:
    """
    Return the first lender key whose name appears in secured_party as whole
    words, or None. Longer names are tried first so "unique funding solutions"
    wins over "unique funding".
    """
    if not secured_party:
        return None
    text = re.sub(r"[^a-z0-9& ]+", " ", secured_party.lower())
    text = re.sub(r"\s+", " ", text)
    for lender in sorted(lenders, key=len, reverse=True):
        if _lender_pattern(lender).search(text):
            return lender
    return None


# ── Business names ─────────────────────────────────────────────

_ENTITY_SUFFIXES = re.compile(
    r"\b(llc|l l c|inc|incorporated|corp|corporation|co|company|ltd|"
    r"limited|lp|llp|pllc|pc|pa|dba)\b"
)


def normalize_business_name(name: str) -> str:
    """Normalize a business name for matching ("Joe's Trucking, LLC" → "joes trucking")."""
    if not name:
        return ""
    text = name.lower().replace("'", "").replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _ENTITY_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Dates ──────────────────────────────────────────────────────

_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y%m%d",
    "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y",
)


def normalize_date(value: str) -> str:
    """
    Normalize a date string to ISO "YYYY-MM-DD". Filing queries compare
    dates as strings, so anything else (e.g. "03/15/2026") silently breaks
    the renewal-window math. Returns "" if unparseable.
    """
    if not value:
        return ""
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d")
    except ValueError:
        pass
    text = text.split(" ")[0] if re.match(r"^\d", text) else text
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


# ── Domains ────────────────────────────────────────────────────

# Search results on these are listings *about* businesses, not the
# businesses themselves. Enriching them burns Hunter/Clearbit credits on
# Yelp's own staff, so they're skipped.
DIRECTORY_DOMAINS = {
    "yelp.com", "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "youtube.com", "tiktok.com", "pinterest.com", "reddit.com",
    "bbb.org", "yellowpages.com", "manta.com", "mapquest.com", "angi.com",
    "angieslist.com", "homeadvisor.com", "thumbtack.com", "houzz.com",
    "indeed.com", "glassdoor.com", "ziprecruiter.com", "craigslist.org",
    "google.com", "apple.com", "tripadvisor.com", "doordash.com",
    "ubereats.com", "grubhub.com", "opentable.com", "nextdoor.com",
    "wikipedia.org", "bizapedia.com", "opencorporates.com", "dnb.com",
    "zoominfo.com", "crunchbase.com", "chamberofcommerce.com",
    "superpages.com", "citysearch.com", "foursquare.com", "porch.com",
    "buildzoom.com", "safer.fmcsa.dot.gov", "fmcsa.dot.gov", "carriersource.io",
    "healthgrades.com", "zocdoc.com", "vitals.com", "webmd.com",
}


def normalize_domain(url_or_domain: str) -> str:
    """'https://www.Example.com/path' → 'example.com'."""
    if not url_or_domain:
        return ""
    text = url_or_domain.strip().lower()
    if "://" not in text:
        text = "http://" + text
    host = urlparse(text).netloc.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_directory_domain(domain: str) -> bool:
    """True if domain is (a subdomain of) a known directory / social site."""
    domain = normalize_domain(domain)
    if not domain or domain.endswith(".gov"):
        return bool(domain)
    return any(domain == d or domain.endswith("." + d) for d in DIRECTORY_DOMAINS)
