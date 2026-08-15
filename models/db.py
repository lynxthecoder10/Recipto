"""SQLite persistence helpers for receipt records."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.bill_logic import (
    generate_next_bill_number,
    get_cycle_months,
    get_cycles_between,
    get_cycle_code,
    format_period_label,
    calculate_months_count,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "database.db"
RECEIPTS_DATABASE_PATH = PROJECT_ROOT / "receipts.db"


class ReceiptStorageError(RuntimeError):
    """Raised when a structured receipt record cannot be stored safely."""


class BillingStorageError(RuntimeError):
    """Raised when gala or billing records cannot be stored safely."""


class BillingDuplicateError(BillingStorageError):
    """Raised before a batch would create a duplicate gala billing cycle."""


def get_connection() -> sqlite3.Connection:
    """Return a connection configured for named-column access and foreign keys."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    """Create the receipts table when the application starts for the first time."""
    with closing(get_connection()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_name TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                amount REAL NOT NULL CHECK (amount > 0),
                date TEXT NOT NULL,
                bill_number INTEGER NOT NULL UNIQUE
            )
            """
        )
        connection.commit()


def get_receipts_connection() -> sqlite3.Connection:
    """Return a connection to the structured receipt-number storage database."""
    connection = sqlite3.connect(RECEIPTS_DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def init_receipt_store() -> None:
    """Create the receipts.db schema used by downloadable rent receipts."""
    with closing(get_receipts_connection()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_no TEXT NOT NULL UNIQUE,
                tenant_name TEXT NOT NULL,
                amount REAL NOT NULL CHECK (amount > 0),
                house_no TEXT NOT NULL,
                month TEXT NOT NULL,
                year INTEGER NOT NULL,
                receipt_type TEXT NOT NULL CHECK (receipt_type IN ('signed', 'unsigned')),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_year_month ON receipts (year, month)"
        )
        connection.commit()


def init_billing_store() -> None:
    """Create the gala and three-month cycle billing tables when needed."""
    with closing(get_receipts_connection()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS galas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gala_number TEXT NOT NULL UNIQUE,
                tenant_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                monthly_rent REAL NOT NULL DEFAULT 0.0
            )
            """
        )
        _migrate_galas_table_if_needed(connection)
        _migrate_bills_table_if_needed(connection)
        _create_bills_table(connection)
        connection.execute("DROP INDEX IF EXISTS idx_bills_lookup")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_bill_lookup ON bills (bill_no)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_gala_period "
            "ON bills (gala_id, year, start_month)"
        )
        connection.commit()


def _create_bills_table(connection: sqlite3.Connection) -> None:
    """Create the current billing table shape supporting flexible month ranges."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_no TEXT NOT NULL UNIQUE,
            gala_id INTEGER NOT NULL,
            start_month INTEGER NOT NULL CHECK (start_month BETWEEN 1 AND 12),
            end_month INTEGER NOT NULL CHECK (end_month BETWEEN 1 AND 12 AND end_month >= start_month),
            year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 9999),
            cycle TEXT NOT NULL,
            amount REAL NOT NULL CHECK (amount > 0),
            payment_status TEXT NOT NULL DEFAULT 'Pending',
            amount_paid REAL NOT NULL DEFAULT 0.0,
            payment_method TEXT,
            pending_amount REAL,
            whatsapp_status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gala_id) REFERENCES galas(id) ON DELETE RESTRICT,
            UNIQUE (gala_id, start_month, end_month, year)
        )
        """
    )


def _migrate_galas_table_if_needed(connection: sqlite3.Connection) -> None:
    """Upgrade galas table to include monthly_rent if missing."""
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'galas'"
    ).fetchone()
    if not table_sql_row:
        return

    table_sql = str(table_sql_row["sql"] or "").lower()
    if "monthly_rent" in table_sql:
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("ALTER TABLE galas ADD COLUMN monthly_rent REAL NOT NULL DEFAULT 0.0")
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise

def _migrate_bills_table_if_needed(connection: sqlite3.Connection) -> None:
    """Upgrade the bills table schema if legacy CHECK constraints are detected."""
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'bills'"
    ).fetchone()
    if not table_sql_row:
        return

    table_sql = str(table_sql_row["sql"] or "").lower()
    if "whatsapp_status" not in table_sql:
        try:
            connection.execute("ALTER TABLE bills ADD COLUMN whatsapp_status TEXT DEFAULT 'Pending'")
        except sqlite3.Error:
            pass

    needs_migration = "amount_paid" not in table_sql or "start_month in" in table_sql or "cycle in" in table_sql

    if not needs_migration:
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DROP INDEX IF EXISTS idx_bills_lookup")
        connection.execute("DROP INDEX IF EXISTS idx_bill_lookup")
        connection.execute("DROP INDEX IF EXISTS idx_bills_gala_period")
        connection.execute("ALTER TABLE bills RENAME TO bills_legacy")
        _create_bills_table(connection)
        
        if "amount_paid" in table_sql:
            connection.execute(
                """
                INSERT INTO bills (
                    id, bill_no, gala_id, start_month, end_month, year, cycle, amount,
                    payment_status, amount_paid, payment_method, pending_amount, created_at
                )
                SELECT id, bill_no, gala_id, start_month, end_month, year, cycle, amount,
                    payment_status, amount_paid, payment_method, pending_amount, created_at
                FROM bills_legacy
                """
            )
        else:
            connection.execute(
                """
                INSERT INTO bills (
                    id, bill_no, gala_id, start_month, end_month, year, cycle, amount,
                    payment_status, created_at
                )
                SELECT id, bill_no, gala_id, start_month, end_month, year, cycle, amount,
                    CASE
                        WHEN payment_status IN ('unpaid', 'pending') THEN 'Pending'
                        WHEN payment_status = 'paid' THEN 'Full Paid'
                        WHEN payment_status = 'partial' THEN 'Half Paid'
                        ELSE 'Pending'
                    END,
                    created_at
                FROM bills_legacy
                """
            )
        connection.execute("DROP TABLE bills_legacy")
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise


def create_or_update_gala(
    *, gala_number: str, tenant_name: str, phone_number: str, monthly_rent: float
) -> dict[str, Any]:
    """Create a gala or update its current tenant contact details and rent atomically."""
    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM galas WHERE gala_number = ?", (gala_number,)
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE galas
                    SET tenant_name = ?, phone_number = ?, monthly_rent = ?
                    WHERE id = ?
                    """,
                    (tenant_name, phone_number, monthly_rent, existing["id"]),
                )
                gala_id = existing["id"]
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO galas (gala_number, tenant_name, phone_number, monthly_rent)
                    VALUES (?, ?, ?, ?)
                    """,
                    (gala_number, tenant_name, phone_number, monthly_rent),
                )
                gala_id = cursor.lastrowid

            record = connection.execute(
                "SELECT * FROM galas WHERE id = ?", (gala_id,)
            ).fetchone()
            connection.commit()
            return dict(record)
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("The gala could not be saved.") from exc


def list_galas() -> list[dict[str, Any]]:
    """Return all galas ordered naturally by their stored number."""
    with closing(get_receipts_connection()) as connection:
        rows = connection.execute(
            "SELECT * FROM galas ORDER BY gala_number COLLATE NOCASE, id"
        ).fetchall()
    return [dict(row) for row in rows]


def create_bill(
    *,
    gala_id: int,
    year: int,
    start_month: int,
    end_month: int,
    monthly_rent: float,
    billing_type: str = "quarter",
    cycle_code: str | None = None,
) -> dict[str, Any]:
    """Create a bill for a gala covering start_month to end_month (inclusive).
    
    Total Amount = Monthly Rent * Number of Months.
    """
    if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
        raise BillingStorageError("Start and end months must be between 1 and 12.")
    if start_month > end_month:
        raise BillingStorageError("End month cannot be before start month.")

    num_months = (end_month - start_month) + 1
    total_amount = round(monthly_rent * num_months, 2)
    code = cycle_code or get_cycle_code(start_month, end_month)

    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            gala = connection.execute(
                "SELECT id, gala_number, tenant_name, phone_number, monthly_rent FROM galas WHERE id = ?",
                (gala_id,),
            ).fetchone()
            if not gala:
                raise BillingStorageError("Selected gala no longer exists.")

            duplicate = connection.execute(
                """
                SELECT bill_no FROM bills
                WHERE gala_id = ? AND year = ? AND start_month = ? AND end_month = ?
                """,
                (gala_id, year, start_month, end_month),
            ).fetchone()
            if duplicate:
                period_str = format_period_label(start_month, end_month, year, mode=billing_type)
                raise BillingDuplicateError(
                    f"No bill created because a duplicate bill already exists for Gala {gala['gala_number']} ({period_str})."
                )

            count_row = connection.execute(
                "SELECT COUNT(*) AS c FROM bills WHERE year = ? AND cycle = ?",
                (year, code),
            ).fetchone()
            sequence = int(count_row["c"]) + 1

            while True:
                bill_no = f"BILL/{year}/{code}/{sequence:04d}"
                exists = connection.execute("SELECT 1 FROM bills WHERE bill_no = ?", (bill_no,)).fetchone()
                if not exists:
                    break
                sequence += 1

            cursor = connection.execute(
                """
                INSERT INTO bills (
                    bill_no, gala_id, start_month, end_month, year, cycle, amount, payment_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending')
                """,
                (bill_no, gala_id, start_month, end_month, year, code, total_amount),
            )
            record = connection.execute(
                """
                SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
                FROM bills
                JOIN galas ON galas.id = bills.gala_id
                WHERE bills.id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
            connection.commit()
            return dict(record)
        except BillingStorageError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise BillingDuplicateError(
                "No bill created because an identical gala billing period already exists."
            ) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("The billing record could not be saved.") from exc


def create_bills_for_cycles(
    *,
    gala_id: int,
    year: int,
    selected_quarters: list[str],
    monthly_rent: float,
) -> list[dict[str, Any]]:
    """Create individual, independent bill records for each selected quarter cycle (e.g., Q1, Q2).
    
    Each quarter generates an independent bill record:
    - 3 months
    - Amount = Monthly Rent * 3
    - Unique Bill Number (e.g. BILL/2026/Q1/0001, BILL/2026/Q2/0002)
    """
    from utils.bill_logic import QUARTER_LIST, QUARTERS_INFO

    if not selected_quarters:
        raise BillingStorageError("Please select at least one quarter cycle.")

    valid_quarters = [q.upper().strip() for q in selected_quarters if q.upper().strip() in QUARTER_LIST]
    if not valid_quarters:
        raise BillingStorageError("Invalid quarter cycles selected.")

    indices = sorted(QUARTER_LIST.index(q) for q in set(valid_quarters))
    for i in range(len(indices) - 1):
        if indices[i + 1] != indices[i] + 1:
            raise BillingStorageError("Selected quarters must be consecutive (e.g., Q1+Q2 or Q2+Q3+Q4).")

    ordered_quarters = [QUARTER_LIST[idx] for idx in indices]

    # Pre-check duplicates so we fail atomically before creating partial records
    with closing(get_receipts_connection()) as connection:
        for q_code in ordered_quarters:
            q_info = QUARTERS_INFO[q_code]
            s_m, e_m = int(q_info["start"]), int(q_info["end"])
            duplicate = connection.execute(
                "SELECT bill_no FROM bills WHERE gala_id = ? AND year = ? AND start_month = ? AND end_month = ?",
                (gala_id, year, s_m, e_m),
            ).fetchone()
            if duplicate:
                gala = connection.execute("SELECT gala_number FROM galas WHERE id = ?", (gala_id,)).fetchone()
                gala_num = gala["gala_number"] if gala else ""
                raise BillingDuplicateError(
                    f"No bills created because a duplicate bill already exists for Gala {gala_num} ({q_code} {year})."
                )

    created_records = []
    for q_code in ordered_quarters:
        q_info = QUARTERS_INFO[q_code]
        s_m, e_m = int(q_info["start"]), int(q_info["end"])
        record = create_bill(
            gala_id=gala_id,
            year=year,
            start_month=s_m,
            end_month=e_m,
            monthly_rent=monthly_rent,
            billing_type="quarter",
            cycle_code=q_code,
        )
        created_records.append(record)

    return created_records


def create_cycle_bills(
    *, gala_ids: list[int], year: int, start_cycle: str, end_cycle: str, amount: float
) -> list[dict[str, Any]]:
    """Create every requested gala/cycle bill as one duplicate-safe transaction."""
    if not gala_ids:
        raise BillingStorageError("Select at least one gala before generating bills.")

    try:
        cycles = get_cycles_between(start_cycle, end_cycle)
    except ValueError as exc:
        raise BillingStorageError(str(exc)) from exc

    unique_gala_ids = sorted(set(gala_ids))
    placeholders = ", ".join("?" for _ in unique_gala_ids)
    period_clause = " OR ".join(
        "(bills.start_month = ? AND bills.end_month = ?)" for _ in cycles
    )
    period_parameters = [value for cycle in cycles for value in get_cycle_months(cycle)]

    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            gala_rows = connection.execute(
                f"SELECT id, gala_number, tenant_name, phone_number, monthly_rent FROM galas WHERE id IN ({placeholders})",
                unique_gala_ids,
            ).fetchall()
            if len(gala_rows) != len(unique_gala_ids):
                raise BillingStorageError("One or more selected galas no longer exist.")

            duplicate_rows = connection.execute(
                f"""
                SELECT galas.gala_number, bills.cycle
                FROM bills
                JOIN galas ON galas.id = bills.gala_id
                WHERE bills.gala_id IN ({placeholders})
                  AND bills.year = ?
                  AND ({period_clause})
                ORDER BY galas.gala_number COLLATE NOCASE, bills.start_month
                """,
                [*unique_gala_ids, year, *period_parameters],
            ).fetchall()
            if duplicate_rows:
                duplicate_labels = [
                    f"Gala {row['gala_number']} ({row['cycle']})" for row in duplicate_rows
                ]
                labels = ", ".join(duplicate_labels[:5])
                suffix = "" if len(duplicate_labels) <= 5 else ", and more"
                raise BillingDuplicateError(
                    f"No bills were created because duplicate cycles already exist for {labels}{suffix}."
                )

            existing_numbers = {
                row["bill_no"]
                for row in connection.execute(
                    f"SELECT bill_no FROM bills WHERE year = ? AND cycle IN ({', '.join('?' for _ in cycles)})",
                    [year, *cycles],
                ).fetchall()
            }
            sequence_by_cycle = {
                cycle: sum(f"/{cycle}/" in bill_no for bill_no in existing_numbers) + 1
                for cycle in cycles
            }

            created: list[dict[str, Any]] = []
            ordered_galas = sorted(
                gala_rows, key=lambda row: (row["gala_number"].casefold(), row["id"])
            )
            for gala in ordered_galas:
                for cycle in cycles:
                    start_month, end_month = get_cycle_months(cycle)
                    sequence = sequence_by_cycle[cycle]
                    bill_no = f"BILL/{year}/{cycle}/{sequence:04d}"
                    while bill_no in existing_numbers:
                        sequence += 1
                        bill_no = f"BILL/{year}/{cycle}/{sequence:04d}"
                    if sequence > 9999:
                        raise BillingStorageError(
                            "Billing cycle capacity of 9999 bills has been reached."
                        )
                    sequence_by_cycle[cycle] = sequence + 1
                    existing_numbers.add(bill_no)

                    cursor = connection.execute(
                        """
                        INSERT INTO bills (
                            bill_no, gala_id, start_month, end_month, year, cycle, amount, payment_status
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending')
                        """,
                        (bill_no, gala["id"], start_month, end_month, year, cycle, amount),
                    )
                    record = connection.execute(
                        """
                        SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
                        FROM bills
                        JOIN galas ON galas.id = bills.gala_id
                        WHERE bills.id = ?
                        """,
                        (cursor.lastrowid,),
                    ).fetchone()
                    created.append(dict(record))

            connection.commit()
            return created
        except BillingStorageError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise BillingDuplicateError(
                "No bills were created because an identical gala billing cycle already exists."
            ) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("The billing records could not be saved.") from exc


def list_bills(limit: int = 100) -> list[dict[str, Any]]:
    """Return recent bills with their gala and tenant information."""
    with closing(get_receipts_connection()) as connection:
        rows = connection.execute(
            """
            SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
            FROM bills
            JOIN galas ON galas.id = bills.gala_id
            ORDER BY bills.year DESC, bills.start_month DESC, bills.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_bill_by_no(bill_no: str) -> dict[str, Any] | None:
    """Return a single bill with tenant details for search and payment actions."""
    with closing(get_receipts_connection()) as connection:
        row = connection.execute(
            """
            SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
            FROM bills
            JOIN galas ON galas.id = bills.gala_id
            WHERE bills.bill_no = ?
            """,
            (bill_no,),
        ).fetchone()
    return dict(row) if row else None


def update_bill_whatsapp_status(bill_no: str, status: str) -> bool:
    """Update delivery status ('Sent', 'Failed', 'Pending') for a bill in SQLite."""
    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE bills SET whatsapp_status = ? WHERE bill_no = ?",
                (status, bill_no),
            )
            updated = cursor.rowcount > 0
            connection.commit()
            return updated
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("Could not update WhatsApp delivery status.") from exc


def update_bill_payment_status(
    bill_no: str, amount_paid: float, payment_method: str) -> dict[str, Any] | None:
    """Set a bill's payment state and return its refreshed details."""
    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("SELECT amount FROM bills WHERE bill_no = ?", (bill_no,))
            row = cursor.fetchone()
            if not row:
                connection.rollback()
                return None
                
            rent_amount = row["amount"]
            pending_amount = max(0, rent_amount - amount_paid)
            
            if amount_paid >= rent_amount:
                payment_status = "Full Paid"
            elif amount_paid > 0:
                payment_status = "Half Paid"
            else:
                payment_status = "Pending"
                
            cursor = connection.execute(
                "UPDATE bills SET payment_status = ?, amount_paid = ?, pending_amount = ?, payment_method = ? WHERE bill_no = ?",
                (payment_status, amount_paid, pending_amount, payment_method, bill_no),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return None
            row = connection.execute(
                """
                SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
                FROM bills
                JOIN galas ON galas.id = bills.gala_id
                WHERE bills.bill_no = ?
                """,
                (bill_no,),
            ).fetchone()
            connection.commit()
            return dict(row)
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("The payment status could not be updated.") from exc


def generate_receipt_number(
    connection: sqlite3.Connection, reference_time: datetime | None = None
) -> str:
    """Allocate the next RCPT/YYYY/MM/XXXX number within an active transaction."""
    current_time = reference_time or datetime.now()
    year = current_time.year
    month = f"{current_time.month:02d}"
    count_row = connection.execute(
        "SELECT COUNT(*) AS receipt_count FROM receipts WHERE year = ? AND month = ?",
        (year, month),
    ).fetchone()
    sequence = int(count_row["receipt_count"]) + 1

    # Count is normally sufficient; this extra check handles manually deleted rows safely.
    while True:
        if sequence > 9999:
            raise ReceiptStorageError("Monthly receipt number capacity of 9999 has been reached.")
        receipt_no = f"RCPT/{year}/{month}/{sequence:04d}"
        exists = connection.execute(
            "SELECT 1 FROM receipts WHERE receipt_no = ?", (receipt_no,)
        ).fetchone()
        if not exists:
            return receipt_no
        sequence += 1


def preview_receipt_number(reference_time: datetime | None = None) -> str:
    """Return the next likely receipt number without creating a database record."""
    with closing(get_receipts_connection()) as connection:
        return generate_receipt_number(connection, reference_time)


def create_numbered_receipt(
    *,
    tenant_name: str,
    amount: float,
    house_no: str,
    receipt_type: str,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Persist a receipt and atomically assign its monthly structured receipt number."""
    if receipt_type not in {"signed", "unsigned"}:
        raise ReceiptStorageError("Receipt type must be signed or unsigned.")

    current_time = reference_time or datetime.now()
    with closing(get_receipts_connection()) as connection:
        try:
            # This lock keeps the monthly count and insert atomic across Flask requests.
            connection.execute("BEGIN IMMEDIATE")
            receipt_no = generate_receipt_number(connection, current_time)
            cursor = connection.execute(
                """
                INSERT INTO receipts (receipt_no, tenant_name, amount, house_no, month, year, receipt_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_no,
                    tenant_name,
                    amount,
                    house_no,
                    f"{current_time.month:02d}",
                    current_time.year,
                    receipt_type,
                ),
            )
            record = connection.execute(
                "SELECT * FROM receipts WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
            return dict(record)
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ReceiptStorageError("A duplicate receipt number was detected. Please try again.") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise ReceiptStorageError("The receipt database could not be updated.") from exc


def list_numbered_receipts() -> list[dict[str, Any]]:
    """Return all structured receipts, newest first."""
    with closing(get_receipts_connection()) as connection:
        rows = connection.execute("SELECT * FROM receipts ORDER BY created_at DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def get_numbered_receipt(receipt_no: str) -> dict[str, Any] | None:
    """Return one structured receipt by its full receipt number, if it exists."""
    with closing(get_receipts_connection()) as connection:
        row = connection.execute("SELECT * FROM receipts WHERE receipt_no = ?", (receipt_no,)).fetchone()
    return dict(row) if row else None


def create_receipt(
    *, customer_name: str, shop_name: str, amount: float, receipt_date: str
) -> dict[str, Any]:
    """Insert one receipt and atomically allocate its next sequential bill number."""
    with closing(get_connection()) as connection:
        try:
            # IMMEDIATE prevents two concurrent submissions receiving the same number.
            connection.execute("BEGIN IMMEDIATE")
            bill_number = generate_next_bill_number(connection)
            cursor = connection.execute(
                """
                INSERT INTO receipts (shop_name, customer_name, amount, date, bill_number)
                VALUES (?, ?, ?, ?, ?)
                """,
                (shop_name, customer_name, amount, receipt_date, bill_number),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise

    return {
        "id": cursor.lastrowid,
        "shop_name": shop_name,
        "customer_name": customer_name,
        "amount": amount,
        "date": receipt_date,
        "bill_number": bill_number,
    }
