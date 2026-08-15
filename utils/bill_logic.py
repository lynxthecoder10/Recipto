"""Reusable business logic for bill numbering, quarter/month ranges, and billing calculations."""

from __future__ import annotations

import calendar
import sqlite3
from decimal import Decimal, ROUND_HALF_UP

MONTH_NAMES: dict[int, str] = {i: calendar.month_name[i] for i in range(1, 13)}
MONTH_ABBRS: dict[int, str] = {i: calendar.month_abbr[i] for i in range(1, 13)}

MONTH_OPTIONS: list[tuple[int, str]] = [
    (i, calendar.month_name[i]) for i in range(1, 13)
]

QUARTER_LIST: list[str] = ["Q1", "Q2", "Q3", "Q4"]

QUARTERS_INFO: dict[str, dict[str, str | int]] = {
    "Q1": {"code": "Q1", "label": "Jan – Mar (Q1)", "abbr": "Jan – Mar", "start": 1, "end": 3},
    "Q2": {"code": "Q2", "label": "Apr – Jun (Q2)", "abbr": "Apr – Jun", "start": 4, "end": 6},
    "Q3": {"code": "Q3", "label": "Jul – Sep (Q3)", "abbr": "Jul – Sep", "start": 7, "end": 9},
    "Q4": {"code": "Q4", "label": "Oct – Dec (Q4)", "abbr": "Oct – Dec", "start": 10, "end": 12},
}

CYCLE_MONTHS: dict[str, tuple[int, int]] = {
    "C1": (1, 3),
    "C2": (4, 6),
    "C3": (7, 9),
    "C4": (10, 12),
    "Q1": (1, 3),
    "Q2": (4, 6),
    "Q3": (7, 9),
    "Q4": (10, 12),
}


def get_cycle(month: int) -> str:
    """Return the three-month billing cycle that contains ``month``."""
    if not 1 <= month <= 12:
        raise ValueError("Month must be between 1 and 12.")
    return ["Q1", "Q1", "Q1", "Q2", "Q2", "Q2", "Q3", "Q3", "Q3", "Q4", "Q4", "Q4"][month - 1]


def get_cycle_months(cycle: str) -> tuple[int, int]:
    """Return the inclusive start and end months for a billing cycle."""
    try:
        return CYCLE_MONTHS[cycle]
    except KeyError as exc:
        raise ValueError("Invalid cycle specified.") from exc


def get_cycles_between(start_cycle: str, end_cycle: str) -> tuple[str, ...]:
    """Return an inclusive ordered cycle range within one calendar year."""
    cycles = ("C1", "C2", "C3", "C4")
    try:
        start_index = cycles.index(start_cycle)
        end_index = cycles.index(end_cycle)
    except ValueError as exc:
        raise ValueError("Cycle must be C1, C2, C3, or C4.") from exc
    if start_index > end_index:
        raise ValueError("End cycle must not be before the start cycle.")
    return cycles[start_index : end_index + 1]


def parse_quarter_selection(selected_quarters: list[str]) -> tuple[int, int, str]:
    """Validate and convert selected quarter keys (e.g. ['Q1', 'Q2']) into start_month, end_month, cycle_code."""
    if not selected_quarters:
        raise ValueError("Please select at least one quarter cycle.")

    valid_selected = [q.upper().strip() for q in selected_quarters if q.upper().strip() in QUARTER_LIST]
    if not valid_selected:
        raise ValueError("Invalid quarter cycles selected.")

    # Get indices in QUARTER_LIST
    indices = sorted(QUARTER_LIST.index(q) for q in set(valid_selected))
    
    # Check if selected quarters are consecutive
    for i in range(len(indices) - 1):
        if indices[i + 1] != indices[i] + 1:
            raise ValueError("Selected quarters must be consecutive (e.g., Q1+Q2 or Q2+Q3+Q4).")

    start_q = QUARTER_LIST[indices[0]]
    end_q = QUARTER_LIST[indices[-1]]

    start_month = int(QUARTERS_INFO[start_q]["start"])
    end_month = int(QUARTERS_INFO[end_q]["end"])

    if start_q == end_q:
        cycle_code = start_q
    else:
        cycle_code = f"{start_q}-{end_q}"

    return start_month, end_month, cycle_code


def calculate_months_count(start_month: int, end_month: int) -> int:
    """Return the inclusive count of months between start_month and end_month."""
    if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
        raise ValueError("Start and end months must be between 1 and 12.")
    if start_month > end_month:
        raise ValueError("End month cannot be before start month.")
    return (end_month - start_month) + 1


def calculate_total_bill_amount(monthly_rent: float, start_month: int, end_month: int) -> float:
    """Compute Total Amount = Monthly Rent * Number of Months."""
    months = calculate_months_count(start_month, end_month)
    return round(monthly_rent * months, 2)


def format_period_label(start_month: int, end_month: int, year: int, mode: str = "quarter") -> str:
    """Format readable billing period label for UI, WhatsApp, and PDF."""
    s_abbr = MONTH_ABBRS.get(start_month, str(start_month))
    e_abbr = MONTH_ABBRS.get(end_month, str(end_month))
    s_name = MONTH_NAMES.get(start_month, str(start_month))
    e_name = MONTH_NAMES.get(end_month, str(end_month))

    if mode == "custom":
        if start_month == end_month:
            return f"{s_name} {year}"
        return f"{s_name} – {e_name} {year}"
    else:
        if start_month == end_month:
            return f"{s_abbr} {year}"
        return f"{s_abbr} – {e_abbr} {year}"


def get_cycle_code(start_month: int, end_month: int) -> str:
    """Return a cycle string representation for storage and bill numbering."""
    # Check exact quarter matches
    for q_code, q_info in QUARTERS_INFO.items():
        if (start_month, end_month) == (q_info["start"], q_info["end"]):
            return q_code

    if (start_month, end_month) == (1, 6):
        return "Q1-Q2"
    elif (start_month, end_month) == (4, 9):
        return "Q2-Q3"
    elif (start_month, end_month) == (7, 12):
        return "Q3-Q4"
    elif (start_month, end_month) == (1, 9):
        return "Q1-Q3"
    elif (start_month, end_month) == (4, 12):
        return "Q2-Q4"
    elif (start_month, end_month) == (1, 12):
        return "Q1-Q4"
    elif start_month == end_month:
        return f"M{start_month:02d}"
    else:
        return f"M{start_month:02d}-M{end_month:02d}"


def generate_next_bill_number(connection: sqlite3.Connection) -> int:
    """Return the next bill number, beginning at 1 for an empty database."""
    row = connection.execute("SELECT COALESCE(MAX(bill_number), 0) + 1 FROM receipts").fetchone()
    return int(row[0])


def calculate_late_fee(
    amount: Decimal | float | str, days_late: int, daily_rate: Decimal | float | str = "0.00"
) -> Decimal:
    """Calculate a simple optional late fee without changing the original receipt amount."""
    if days_late <= 0:
        return Decimal("0.00")

    base_amount = Decimal(str(amount))
    rate = Decimal(str(daily_rate))
    if base_amount < 0 or rate < 0:
        raise ValueError("Amount and daily rate cannot be negative.")

    fee = base_amount * rate * days_late
    return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def send_whatsapp_bill(pdf_bytes: bytes, phone_number: str) -> None:
    """Placeholder for WhatsApp integration."""
    print(f"[WhatsApp Placeholder] Sending bill PDF ({len(pdf_bytes)} bytes) to {phone_number}...")
