"""Reusable business logic for bill numbering and optional late charges."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, ROUND_HALF_UP


def generate_next_bill_number(connection: sqlite3.Connection) -> int:
    """Return the next bill number, beginning at 1 for an empty database."""
    row = connection.execute("SELECT COALESCE(MAX(bill_number), 0) + 1 FROM receipts").fetchone()
    return int(row[0])


def calculate_late_fee(
    amount: Decimal | float | str, days_late: int, daily_rate: Decimal | float | str = "0.00"
) -> Decimal:
    """Calculate a simple optional late fee without changing the original receipt amount.

    ``daily_rate`` is a decimal percentage per late day (for example ``0.01`` is 1%).
    """
    if days_late <= 0:
        return Decimal("0.00")

    base_amount = Decimal(str(amount))
    rate = Decimal(str(daily_rate))
    if base_amount < 0 or rate < 0:
        raise ValueError("Amount and daily rate cannot be negative.")

    fee = base_amount * rate * days_late
    return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
