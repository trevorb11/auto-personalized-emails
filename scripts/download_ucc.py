"""
UCC Filing Data Downloader.

Downloads and processes UCC filing data into SQLite.
Supports multiple state sources:
  - Florida Sunbiz (free FTP, daily updates)
  - CSV import for other states (DataToLeads, manual exports, etc.)

Run this before the daily agent (e.g., 30 min before via cron).
"""
import csv
import io
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH, MCA_LENDERS, DATA_DIR, UCC_DATA_DIR
from data.database import init_db, get_connection, insert_filing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def classify_secured_party(name: str) -> str:
    """Classify a secured party as MCA lender, bank, equipment, or other."""
    name_lower = name.lower()
    if any(lender in name_lower for lender in MCA_LENDERS):
        return "MCA_LENDER"
    elif any(term in name_lower for term in ["bank", "credit union", "federal savings"]):
        return "BANK"
    elif any(term in name_lower for term in ["lease", "leasing", "equipment"]):
        return "EQUIPMENT"
    return "OTHER"


def download_florida_ucc() -> list[dict]:
    """
    Download Florida UCC filings from Sunbiz FTP.

    Note: The exact FTP path and CSV format may vary.
    Verify the current structure at ftp.dos.state.fl.us before
    first use. This function provides the framework — you'll need
    to adjust column mappings based on the actual file format.
    """
    import ftplib

    logger.info("Connecting to Florida Sunbiz FTP...")
    filings = []

    try:
        ftp = ftplib.FTP("ftp.dos.state.fl.us", timeout=30)
        ftp.login("", "PubAccess1845!")

        data = io.BytesIO()
        ftp.retrbinary("RETR /UCC/ucc_data.csv", data.write)
        ftp.quit()

        data.seek(0)
        reader = csv.DictReader(io.TextIOWrapper(data, encoding="utf-8", errors="replace"))

        for row in reader:
            filings.append({
                "filing_number": row.get("filing_number", row.get("FILING_NUMBER", "")),
                "filing_date": row.get("filing_date", row.get("FILING_DATE", "")),
                "debtor_name": row.get("debtor_name", row.get("DEBTOR_NAME", "")),
                "debtor_address": row.get("debtor_address", row.get("DEBTOR_ADDRESS", "")),
                "debtor_city": row.get("debtor_city", row.get("DEBTOR_CITY", "")),
                "debtor_state": row.get("debtor_state", row.get("DEBTOR_STATE", "FL")),
                "debtor_zip": row.get("debtor_zip", row.get("DEBTOR_ZIP", "")),
                "secured_party": row.get("secured_party", row.get("SECURED_PARTY", "")),
                "source_state": "FL",
            })

        logger.info(f"Downloaded {len(filings)} Florida filings")

    except ftplib.all_errors as e:
        logger.error(f"Florida FTP download failed: {e}")
    except Exception as e:
        logger.error(f"Florida processing error: {e}")

    return filings


def import_csv_filings(csv_path: Path, source_state: str) -> list[dict]:
    """
    Import UCC filings from a local CSV file.

    Use this for states where you download data manually or from
    paid sources like DataToLeads. Place CSV files in data/ucc/.

    Expected columns (flexible naming):
        filing_number, filing_date, debtor_name, debtor_address,
        debtor_city, debtor_state, debtor_zip, secured_party
    """
    filings = []

    if not csv_path.exists():
        logger.warning(f"CSV file not found: {csv_path}")
        return filings

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Flexible column name matching
            filings.append({
                "filing_number": _get_col(row, ["filing_number", "FILING_NUMBER", "file_number", "number"]),
                "filing_date": _get_col(row, ["filing_date", "FILING_DATE", "date", "file_date"]),
                "debtor_name": _get_col(row, ["debtor_name", "DEBTOR_NAME", "business_name", "name", "debtor"]),
                "debtor_address": _get_col(row, ["debtor_address", "DEBTOR_ADDRESS", "address", "street"]),
                "debtor_city": _get_col(row, ["debtor_city", "DEBTOR_CITY", "city"]),
                "debtor_state": _get_col(row, ["debtor_state", "DEBTOR_STATE", "state"]) or source_state,
                "debtor_zip": _get_col(row, ["debtor_zip", "DEBTOR_ZIP", "zip", "zip_code", "postal"]),
                "secured_party": _get_col(row, ["secured_party", "SECURED_PARTY", "lender", "creditor"]),
                "source_state": source_state,
            })

    logger.info(f"Loaded {len(filings)} filings from {csv_path.name}")
    return filings


def _get_col(row: dict, possible_names: list[str]) -> str:
    """Try multiple column names and return the first match."""
    for name in possible_names:
        if name in row and row[name]:
            return row[name].strip()
    return ""


def process_filings(filings: list[dict]) -> tuple[int, int]:
    """Insert filings into the database. Returns (inserted, skipped)."""
    conn = get_connection()
    inserted = 0
    skipped = 0

    for filing in filings:
        # Classify the secured party
        secured = filing.get("secured_party", "")
        filing["secured_party_type"] = classify_secured_party(secured) if secured else "OTHER"

        result = insert_filing(conn, filing)
        if result:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    return inserted, skipped


def run_daily_download():
    """Run the full daily UCC data download pipeline."""
    logger.info(f"Starting UCC data download: {datetime.now().isoformat()}")

    # Initialize the database if needed
    init_db()

    total_inserted = 0
    total_skipped = 0

    # ── Source 1: Florida FTP (free) ───────────────────────────
    try:
        fl_filings = download_florida_ucc()
        if fl_filings:
            ins, skip = process_filings(fl_filings)
            total_inserted += ins
            total_skipped += skip
            logger.info(f"Florida: {ins} inserted, {skip} skipped/duplicates")
    except Exception as e:
        logger.error(f"Florida download failed: {e}")

    # ── Source 2: Local CSV imports ────────────────────────────
    # Place CSV files in data/ucc/ named like: CA_ucc.csv, NY_ucc.csv
    for csv_file in UCC_DATA_DIR.glob("*.csv"):
        try:
            # Extract state from filename (e.g., "CA_ucc.csv" -> "CA")
            state = csv_file.stem.split("_")[0].upper()
            if len(state) == 2:
                filings = import_csv_filings(csv_file, source_state=state)
                if filings:
                    ins, skip = process_filings(filings)
                    total_inserted += ins
                    total_skipped += skip
                    logger.info(f"{state}: {ins} inserted, {skip} skipped/duplicates")

                # Archive processed file
                archive_dir = UCC_DATA_DIR / "processed"
                archive_dir.mkdir(exist_ok=True)
                archive_name = f"{csv_file.stem}_{datetime.now().strftime('%Y%m%d')}{csv_file.suffix}"
                csv_file.rename(archive_dir / archive_name)
        except Exception as e:
            logger.error(f"CSV import failed for {csv_file.name}: {e}")

    logger.info(
        f"UCC download complete: {total_inserted} new filings, "
        f"{total_skipped} duplicates/skipped"
    )


if __name__ == "__main__":
    run_daily_download()
