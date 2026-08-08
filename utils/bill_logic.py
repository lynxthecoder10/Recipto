"""Reusable business logic for bill numbering and optional late charges."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, ROUND_HALF_UP


CYCLE_MONTHS: dict[str, tuple[int, int]] = {
    "C1": (1, 3),
    "C2": (4, 6),
    "C3": (7, 9),
    "C4": (10, 12),
}


def get_cycle(month: int) -> str:
    """Return the three-month billing cycle that contains ``month``."""
    if not 1 <= month <= 12:
        raise ValueError("Month must be between 1 and 12.")
    return [
        "C1", "C1", "C1", "C2", "C2", "C2",
        "C3", "C3", "C3", "C4", "C4", "C4",
    ][month - 1]


def get_cycle_months(cycle: str) -> tuple[int, int]:
    """Return the inclusive start and end months for a billing cycle."""
    try:
        return CYCLE_MONTHS[cycle]
    except KeyError as exc:
        raise ValueError("Cycle must be C1, C2, C3, or C4.") from exc


def get_cycles_between(start_cycle: str, end_cycle: str) -> tuple[str, ...]:
    """Return an inclusive ordered cycle range within one calendar year."""
    cycles = tuple(CYCLE_MONTHS)
    try:
        start_index = cycles.index(start_cycle)
        end_index = cycles.index(end_cycle)
    except ValueError as exc:
        raise ValueError("Cycle must be C1, C2, C3, or C4.") from exc
    if start_index > end_index:
        raise ValueError("End cycle must not be before the start cycle.")
    return cycles[start_index : end_index + 1]


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
