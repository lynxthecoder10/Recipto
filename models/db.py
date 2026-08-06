"""SQLite persistence helpers for receipt records."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.bill_logic import generate_next_bill_number


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "database.db"
RECEIPTS_DATABASE_PATH = PROJECT_ROOT / "receipts.db"


class ReceiptStorageError(RuntimeError):
    """Raised when a structured receipt record cannot be stored safely."""


def get_connection() -> sqlite3.Connection:
    """Return a connection configured for named-column access and foreign keys."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    """Create the receipts table when the application starts for the first time."""
    connection = get_connection()
    try:
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
    finally:
        connection.close()


def get_receipts_connection() -> sqlite3.Connection:
    """Return a connection to the structured receipt-number storage database."""
    connection = sqlite3.connect(RECEIPTS_DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_receipt_store() -> None:
    """Create the receipts.db schema used by downloadable rent receipts."""
    connection = get_receipts_connection()
    try:
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
    finally:
        connection.close()


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
    connection = get_receipts_connection()
    try:
        return generate_receipt_number(connection, reference_time)
    finally:
        connection.close()


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
    connection = get_receipts_connection()
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
    finally:
        connection.close()


def list_numbered_receipts() -> list[dict[str, Any]]:
    """Return all structured receipts, newest first."""
    connection = get_receipts_connection()
    try:
        rows = connection.execute("SELECT * FROM receipts ORDER BY created_at DESC, id DESC").fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def get_numbered_receipt(receipt_no: str) -> dict[str, Any] | None:
    """Return one structured receipt by its full receipt number, if it exists."""
    connection = get_receipts_connection()
    try:
        row = connection.execute("SELECT * FROM receipts WHERE receipt_no = ?", (receipt_no,)).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def create_receipt(
    *, customer_name: str, shop_name: str, amount: float, receipt_date: str
) -> dict[str, Any]:
    """Insert one receipt and atomically allocate its next sequential bill number."""
    connection = get_connection()
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
    finally:
        connection.close()

    return {
        "id": cursor.lastrowid,
        "shop_name": shop_name,
        "customer_name": customer_name,
        "amount": amount,
        "date": receipt_date,
        "bill_number": bill_number,
    }
