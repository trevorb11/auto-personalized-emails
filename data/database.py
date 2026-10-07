"""
SQLite database layer for MCA lead tracking.

Handles UCC filings storage, lead scoring history,
enrichment data, GHL export tracking, inbound discovery
leads, GHL sync snapshots, and opportunity tracking.
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import DB_PATH
from tools.parsing import normalize_business_name, normalize_date

logger = logging.getLogger(__name__)


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize all database tables."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS filings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_number TEXT UNIQUE,
            filing_date TEXT,
            debtor_name TEXT,
            debtor_address TEXT,
            debtor_city TEXT,
            debtor_state TEXT,
            debtor_zip TEXT,
            secured_party TEXT,
            secured_party_type TEXT,
            source_state TEXT,
            processed_date TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_filings_state
            ON filings(debtor_state);
        CREATE INDEX IF NOT EXISTS idx_filings_date
            ON filings(filing_date);
        CREATE INDEX IF NOT EXISTS idx_filings_type
            ON filings(secured_party_type);

        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_id INTEGER REFERENCES filings(id),
            source TEXT DEFAULT 'ucc',
            business_name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            website TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            zip_code TEXT,
            industry TEXT,
            monthly_revenue TEXT,
            years_in_business REAL,
            employee_count INTEGER,
            google_rating REAL,
            google_review_count INTEGER,
            lead_score INTEGER DEFAULT 0,
            lead_tier TEXT DEFAULT 'D',
            score_reasons TEXT,
            business_focus TEXT,
            personalized_message TEXT,
            estimated_funding_amount TEXT,
            ucc_filing_age_months REAL,
            has_existing_mca BOOLEAN DEFAULT 0,
            domain TEXT,
            description TEXT,
            linkedin TEXT,
            contact_position TEXT,
            email_confidence INTEGER,
            clearbit_data TEXT,
            ghl_contact_id TEXT,
            exported_to_ghl BOOLEAN DEFAULT 0,
            exported_at TEXT,
            batch_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_leads_tier
            ON leads(lead_tier);
        CREATE INDEX IF NOT EXISTS idx_leads_score
            ON leads(lead_score);
        CREATE INDEX IF NOT EXISTS idx_leads_exported
            ON leads(exported_to_ghl);
        CREATE INDEX IF NOT EXISTS idx_leads_batch
            ON leads(batch_date);
        CREATE INDEX IF NOT EXISTS idx_leads_source
            ON leads(source);
        CREATE INDEX IF NOT EXISTS idx_leads_domain
            ON leads(domain);

        -- GHL sync snapshots: stores periodic CRM state pulls
        CREATE TABLE IF NOT EXISTS ghl_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_contacts INTEGER DEFAULT 0,
            new_contacts_24h INTEGER DEFAULT 0,
            open_opportunities INTEGER DEFAULT 0,
            pipeline_summary TEXT,
            recent_conversations INTEGER DEFAULT 0,
            upcoming_appointments INTEGER DEFAULT 0,
            raw_data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- GHL opportunities tracked locally for pipeline analytics
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ghl_opportunity_id TEXT UNIQUE,
            ghl_contact_id TEXT,
            pipeline_name TEXT,
            stage_name TEXT,
            name TEXT,
            monetary_value REAL DEFAULT 0,
            status TEXT DEFAULT 'open',
            source TEXT,
            last_synced TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_opps_status
            ON opportunities(status);
        CREATE INDEX IF NOT EXISTS idx_opps_contact
            ON opportunities(ghl_contact_id);

        -- Inbound discovery tracking: prevents re-discovering same domains
        CREATE TABLE IF NOT EXISTS discovered_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL,
            business_name TEXT,
            industry TEXT,
            city TEXT,
            state TEXT,
            first_seen TEXT DEFAULT (datetime('now')),
            enriched BOOLEAN DEFAULT 0,
            lead_id INTEGER REFERENCES leads(id)
        );

        CREATE INDEX IF NOT EXISTS idx_domains_domain
            ON discovered_domains(domain);

        CREATE TABLE IF NOT EXISTS daily_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            started_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            total_filings_processed INTEGER DEFAULT 0,
            total_leads_scored INTEGER DEFAULT 0,
            tier_a_count INTEGER DEFAULT 0,
            tier_b_count INTEGER DEFAULT 0,
            tier_c_count INTEGER DEFAULT 0,
            tier_d_count INTEGER DEFAULT 0,
            contacts_created_ghl INTEGER DEFAULT 0,
            emails_drafted INTEGER DEFAULT 0,
            inbound_discovered INTEGER DEFAULT 0,
            inbound_enriched INTEGER DEFAULT 0,
            ghl_snapshot_id INTEGER,
            errors TEXT,
            status TEXT DEFAULT 'running'
        );

        -- Per-day API call counters so daily caps survive restarts
        CREATE TABLE IF NOT EXISTS api_usage (
            usage_date TEXT NOT NULL,
            api TEXT NOT NULL,
            calls INTEGER DEFAULT 0,
            PRIMARY KEY (usage_date, api)
        );
    """)

    _migrate_columns(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_filings_debtor_norm "
        "ON filings(debtor_name_normalized)"
    )
    conn.commit()
    conn.close()


# Columns added after the original schema. init_db() adds any that are
# missing so existing databases upgrade in place.
_ADDED_COLUMNS = {
    "filings": {
        "debtor_name_normalized": "TEXT",
    },
    "leads": {
        "secured_party": "TEXT",
        "funder_display_name": "TEXT",
        "funder_tier": "INTEGER",
        "urgency_level": "TEXT",
        "position_count": "INTEGER",
        "estimated_daily_payment": "REAL",
        "estimated_monthly_obligation": "REAL",
        "first_name": "TEXT",
        "last_name": "TEXT",
        "email_subject": "TEXT",
        "email_body": "TEXT",
        "sms_body": "TEXT",
        "sales_notes": "TEXT",
        "best_funder_match": "TEXT",
    },
}


def _migrate_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, col_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    # Backfill normalized debtor names for rows created before the column existed
    rows = conn.execute(
        "SELECT id, debtor_name FROM filings WHERE debtor_name_normalized IS NULL"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE filings SET debtor_name_normalized = ? WHERE id = ?",
            (normalize_business_name(row["debtor_name"] or ""), row["id"]),
        )


def insert_filing(conn: sqlite3.Connection, filing: dict) -> Optional[int]:
    """Insert a UCC filing record. Returns row ID or None if duplicate."""
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO filings
            (filing_number, filing_date, debtor_name, debtor_name_normalized,
             debtor_address, debtor_city, debtor_state, debtor_zip,
             secured_party, secured_party_type, source_state, processed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            filing.get("filing_number"),
            normalize_date(filing.get("filing_date", "")) or filing.get("filing_date"),
            filing.get("debtor_name"),
            normalize_business_name(filing.get("debtor_name", "")),
            filing.get("debtor_address"),
            filing.get("debtor_city"),
            filing.get("debtor_state"),
            filing.get("debtor_zip"),
            filing.get("secured_party"),
            filing.get("secured_party_type"),
            filing.get("source_state"),
            datetime.now().isoformat(),
        ))
        return cursor.lastrowid if cursor.rowcount > 0 else None
    except sqlite3.Error:
        return None


_LEAD_COLUMNS = [
    "filing_id", "source", "business_name", "phone", "email", "website",
    "address", "city", "state", "zip_code", "industry", "monthly_revenue",
    "years_in_business", "employee_count", "google_rating",
    "google_review_count", "lead_score", "lead_tier", "score_reasons",
    "business_focus", "personalized_message", "estimated_funding_amount",
    "ucc_filing_age_months", "has_existing_mca", "domain", "description",
    "linkedin", "contact_position", "email_confidence", "batch_date",
    "secured_party", "funder_display_name", "funder_tier", "urgency_level",
    "position_count", "estimated_daily_payment", "estimated_monthly_obligation",
    "first_name", "last_name", "email_subject", "email_body", "sms_body",
    "sales_notes", "best_funder_match",
]


def insert_lead(conn: sqlite3.Connection, lead: dict) -> Optional[int]:
    """Insert a scored lead record."""
    values = []
    for col in _LEAD_COLUMNS:
        value = lead.get(col)
        if col == "source" and not value:
            value = "ucc"
        if col == "urgency_level" and value is None:
            value = (lead.get("urgency") or {}).get("level")
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        values.append(value)
    placeholders = ", ".join("?" for _ in _LEAD_COLUMNS)
    try:
        cursor = conn.execute(
            f"INSERT INTO leads ({', '.join(_LEAD_COLUMNS)}) VALUES ({placeholders})",
            values,
        )
        return cursor.lastrowid
    except sqlite3.Error as e:
        logger.error(
            f"insert_lead failed for {lead.get('business_name')}: {e}"
        )
        return None


def get_unprocessed_filings(
    conn: sqlite3.Connection,
    state: str,
    min_age_months: float = 0,
    max_age_months: float = 15,
    secured_party_type: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    Get filings whose filing_date falls in [max_age_months ago, min_age_months ago]
    and that haven't been converted to leads yet.

    The window is on filing *age* because that's what predicts renewal
    timing: a filing from ~6-12 months ago is a merchant whose advance is
    maturing. Each filing becomes a lead once, so the window slides forward
    day by day and picks up filings as they age into it.
    """
    now = datetime.now()
    newest = (now - timedelta(days=int(min_age_months * 30))).strftime("%Y-%m-%d")
    oldest = (now - timedelta(days=int(max_age_months * 30))).strftime("%Y-%m-%d")
    query = """
        SELECT f.* FROM filings f
        LEFT JOIN leads l ON l.filing_id = f.id
        WHERE f.debtor_state = ?
          AND f.filing_date >= ?
          AND f.filing_date <= ?
          AND l.id IS NULL
    """
    params: list = [state, oldest, newest]

    if secured_party_type:
        query += " AND f.secured_party_type = ?"
        params.append(secured_party_type)

    # MCA filings first, then closest to the 8-10 month sweet spot
    query += """
        ORDER BY (f.secured_party_type = 'MCA_LENDER') DESC,
                 ABS(julianday('now') - julianday(f.filing_date) - 270) ASC
        LIMIT ?
    """
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_debtor_filing_history(
    conn: sqlite3.Connection,
    debtor_name_normalized: str,
    state: str,
    since_months: float = 18,
) -> list[dict]:
    """All filings for a debtor (by normalized name) in a state within the lookback."""
    if not debtor_name_normalized:
        return []
    cutoff = (datetime.now() - timedelta(days=int(since_months * 30))).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT * FROM filings
        WHERE debtor_name_normalized = ? AND debtor_state = ? AND filing_date >= ?
        ORDER BY filing_date DESC
    """, (debtor_name_normalized, state, cutoff)).fetchall()
    return [dict(row) for row in rows]


def get_leads_by_tier(
    conn: sqlite3.Connection,
    tier: str,
    batch_date: Optional[str] = None,
    exported: Optional[bool] = None,
    limit: int = 50,
) -> list[dict]:
    """Get leads filtered by tier and optionally by batch/export status."""
    query = "SELECT * FROM leads WHERE lead_tier = ?"
    params: list = [tier]

    if batch_date:
        query += " AND batch_date = ?"
        params.append(batch_date)
    if exported is not None:
        query += " AND exported_to_ghl = ?"
        params.append(exported)

    query += " ORDER BY lead_score DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def mark_lead_exported(
    conn: sqlite3.Connection, lead_id: int, ghl_contact_id: str
) -> None:
    """Mark a lead as exported to GHL."""
    conn.execute("""
        UPDATE leads
        SET exported_to_ghl = 1,
            exported_at = ?,
            ghl_contact_id = ?,
            updated_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), ghl_contact_id,
          datetime.now().isoformat(), lead_id))


def get_daily_stats(conn: sqlite3.Connection, batch_date: str) -> dict:
    """Get statistics for a given batch/run date."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total_leads,
            SUM(CASE WHEN lead_tier = 'A' THEN 1 ELSE 0 END) as tier_a,
            SUM(CASE WHEN lead_tier = 'B' THEN 1 ELSE 0 END) as tier_b,
            SUM(CASE WHEN lead_tier = 'C' THEN 1 ELSE 0 END) as tier_c,
            SUM(CASE WHEN lead_tier = 'D' THEN 1 ELSE 0 END) as tier_d,
            SUM(CASE WHEN exported_to_ghl = 1 THEN 1 ELSE 0 END) as exported,
            AVG(lead_score) as avg_score
        FROM leads
        WHERE batch_date = ?
    """, (batch_date,)).fetchone()

    return dict(row) if row else {}


def start_daily_run(conn: sqlite3.Connection, run_date: str) -> int:
    """Record the start of a daily agent run."""
    cursor = conn.execute(
        "INSERT INTO daily_runs (run_date) VALUES (?)", (run_date,)
    )
    conn.commit()
    return cursor.lastrowid


def complete_daily_run(conn: sqlite3.Connection, run_id: int, stats: dict) -> None:
    """Record the completion of a daily agent run."""
    conn.execute("""
        UPDATE daily_runs
        SET completed_at = ?,
            total_filings_processed = ?,
            total_leads_scored = ?,
            tier_a_count = ?,
            tier_b_count = ?,
            tier_c_count = ?,
            tier_d_count = ?,
            contacts_created_ghl = ?,
            emails_drafted = ?,
            inbound_discovered = ?,
            inbound_enriched = ?,
            ghl_snapshot_id = ?,
            errors = ?,
            status = 'completed'
        WHERE id = ?
    """, (
        datetime.now().isoformat(),
        stats.get("total_filings", 0),
        stats.get("total_leads", 0),
        stats.get("tier_a", 0),
        stats.get("tier_b", 0),
        stats.get("tier_c", 0),
        stats.get("tier_d", 0),
        stats.get("contacts_created", 0),
        stats.get("emails_drafted", 0),
        stats.get("inbound_discovered", 0),
        stats.get("inbound_enriched", 0),
        stats.get("ghl_snapshot_id"),
        stats.get("errors"),
        run_id,
    ))
    conn.commit()


# ═══════════════════════════════════════════════════════════════
# INBOUND DISCOVERY TRACKING
# ═══════════════════════════════════════════════════════════════

def is_domain_known(conn: sqlite3.Connection, domain: str) -> bool:
    """Check if a domain has already been discovered."""
    row = conn.execute(
        "SELECT 1 FROM discovered_domains WHERE domain = ?", (domain,)
    ).fetchone()
    return row is not None


def record_domain(
    conn: sqlite3.Connection,
    domain: str,
    business_name: str = "",
    industry: str = "",
    city: str = "",
    state: str = "",
    lead_id: Optional[int] = None,
) -> None:
    """Record a discovered domain to prevent re-processing."""
    conn.execute("""
        INSERT OR IGNORE INTO discovered_domains
        (domain, business_name, industry, city, state, lead_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (domain, business_name, industry, city, state, lead_id))


def get_undiscovered_count(conn: sqlite3.Connection) -> int:
    """Get count of domains not yet enriched."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM discovered_domains WHERE enriched = 0"
    ).fetchone()
    return row["cnt"] if row else 0


# ═══════════════════════════════════════════════════════════════
# GHL SNAPSHOTS
# ═══════════════════════════════════════════════════════════════

def save_ghl_snapshot(conn: sqlite3.Connection, snapshot: dict) -> int:
    """Save a GHL inbound snapshot to the database."""
    cursor = conn.execute("""
        INSERT INTO ghl_snapshots
        (snapshot_date, total_contacts, new_contacts_24h,
         open_opportunities, pipeline_summary,
         recent_conversations, upcoming_appointments, raw_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d"),
        len(snapshot.get("new_contacts", [])),
        len(snapshot.get("new_contacts", [])),
        len(snapshot.get("open_opportunities", [])),
        json.dumps([
            {"name": p.get("name"), "stages": len(p.get("stages", []))}
            for p in snapshot.get("pipelines", [])
        ]),
        len(snapshot.get("recent_conversations", [])),
        len(snapshot.get("upcoming_appointments", [])),
        json.dumps(snapshot, default=str),
    ))
    conn.commit()
    return cursor.lastrowid


def sync_opportunities(conn: sqlite3.Connection, opportunities: list[dict]) -> int:
    """Sync opportunities from GHL snapshot into local tracking table."""
    synced = 0
    for opp in opportunities:
        opp_id = opp.get("id", "")
        if not opp_id:
            continue
        conn.execute("""
            INSERT INTO opportunities
            (ghl_opportunity_id, ghl_contact_id, pipeline_name,
             stage_name, name, monetary_value, status, source, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ghl_opportunity_id) DO UPDATE SET
                stage_name = excluded.stage_name,
                monetary_value = excluded.monetary_value,
                status = excluded.status,
                last_synced = excluded.last_synced
        """, (
            opp_id,
            opp.get("contact", {}).get("id", "") if isinstance(opp.get("contact"), dict) else opp.get("contactId", ""),
            opp.get("_pipeline_name", ""),
            opp.get("pipelineStageId", ""),
            opp.get("name", ""),
            opp.get("monetaryValue", 0),
            opp.get("status", "open"),
            opp.get("source", ""),
            datetime.now().isoformat(),
        ))
        synced += 1
    conn.commit()
    return synced


# ═══════════════════════════════════════════════════════════════
# API USAGE / DAILY CAPS
# ═══════════════════════════════════════════════════════════════

def get_api_usage(conn: sqlite3.Connection, api: str, usage_date: Optional[str] = None) -> int:
    """Calls recorded today (or on usage_date) for an API."""
    usage_date = usage_date or datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT calls FROM api_usage WHERE usage_date = ? AND api = ?",
        (usage_date, api),
    ).fetchone()
    return row["calls"] if row else 0


def record_api_usage(conn: sqlite3.Connection, api: str, calls: int = 1) -> None:
    """Increment today's call counter for an API."""
    conn.execute("""
        INSERT INTO api_usage (usage_date, api, calls) VALUES (?, ?, ?)
        ON CONFLICT(usage_date, api) DO UPDATE SET calls = calls + excluded.calls
    """, (datetime.now().strftime("%Y-%m-%d"), api, calls))
    conn.commit()
