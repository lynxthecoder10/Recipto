"""Seed the SQLite database with 10 demo gala records.

Run once:  python seed_demo_data.py
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so `models` and `utils` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from contextlib import closing
from models.db import (
    init_receipt_store,
    init_billing_store,
    create_or_update_gala,
    list_galas,
    get_receipts_connection,
)

DEMO_GALAS = [
    # (gala_number, tenant_name, phone_number, monthly_rent)
    ("G001", "Alice",   "8779033522", 600.0),
    ("G002", "Bob",     "8779033522", 600.0),
    ("G003", "Charlie", "8779033522", 600.0),
    ("G004", "David",   "",           600.0),
    ("G005", "Emma",    "",           600.0),
    ("G006", "Frank",   "",           600.0),
    ("G007", "Grace",   "",           600.0),
    ("G008", "Henry",   "",           600.0),
    ("G009", "Ivy",     "",           600.0),
    ("G010", "Jack",    "",           600.0),
]


def main() -> None:
    print("Initialising database schema ...")
    init_receipt_store()
    init_billing_store()

    # Clear existing galas and bills for clean demo seed
    with closing(get_receipts_connection()) as conn:
        conn.execute("DELETE FROM bills")
        conn.execute("DELETE FROM galas")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('galas', 'bills', 'receipts')")
        conn.commit()

    print(f"Seeding {len(DEMO_GALAS)} demo galas ...")
    for gala_number, tenant_name, phone_number, monthly_rent in DEMO_GALAS:
        record = create_or_update_gala(
            gala_number=gala_number,
            tenant_name=tenant_name,
            phone_number=phone_number,
            monthly_rent=monthly_rent,
        )
        phone_display = phone_number if phone_number else "(blank)"
        print(f"  {record['gala_number']:5s}  {record['tenant_name']:10s}  phone={phone_display:12s}  rent=Rs.{record['monthly_rent']:.2f}")

    galas = list_galas()
    print(f"\nDone. Total galas in database: {len(galas)}")


if __name__ == "__main__":
    main()
