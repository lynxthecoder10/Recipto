"""SQLite persistence helpers for receipt and billing records."""

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
                receipt_no TEXT NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 0,
                tenant_name TEXT NOT NULL,
                amount REAL NOT NULL CHECK (amount > 0),
                house_no TEXT NOT NULL,
                month TEXT NOT NULL,
                year INTEGER NOT NULL,
                receipt_type TEXT NOT NULL CHECK (receipt_type IN ('signed', 'unsigned')),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (receipt_no, user_id)
            )
            """
        )
        _migrate_receipts_columns(connection)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_year_month ON receipts (year, month)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_user_lookup ON receipts (receipt_no, user_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_user_period ON receipts (user_id, year, month)"
        )
        connection.commit()


def _migrate_receipts_columns(connection: sqlite3.Connection) -> None:
    """Ensure receipts table has user_id and UNIQUE(receipt_no, user_id)."""
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'receipts'"
    ).fetchone()
    if not table_sql_row:
        return

    table_sql = str(table_sql_row["sql"] or "")
    table_sql_condensed = table_sql.lower().replace(" ", "").replace("\n", "").replace('"', '').replace("'", "")

    needs_rebuild = (
        "unique(receipt_no,user_id)" not in table_sql_condensed
        or "user_id" not in table_sql.lower()
    )

    if needs_rebuild:
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE receipts RENAME TO _receipts_old")
            connection.execute(
                """
                CREATE TABLE receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_no TEXT NOT NULL,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    tenant_name TEXT NOT NULL,
                    amount REAL NOT NULL CHECK (amount > 0),
                    house_no TEXT NOT NULL,
                    month TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    receipt_type TEXT NOT NULL CHECK (receipt_type IN ('signed', 'unsigned')),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (receipt_no, user_id)
                )
                """
            )

            old_cols = {row[1] for row in connection.execute("PRAGMA table_info(_receipts_old)").fetchall()}
            user_id_expr = "COALESCE(user_id, 0)" if "user_id" in old_cols else "0"

            connection.execute(
                f"""
                INSERT INTO receipts (
                    id, receipt_no, user_id, tenant_name, amount, house_no, month, year, receipt_type, created_at
                )
                SELECT
                    id, receipt_no, {user_id_expr}, tenant_name, amount, house_no, month, year, receipt_type,
                    COALESCE(created_at, CURRENT_TIMESTAMP)
                FROM _receipts_old
                """
            )
            connection.execute("DROP TABLE _receipts_old")
        except sqlite3.Error:
            try:
                connection.execute("ALTER TABLE _receipts_old RENAME TO receipts")
            except sqlite3.Error:
                pass
        finally:
            connection.execute("PRAGMA foreign_keys = ON")


def init_billing_store() -> None:
    """Create the users, galas, and bills tables, apply migrations, and create indexes."""
    with closing(get_receipts_connection()) as connection:
        # 1. Users table for authentication
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'owner',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 2. Galas table
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS galas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gala_number TEXT NOT NULL,
                tenant_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                monthly_rent REAL NOT NULL DEFAULT 0.0,
                user_id INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (gala_number, user_id)
            )
            """
        )

        # Run schema upgrades on existing users table if needed
        _migrate_users_columns(connection)

        # Run schema upgrades on existing galas table if needed
        _migrate_galas_columns(connection)

        # 3. Bills table
        _create_bills_table(connection)
        _migrate_bills_columns(connection)

        # 4. Performance Indexes
        connection.execute("DROP INDEX IF EXISTS idx_bills_lookup")
        connection.execute("DROP INDEX IF EXISTS idx_bill_lookup")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_bill_lookup ON bills (bill_no, user_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_gala_period ON bills (gala_id, year, start_month)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_bills_user ON bills (user_id, year, start_month)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_galas_user ON galas (user_id)"
        )
        connection.commit()


def _create_bills_table(connection: sqlite3.Connection) -> None:
    """Create the billing table with user scoping and flexible month ranges."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_no TEXT NOT NULL,
            gala_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 0,
            start_month INTEGER NOT NULL CHECK (start_month BETWEEN 1 AND 12),
            end_month INTEGER NOT NULL CHECK (end_month BETWEEN 1 AND 12 AND end_month >= start_month),
            year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 9999),
            cycle TEXT NOT NULL,
            amount REAL NOT NULL CHECK (amount > 0),
            payment_status TEXT NOT NULL DEFAULT 'pending',
            amount_paid REAL NOT NULL DEFAULT 0.0,
            payment_method TEXT,
            pending_amount REAL,
            whatsapp_status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gala_id) REFERENCES galas(id) ON DELETE RESTRICT,
            UNIQUE (bill_no, user_id),
            UNIQUE (gala_id, start_month, end_month, year)
        )
        """
    )


def _migrate_users_columns(connection: sqlite3.Connection) -> None:
    """Ensure all required columns exist in the users table."""
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if not existing:
        return  # Table doesn't exist yet

    if "created_at" not in existing:
        try:
            connection.execute(
                "ALTER TABLE users ADD COLUMN created_at TIMESTAMP"
            )
        except sqlite3.Error:
            pass


def _migrate_galas_columns(connection: sqlite3.Connection) -> None:
    """Ensure galas table has correct columns AND the right UNIQUE constraint."""
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'galas'"
    ).fetchone()
    if not table_sql_row:
        return

    table_sql = str(table_sql_row["sql"] or "")
    table_sql_lower = table_sql.lower()

    if "monthly_rent" not in table_sql_lower:
        try:
            connection.execute("ALTER TABLE galas ADD COLUMN monthly_rent REAL NOT NULL DEFAULT 0.0")
        except sqlite3.Error:
            pass

    if "user_id" not in table_sql_lower:
        try:
            connection.execute("ALTER TABLE galas ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
        except sqlite3.Error:
            pass

    needs_rebuild = (
        "unique" in table_sql_lower
        and "gala_number" in table_sql_lower
        and "unique (gala_number, user_id)" not in table_sql_lower.replace(" ", "").replace("\n", "")
    )

    if needs_rebuild:
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE galas RENAME TO _galas_old")
            connection.execute(
                """
                CREATE TABLE galas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gala_number TEXT NOT NULL,
                    tenant_name TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    monthly_rent REAL NOT NULL DEFAULT 0.0,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP,
                    UNIQUE (gala_number, user_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO galas (id, gala_number, tenant_name, phone_number, monthly_rent, user_id)
                SELECT id, gala_number, tenant_name, phone_number,
                       COALESCE(monthly_rent, 0.0),
                       COALESCE(user_id, 0)
                FROM _galas_old
                """
            )
            connection.execute("DROP TABLE _galas_old")
        except sqlite3.Error:
            try:
                connection.execute("ALTER TABLE _galas_old RENAME TO galas")
            except sqlite3.Error:
                pass
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
    else:
        existing_cols = {
            row[1]
            for row in connection.execute("PRAGMA table_info(galas)").fetchall()
        }
        if "created_at" not in existing_cols:
            try:
                connection.execute("ALTER TABLE galas ADD COLUMN created_at TIMESTAMP")
            except sqlite3.Error:
                pass


def _migrate_bills_columns(connection: sqlite3.Connection) -> None:
    """Ensure bills table has user_id, right UNIQUE(bill_no, user_id), and proper FK."""
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'bills'"
    ).fetchone()
    if not table_sql_row:
        return

    table_sql = str(table_sql_row["sql"] or "")
    table_sql_lower = table_sql.lower()
    table_sql_condensed = table_sql_lower.replace(" ", "").replace("\n", "").replace('"', '').replace("'", "")

    needs_rebuild = (
        "_galas_old" in table_sql_lower
        or "unique(bill_no,user_id)" not in table_sql_condensed
        or "user_id" not in table_sql_lower
    )

    if needs_rebuild:
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE bills RENAME TO _bills_old")
            _create_bills_table(connection)

            old_cols = {row[1] for row in connection.execute("PRAGMA table_info(_bills_old)").fetchall()}
            user_id_expr = "COALESCE(user_id, 0)" if "user_id" in old_cols else "0"
            whatsapp_expr = "COALESCE(whatsapp_status, 'Pending')" if "whatsapp_status" in old_cols else "'Pending'"
            pending_expr = "COALESCE(pending_amount, amount)" if "pending_amount" in old_cols else "amount"
            paid_expr = "COALESCE(amount_paid, 0.0)" if "amount_paid" in old_cols else "0.0"

            connection.execute(
                f"""
                INSERT INTO bills (
                    id, bill_no, gala_id, user_id, start_month, end_month, year,
                    cycle, amount, payment_status, amount_paid, payment_method,
                    pending_amount, whatsapp_status, created_at
                )
                SELECT
                    id, bill_no, gala_id, {user_id_expr}, start_month, end_month, year,
                    cycle, amount, payment_status, {paid_expr}, payment_method,
                    {pending_expr}, {whatsapp_expr}, COALESCE(created_at, CURRENT_TIMESTAMP)
                FROM _bills_old
                """
            )
            connection.execute("DROP TABLE _bills_old")
        except sqlite3.Error:
            try:
                connection.execute("ALTER TABLE _bills_old RENAME TO bills")
            except sqlite3.Error:
                pass
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
    else:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(bills)").fetchall()}
        if "whatsapp_status" not in existing:
            try:
                connection.execute("ALTER TABLE bills ADD COLUMN whatsapp_status TEXT DEFAULT 'Pending'")
            except sqlite3.Error:
                pass
        if "user_id" not in existing:
            try:
                connection.execute("ALTER TABLE bills ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
            except sqlite3.Error:
                pass


# ===========================================================================
# User Authentication & Profile Management
# ===========================================================================

def create_user(
    *, email: str, password_hash: str, name: str, role: str = "owner"
) -> dict[str, Any]:
    """Create a new user in the system."""
    clean_email = email.strip().lower()
    clean_name = name.strip()
    clean_role = role.strip().lower()

    with closing(get_receipts_connection()) as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO users (email, password_hash, name, role)
                VALUES (?, ?, ?, ?)
                """,
                (clean_email, password_hash, clean_name, clean_role),
            )
            connection.commit()
            user = connection.execute(
                "SELECT id, email, name, role, created_at FROM users WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(user)
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise BillingStorageError("An account with this email already exists.") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("Could not create user account.") from exc


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Retrieve user record by email (including password_hash for auth checks)."""
    with closing(get_receipts_connection()) as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(?)",
            (email.strip(),),
        ).fetchone()
        return dict(user) if user else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Retrieve safe user profile details by ID."""
    with closing(get_receipts_connection()) as connection:
        user = connection.execute(
            "SELECT id, email, name, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(user) if user else None


def update_user_profile(
    user_id: int, *, name: str, email: str, password_hash: str | None = None
) -> dict[str, Any]:
    """Update profile information and optionally password_hash."""
    clean_name = name.strip()
    clean_email = email.strip().lower()

    with closing(get_receipts_connection()) as connection:
        try:
            if password_hash:
                connection.execute(
                    "UPDATE users SET name = ?, email = ?, password_hash = ? WHERE id = ?",
                    (clean_name, clean_email, password_hash, user_id),
                )
            else:
                connection.execute(
                    "UPDATE users SET name = ?, email = ? WHERE id = ?",
                    (clean_name, clean_email, user_id),
                )
            connection.commit()
            user = connection.execute(
                "SELECT id, email, name, role, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            return dict(user) if user else {}
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise BillingStorageError("This email address is already in use by another account.") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("Could not update profile.") from exc


def ensure_default_user() -> dict[str, Any]:
    """Ensure at least one default owner user exists in the database."""
    with closing(get_receipts_connection()) as connection:
        user = connection.execute("SELECT * FROM users ORDER BY id ASC LIMIT 1").fetchone()
        if user:
            return dict(user)

        from werkzeug.security import generate_password_hash
        default_pwd_hash = generate_password_hash("Owner@12345")
        cursor = connection.execute(
            """
            INSERT INTO users (email, password_hash, name, role)
            VALUES (?, ?, ?, ?)
            """,
            ("owner@recipto.com", default_pwd_hash, "Estate Manager", "owner"),
        )
        connection.commit()
        created = connection.execute(
            "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(created)


# ===========================================================================
# Gala Property Unit Operations
# ===========================================================================

def create_or_update_gala(
    *,
    gala_number: str,
    tenant_name: str,
    phone_number: str,
    monthly_rent: float,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Create or update a gala property unit scoped to a user."""
    target_user_id = user_id if user_id is not None else 1

    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM galas WHERE gala_number = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                (gala_number, target_user_id, target_user_id),
            ).fetchone()

            if existing:
                connection.execute(
                    """
                    UPDATE galas
                    SET tenant_name = ?, phone_number = ?, monthly_rent = ?, user_id = ?
                    WHERE id = ?
                    """,
                    (tenant_name, phone_number, monthly_rent, target_user_id, existing["id"]),
                )
                gala_id = existing["id"]
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO galas (gala_number, tenant_name, phone_number, monthly_rent, user_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (gala_number, tenant_name, phone_number, monthly_rent, target_user_id),
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


def get_gala_by_id(gala_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    """Return a single gala by ID, optionally validating user ownership."""
    with closing(get_receipts_connection()) as connection:
        if user_id is not None:
            row = connection.execute(
                "SELECT * FROM galas WHERE id = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                (gala_id, user_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM galas WHERE id = ?", (gala_id,)
            ).fetchone()
        return dict(row) if row else None


def list_galas(user_id: int | None = None) -> list[dict[str, Any]]:
    """Return all galas ordered naturally by gala_number, optionally filtered by user_id."""
    with closing(get_receipts_connection()) as connection:
        if user_id is not None:
            rows = connection.execute(
                "SELECT * FROM galas WHERE user_id = ? OR (user_id = 0 AND ? = 1) ORDER BY gala_number COLLATE NOCASE, id",
                (user_id, user_id),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM galas ORDER BY gala_number COLLATE NOCASE, id"
            ).fetchall()
    return [dict(row) for row in rows]


# ===========================================================================
# Bill Generation & Lifecycle Management
# ===========================================================================

def create_bill(
    *,
    gala_id: int,
    year: int,
    start_month: int,
    end_month: int,
    monthly_rent: float,
    billing_type: str = "quarter",
    cycle_code: str | None = None,
    user_id: int | None = None,
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
                "SELECT id, gala_number, tenant_name, phone_number, monthly_rent, user_id FROM galas WHERE id = ?",
                (gala_id,)
            ).fetchone()
            if not gala:
                raise BillingStorageError("Selected gala no longer exists.")

            target_user_id = user_id if user_id is not None else gala["user_id"]
            if user_id is not None and gala["user_id"] != user_id and gala["user_id"] != 0:
                raise BillingStorageError("Unauthorized: cannot create a bill for a gala you do not own.")

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
                "SELECT COUNT(*) AS c FROM bills WHERE (user_id = ? OR (user_id = 0 AND ? = 1)) AND year = ? AND cycle = ?",
                (target_user_id, target_user_id, year, code),
            ).fetchone()
            sequence = int(count_row["c"]) + 1

            while True:
                bill_no = f"BILL/{year}/{code}/{sequence:04d}"
                exists = connection.execute(
                    "SELECT 1 FROM bills WHERE bill_no = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                    (bill_no, target_user_id, target_user_id),
                ).fetchone()
                if not exists:
                    break
                sequence += 1

            cursor = connection.execute(
                """
                INSERT INTO bills (
                    bill_no, gala_id, user_id, start_month, end_month, year, cycle, amount, payment_status, pending_amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (bill_no, gala_id, target_user_id, start_month, end_month, year, code, total_amount, total_amount),
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
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Create individual, independent bill records for each selected quarter cycle (e.g., Q1, Q2)."""
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
            user_id=user_id,
        )
        created_records.append(record)

    return created_records


def create_cycle_bills(
    *,
    gala_ids: list[int],
    year: int,
    start_cycle: str,
    end_cycle: str,
    amount: float,
    user_id: int | None = None,
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
            if user_id is not None:
                gala_rows = connection.execute(
                    f"SELECT id, gala_number, tenant_name, phone_number, monthly_rent, user_id FROM galas WHERE id IN ({placeholders}) AND (user_id = ? OR user_id = 0)",
                    (*unique_gala_ids, user_id),
                ).fetchall()
            else:
                gala_rows = connection.execute(
                    f"SELECT id, gala_number, tenant_name, phone_number, monthly_rent, user_id FROM galas WHERE id IN ({placeholders})",
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

            effective_user_id = user_id if user_id is not None else (gala_rows[0]["user_id"] if gala_rows else 1)
            existing_numbers = {
                row["bill_no"]
                for row in connection.execute(
                    f"SELECT bill_no FROM bills WHERE (user_id = ? OR (user_id = 0 AND ? = 1)) AND year = ? AND cycle IN ({', '.join('?' for _ in cycles)})",
                    [effective_user_id, effective_user_id, year, *cycles],
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
                target_user_id = user_id if user_id is not None else gala["user_id"]
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
                            bill_no, gala_id, user_id, start_month, end_month, year, cycle, amount, payment_status, pending_amount
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                        """,
                        (
                            bill_no,
                            gala["id"],
                            target_user_id,
                            start_month,
                            end_month,
                            year,
                            cycle,
                            amount,
                            amount,
                        ),
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


def list_bills(limit: int = 100, user_id: int | None = None) -> list[dict[str, Any]]:
    """Return recent bills with gala and tenant info, optionally filtered by user_id."""
    with closing(get_receipts_connection()) as connection:
        if user_id is not None:
            rows = connection.execute(
                """
                SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
                FROM bills
                JOIN galas ON galas.id = bills.gala_id
                WHERE bills.user_id = ? OR bills.user_id = 0
                ORDER BY bills.year DESC, bills.start_month DESC, bills.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
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


def get_bill_by_no(bill_no: str, user_id: int | None = None) -> dict[str, Any] | None:
    """Return a single bill with tenant details, optionally validating user ownership."""
    with closing(get_receipts_connection()) as connection:
        if user_id is not None:
            row = connection.execute(
                """
                SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent, galas.user_id AS gala_user_id
                FROM bills
                JOIN galas ON galas.id = bills.gala_id
                WHERE bills.bill_no = ? AND (bills.user_id = ? OR (bills.user_id = 0 AND ? = 1))
                """,
                (bill_no, user_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent, galas.user_id AS gala_user_id
                FROM bills
                JOIN galas ON galas.id = bills.gala_id
                WHERE bills.bill_no = ?
                """,
                (bill_no,),
            ).fetchone()
    return dict(row) if row else None


def update_bill_whatsapp_status(bill_no: str, status: str, user_id: int | None = None) -> bool:
    """Update delivery status ('Sent', 'Failed', 'Pending') for a bill in SQLite."""
    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            if user_id is not None:
                owner_check = connection.execute(
                    "SELECT user_id FROM bills WHERE bill_no = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                    (bill_no, user_id, user_id),
                ).fetchone()
                if not owner_check:
                    connection.rollback()
                    raise BillingStorageError("Unauthorized update of WhatsApp status.")

                cursor = connection.execute(
                    "UPDATE bills SET whatsapp_status = ? WHERE bill_no = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                    (status, bill_no, user_id, user_id),
                )
            else:
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
    bill_no: str, amount_paid: float, payment_method: str, user_id: int | None = None
) -> dict[str, Any] | None:
    """Set a bill's payment state and return its refreshed details."""
    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            if user_id is not None:
                owner_check = connection.execute(
                    "SELECT amount FROM bills WHERE bill_no = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                    (bill_no, user_id, user_id),
                ).fetchone()
                if not owner_check:
                    connection.rollback()
                    raise BillingStorageError("Unauthorized payment update for this bill.")
                row = owner_check
            else:
                cursor = connection.execute("SELECT amount FROM bills WHERE bill_no = ?", (bill_no,))
                row = cursor.fetchone()
                if not row:
                    connection.rollback()
                    return None

            rent_amount = float(row["amount"])
            pending_amount = max(0.0, rent_amount - amount_paid)

            if amount_paid >= rent_amount:
                payment_status = "paid"
            elif amount_paid > 0:
                payment_status = "partial"
            else:
                payment_status = "pending"

            if user_id is not None:
                cursor = connection.execute(
                    """
                    UPDATE bills
                    SET payment_status = ?, amount_paid = ?, pending_amount = ?, payment_method = ?
                    WHERE bill_no = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))
                    """,
                    (payment_status, amount_paid, pending_amount, payment_method, bill_no, user_id, user_id),
                )
            else:
                cursor = connection.execute(
                    "UPDATE bills SET payment_status = ?, amount_paid = ?, pending_amount = ?, payment_method = ? WHERE bill_no = ?",
                    (payment_status, amount_paid, pending_amount, payment_method, bill_no),
                )

            if cursor.rowcount == 0:
                connection.rollback()
                return None

            if user_id is not None:
                refreshed = connection.execute(
                    """
                    SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
                    FROM bills
                    JOIN galas ON galas.id = bills.gala_id
                    WHERE bills.bill_no = ? AND (bills.user_id = ? OR (bills.user_id = 0 AND ? = 1))
                    """,
                    (bill_no, user_id, user_id),
                ).fetchone()
            else:
                refreshed = connection.execute(
                    """
                    SELECT bills.*, galas.gala_number, galas.tenant_name, galas.phone_number, galas.monthly_rent
                    FROM bills
                    JOIN galas ON galas.id = bills.gala_id
                    WHERE bills.bill_no = ?
                    """,
                    (bill_no,),
                ).fetchone()
            connection.commit()
            return dict(refreshed)
        except sqlite3.Error as exc:
            connection.rollback()
            raise BillingStorageError("The payment status could not be updated.") from exc


# ===========================================================================
# Structured Receipt Operations (Standalone Receipt Numbering)
# ===========================================================================

def generate_receipt_number(
    connection: sqlite3.Connection,
    reference_time: datetime | None = None,
    user_id: int | None = None,
) -> str:
    """Allocate the next RCPT/YYYY/MM/XXXX number within an active transaction for the user."""
    current_time = reference_time or datetime.now()
    year = current_time.year
    month = f"{current_time.month:02d}"
    target_user_id = user_id if user_id is not None else 0

    if user_id is not None:
        count_row = connection.execute(
            "SELECT COUNT(*) AS receipt_count FROM receipts WHERE (user_id = ? OR (user_id = 0 AND ? = 1)) AND year = ? AND month = ?",
            (target_user_id, target_user_id, year, month),
        ).fetchone()
    else:
        count_row = connection.execute(
            "SELECT COUNT(*) AS receipt_count FROM receipts WHERE year = ? AND month = ?",
            (year, month),
        ).fetchone()
    sequence = int(count_row["receipt_count"]) + 1

    while True:
        if sequence > 9999:
            raise ReceiptStorageError("Monthly receipt number capacity of 9999 has been reached.")
        receipt_no = f"RCPT/{year}/{month}/{sequence:04d}"
        if user_id is not None:
            exists = connection.execute(
                "SELECT 1 FROM receipts WHERE receipt_no = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                (receipt_no, target_user_id, target_user_id),
            ).fetchone()
        else:
            exists = connection.execute(
                "SELECT 1 FROM receipts WHERE receipt_no = ?", (receipt_no,)
            ).fetchone()
        if not exists:
            return receipt_no
        sequence += 1


def preview_receipt_number(reference_time: datetime | None = None, user_id: int | None = None) -> str:
    """Return the next likely receipt number without creating a database record."""
    with closing(get_receipts_connection()) as connection:
        return generate_receipt_number(connection, reference_time, user_id=user_id)


def create_numbered_receipt(
    *,
    tenant_name: str,
    amount: float,
    house_no: str,
    receipt_type: str,
    reference_time: datetime | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Persist a receipt and atomically assign its monthly structured receipt number."""
    if receipt_type not in {"signed", "unsigned"}:
        raise ReceiptStorageError("Receipt type must be signed or unsigned.")

    target_user_id = user_id if user_id is not None else 0
    current_time = reference_time or datetime.now()
    with closing(get_receipts_connection()) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            receipt_no = generate_receipt_number(connection, current_time, user_id=user_id)
            cursor = connection.execute(
                """
                INSERT INTO receipts (receipt_no, user_id, tenant_name, amount, house_no, month, year, receipt_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_no,
                    target_user_id,
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


def list_numbered_receipts(user_id: int | None = None) -> list[dict[str, Any]]:
    """Return all structured receipts, newest first, optionally filtered by user."""
    with closing(get_receipts_connection()) as connection:
        if user_id is not None:
            rows = connection.execute(
                "SELECT * FROM receipts WHERE user_id = ? OR (user_id = 0 AND ? = 1) ORDER BY created_at DESC, id DESC",
                (user_id, user_id),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM receipts ORDER BY created_at DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def get_numbered_receipt(receipt_no: str, user_id: int | None = None) -> dict[str, Any] | None:
    """Return one structured receipt by its full receipt number, if it exists, optionally scoped to user."""
    with closing(get_receipts_connection()) as connection:
        if user_id is not None:
            row = connection.execute(
                "SELECT * FROM receipts WHERE receipt_no = ? AND (user_id = ? OR (user_id = 0 AND ? = 1))",
                (receipt_no, user_id, user_id),
            ).fetchone()
        else:
            row = connection.execute("SELECT * FROM receipts WHERE receipt_no = ?", (receipt_no,)).fetchone()
    return dict(row) if row else None


def create_receipt(
    *, customer_name: str, shop_name: str, amount: float, receipt_date: str
) -> dict[str, Any]:
    """Insert one receipt and atomically allocate its next sequential bill number."""
    with closing(get_connection()) as connection:
        try:
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
