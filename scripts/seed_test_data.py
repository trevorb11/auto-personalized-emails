"""
Seed the database with realistic sample UCC filings for testing.

Use this to verify the full pipeline works before connecting
to real data sources (Florida FTP, Apify scraping, etc.).

Usage:
    python scripts/seed_test_data.py
    python scripts/seed_test_data.py --count 50
"""
import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import MCA_LENDERS, MCA_LENDER_PROFILES
from data.database import init_db, get_connection, insert_filing
from scripts.download_ucc import classify_secured_party

# Realistic Florida business names by industry
BUSINESS_NAMES = {
    "trucking": [
        "SUNSHINE STATE TRUCKING LLC", "MIAMI FREIGHT SOLUTIONS INC",
        "PALM LOGISTICS GROUP LLC", "COASTAL TRANSPORT SERVICES INC",
        "GULF COAST HAULING LLC", "SOUTHEAST CARRIERS INC",
        "FLORIDA EXPRESS LOGISTICS LLC", "ATLANTIC FREIGHT LINES INC",
        "TAMPA BAY TRUCKING CO LLC", "EVERGLADES TRANSPORT LLC",
    ],
    "construction": [
        "PREMIER BUILDERS OF FLORIDA LLC", "SUNSHINE CONSTRUCTION GROUP INC",
        "MIAMI DADE GENERAL CONTRACTORS LLC", "COASTAL CONCRETE SOLUTIONS INC",
        "GULF SHORE BUILDERS LLC", "TROPICAL ROOFING AND CONSTRUCTION INC",
        "PALM BEACH RENOVATIONS LLC", "ORLANDO COMMERCIAL BUILDERS INC",
        "JACKSONVILLE SITE WORK LLC", "TAMPA STEEL ERECTORS INC",
    ],
    "restaurant": [
        "SABOR LATINO RESTAURANT GROUP LLC", "BEACHSIDE GRILL AND BAR INC",
        "LITTLE HAVANA CUISINE LLC", "OCEAN DRIVE DINING INC",
        "BRICKELL BISTRO GROUP LLC", "SOUTH BEACH EATS INC",
        "FLORIDA FRESH KITCHEN LLC", "CORAL GABLES CATERING CO INC",
        "TAMPA TAQUERIA GROUP LLC", "ORLANDO SMOKEHOUSE BBQ LLC",
    ],
    "auto_repair": [
        "PRECISION AUTO CARE OF MIAMI LLC", "SUNSHINE STATE COLLISION CENTER INC",
        "COASTAL AUTOMOTIVE REPAIR LLC", "PALM BEACH AUTO SERVICE INC",
        "GULF COAST TRANSMISSION SPECIALISTS LLC", "FLORIDA FLEET MAINTENANCE INC",
        "JACKSONVILLE BRAKE AND TIRE LLC", "TAMPA AUTO ELECTRIC INC",
        "ORLANDO PERFORMANCE MOTORS LLC", "BROWARD BODY SHOP INC",
    ],
    "medical": [
        "SOUTH FLORIDA URGENT CARE LLC", "MIAMI SPINE AND WELLNESS CENTER INC",
        "PALM BEACH FAMILY MEDICINE LLC", "COASTAL DENTAL ASSOCIATES INC",
        "SUNSHINE PHYSICAL THERAPY GROUP LLC", "GULF COAST IMAGING CENTER INC",
        "FLORIDA PAIN MANAGEMENT SPECIALISTS LLC", "TAMPA BAY DERMATOLOGY INC",
        "ORLANDO ORTHOPEDIC GROUP LLC", "JACKSONVILLE WOMENS HEALTH CENTER INC",
    ],
}

FL_CITIES = [
    ("Miami", "33101"), ("Tampa", "33601"), ("Orlando", "32801"),
    ("Jacksonville", "32099"), ("Fort Lauderdale", "33301"),
    ("West Palm Beach", "33401"), ("St Petersburg", "33701"),
    ("Hialeah", "33010"), ("Coral Gables", "33134"),
    ("Boca Raton", "33431"), ("Pembroke Pines", "33024"),
    ("Hollywood", "33019"), ("Doral", "33166"),
    ("Homestead", "33030"), ("Kissimmee", "34741"),
]

FL_STREETS = [
    "123 NW 7th Ave", "456 Biscayne Blvd", "789 Collins Ave",
    "1010 Brickell Ave", "2200 NW 36th St", "3300 S Dixie Hwy",
    "4500 W Flagler St", "5600 Bird Rd", "6700 Coral Way",
    "8900 NW 25th St", "1234 Federal Hwy", "5678 University Dr",
    "910 Palm Ave", "1100 Main St", "2345 Industrial Blvd",
]

# Known MCA lender names (as they appear on UCC filings)
FILING_SECURED_PARTIES = [
    "YELLOWSTONE CAPITAL LLC",
    "CREDIBLY INC",
    "RAPID FINANCE INC",
    "KAPITUS LLC",
    "FORWARD FINANCING LLC",
    "LIBERTAS FUNDING LLC",
    "FUNDKITE LLC",
    "FORA FINANCIAL BUSINESS LOANS LLC",
    "NATIONAL FUNDING INC",
    "ONDECK CAPITAL INC",
    "CLOUDFUND LLC",
    "PEARL CAPITAL BUSINESS FUNDING LLC",
    "MANTIS FUNDING LLC",
    "GREENBOX CAPITAL INC",
    # Also include some non-MCA to be realistic
    "WELLS FARGO BANK NA",
    "JPMORGAN CHASE BANK NA",
    "BANK OF AMERICA NA",
    "DE LAGE LANDEN FINANCIAL SERVICES",  # equipment
    "CIT FINANCE LLC",  # equipment
    "NAVITAS LEASE FINANCE RECEIVABLES LLC",  # equipment
]


def generate_filing(filing_num: int, months_back_range: tuple = (1, 18)) -> dict:
    """Generate a single realistic UCC filing."""
    # Pick a random industry and business
    industry = random.choice(list(BUSINESS_NAMES.keys()))
    business = random.choice(BUSINESS_NAMES[industry])

    # Random city
    city, zip_code = random.choice(FL_CITIES)
    street = random.choice(FL_STREETS)

    # Random secured party (weighted toward MCA lenders)
    # 70% MCA, 30% non-MCA
    if random.random() < 0.70:
        secured = random.choice(FILING_SECURED_PARTIES[:14])  # MCA lenders
    else:
        secured = random.choice(FILING_SECURED_PARTIES[14:])  # Banks/equipment

    # Filing date: random within range
    min_days = months_back_range[0] * 30
    max_days = months_back_range[1] * 30
    days_ago = random.randint(min_days, max_days)
    filing_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    # Florida filing numbers look like: 202600012345
    year = datetime.now().year
    filing_number = f"{year}{filing_num:08d}"

    return {
        "filing_number": filing_number,
        "filing_date": filing_date,
        "debtor_name": business,
        "debtor_address": street,
        "debtor_city": city,
        "debtor_state": "FL",
        "debtor_zip": zip_code,
        "secured_party": secured,
        "secured_party_type": classify_secured_party(secured),
        "source_state": "FL",
    }


def seed_database(count: int = 100):
    """Generate and insert sample UCC filings."""
    init_db()
    conn = get_connection()

    print(f"Generating {count} sample UCC filings...")

    inserted = 0
    skipped = 0

    # Generate some stacked merchants (same business, multiple funders)
    stacked_businesses = random.sample(
        [b for names in BUSINESS_NAMES.values() for b in names],
        min(count // 10, 10),
    )

    filing_num = random.randint(10000, 99999)

    for i in range(count):
        filing = generate_filing(filing_num + i)

        # 10% chance: make this a stacked merchant
        if random.random() < 0.10 and stacked_businesses:
            filing["debtor_name"] = random.choice(stacked_businesses)

        result = insert_filing(conn, filing)
        if result:
            inserted += 1
        else:
            skipped += 1

    conn.commit()

    # Print summary
    total = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    mca = conn.execute(
        "SELECT COUNT(*) FROM filings WHERE secured_party_type = 'MCA_LENDER'"
    ).fetchone()[0]
    bank = conn.execute(
        "SELECT COUNT(*) FROM filings WHERE secured_party_type = 'BANK'"
    ).fetchone()[0]

    # Count filings in renewal window (6-12 months old)
    cutoff_6mo = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    cutoff_12mo = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    renewal = conn.execute(
        "SELECT COUNT(*) FROM filings WHERE filing_date BETWEEN ? AND ? AND secured_party_type = 'MCA_LENDER'",
        (cutoff_12mo, cutoff_6mo),
    ).fetchone()[0]

    conn.close()

    print(f"\nSeed complete:")
    print(f"  Inserted: {inserted}")
    print(f"  Skipped (duplicates): {skipped}")
    print(f"\nDatabase totals:")
    print(f"  Total filings: {total}")
    print(f"  MCA lender filings: {mca}")
    print(f"  Bank/other filings: {bank}")
    print(f"  MCA in renewal window (6-12mo): {renewal}")
    print(f"\nYou can now run: python main_agent.py --dry-run --states FL")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed test UCC data")
    parser.add_argument("--count", type=int, default=100, help="Number of filings to generate")
    args = parser.parse_args()
    seed_database(args.count)
