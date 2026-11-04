"""Unit and integration tests for per-user identifier uniqueness and auto-generation.

Verifies:
1. User 1 and User 2 can both have the same gala number without collision.
2. A single user cannot create duplicate gala numbers (updates existing instead).
3. User 1 and User 2 can both have the same bill number (bill_no) without collision.
4. A single user cannot insert duplicate bills.
5. User 1 and User 2 can both have the same receipt number (receipt_no).
6. Auto-generation of bill numbers starts at 0001 independently for each user.
7. Auto-generation of receipt numbers starts at 0001 independently for each user.
8. Lookups and updates by identifier correctly isolate to the authenticated user.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from app import app
from models.db import (
    BillingDuplicateError,
    BillingStorageError,
    ReceiptStorageError,
    create_bill,
    create_bills_for_cycles,
    create_numbered_receipt,
    create_or_update_gala,
    create_user,
    get_bill_by_no,
    get_gala_by_id,
    get_numbered_receipt,
    get_receipts_connection,
    init_billing_store,
    init_receipt_store,
    list_numbered_receipts,
    update_bill_payment_status,
    update_bill_whatsapp_status,
)


@pytest.fixture(autouse=True)
def setup_stores():
    init_receipt_store()
    init_billing_store()


@pytest.fixture
def two_users():
    """Create two distinct users for multi-tenant isolation testing."""
    ts = int(datetime.now().timestamp() * 1000)
    u1 = create_user(
        email=f"owner1_{ts}@test.com",
        password_hash="dummy_hash_1",
        name="User One",
        role="owner",
    )
    u2 = create_user(
        email=f"owner2_{ts}@test.com",
        password_hash="dummy_hash_2",
        name="User Two",
        role="owner",
    )
    return u1["id"], u2["id"]


def test_gala_number_unique_per_user(two_users):
    """User 1 and User 2 can each have gala 'G100' without conflict."""
    u1_id, u2_id = two_users

    # User 1 creates G100
    gala1 = create_or_update_gala(
        gala_number="G100",
        tenant_name="Tenant 1",
        phone_number="9876543210",
        monthly_rent=15000.0,
        user_id=u1_id,
    )
    assert gala1["gala_number"] == "G100"
    assert gala1["user_id"] == u1_id

    # User 2 creates G100 - must succeed without conflict
    gala2 = create_or_update_gala(
        gala_number="G100",
        tenant_name="Tenant 2",
        phone_number="9123456780",
        monthly_rent=20000.0,
        user_id=u2_id,
    )
    assert gala2["gala_number"] == "G100"
    assert gala2["user_id"] == u2_id
    assert gala1["id"] != gala2["id"]

    # Updating User 1's G100 updates existing without creating duplicate
    gala1_updated = create_or_update_gala(
        gala_number="G100",
        tenant_name="Tenant 1 Modified",
        phone_number="9876543210",
        monthly_rent=18000.0,
        user_id=u1_id,
    )
    assert gala1_updated["id"] == gala1["id"]
    assert gala1_updated["tenant_name"] == "Tenant 1 Modified"

    # User 2's G100 remains unchanged
    gala2_check = get_gala_by_id(gala2["id"], user_id=u2_id)
    assert gala2_check["tenant_name"] == "Tenant 2"
    assert gala2_check["monthly_rent"] == 20000.0


def test_bill_number_auto_generation_independent_sequences(two_users):
    """Auto-generated bill numbers start at 0001 for each user independently."""
    u1_id, u2_id = two_users

    # Create galas
    g1 = create_or_update_gala(
        gala_number="G201",
        tenant_name="Tenant A",
        phone_number="9876500001",
        monthly_rent=10000.0,
        user_id=u1_id,
    )
    g2 = create_or_update_gala(
        gala_number="G201",
        tenant_name="Tenant B",
        phone_number="9876500002",
        monthly_rent=12000.0,
        user_id=u2_id,
    )

    year = 2028
    # User 1 creates first bill for 2028 Q1
    b1 = create_bill(
        gala_id=g1["id"],
        year=year,
        start_month=1,
        end_month=3,
        monthly_rent=10000.0,
        billing_type="quarter",
        cycle_code="Q1",
        user_id=u1_id,
    )
    assert b1["bill_no"] == f"BILL/{year}/Q1/0001"
    assert b1["user_id"] == u1_id

    # User 2 creates first bill for 2028 Q1 -> MUST also start at 0001 independently!
    b2 = create_bill(
        gala_id=g2["id"],
        year=year,
        start_month=1,
        end_month=3,
        monthly_rent=12000.0,
        billing_type="quarter",
        cycle_code="Q1",
        user_id=u2_id,
    )
    assert b2["bill_no"] == f"BILL/{year}/Q1/0001"
    assert b2["user_id"] == u2_id
    assert b1["id"] != b2["id"]

    # User 1 creates second bill in same cycle for another gala -> sequence becomes 0002
    g1_b = create_or_update_gala(
        gala_number="G202",
        tenant_name="Tenant A2",
        phone_number="9876500003",
        monthly_rent=8000.0,
        user_id=u1_id,
    )
    b1_2 = create_bill(
        gala_id=g1_b["id"],
        year=year,
        start_month=1,
        end_month=3,
        monthly_rent=8000.0,
        billing_type="quarter",
        cycle_code="Q1",
        user_id=u1_id,
    )
    assert b1_2["bill_no"] == f"BILL/{year}/Q1/0002"


def test_bill_lookup_and_update_isolation(two_users):
    """Looking up and updating a bill by bill_no strictly targets the authenticated user's record."""
    u1_id, u2_id = two_users

    g1 = create_or_update_gala(
        gala_number="G301",
        tenant_name="Tenant X",
        phone_number="9876511111",
        monthly_rent=10000.0,
        user_id=u1_id,
    )
    g2 = create_or_update_gala(
        gala_number="G301",
        tenant_name="Tenant Y",
        phone_number="9876522222",
        monthly_rent=14000.0,
        user_id=u2_id,
    )

    year = 2029
    b1 = create_bill(
        gala_id=g1["id"],
        year=year,
        start_month=4,
        end_month=6,
        monthly_rent=10000.0,
        billing_type="quarter",
        cycle_code="Q2",
        user_id=u1_id,
    )
    b2 = create_bill(
        gala_id=g2["id"],
        year=year,
        start_month=4,
        end_month=6,
        monthly_rent=14000.0,
        billing_type="quarter",
        cycle_code="Q2",
        user_id=u2_id,
    )
    shared_bill_no = b1["bill_no"]
    assert shared_bill_no == b2["bill_no"]

    # Lookup isolation
    found_u1 = get_bill_by_no(shared_bill_no, user_id=u1_id)
    assert found_u1 is not None
    assert found_u1["id"] == b1["id"]
    assert found_u1["tenant_name"] == "Tenant X"

    found_u2 = get_bill_by_no(shared_bill_no, user_id=u2_id)
    assert found_u2 is not None
    assert found_u2["id"] == b2["id"]
    assert found_u2["tenant_name"] == "Tenant Y"

    # Payment update isolation: pay User 1's bill
    res = update_bill_payment_status(
        bill_no=shared_bill_no,
        amount_paid=30000.0,
        payment_method="UPI",
        user_id=u1_id,
    )
    assert res is not None
    assert res["id"] == b1["id"]
    assert res["payment_status"] == "paid"
    assert res["amount_paid"] == 30000.0

    # Verify User 2's bill with identical bill_no was completely untouched!
    b2_check = get_bill_by_no(shared_bill_no, user_id=u2_id)
    assert b2_check["payment_status"] == "pending"
    assert b2_check["amount_paid"] == 0.0

    # WhatsApp update isolation
    update_bill_whatsapp_status(shared_bill_no, "Sent", user_id=u1_id)
    b1_check = get_bill_by_no(shared_bill_no, user_id=u1_id)
    assert b1_check["whatsapp_status"] == "Sent"

    b2_check = get_bill_by_no(shared_bill_no, user_id=u2_id)
    assert b2_check["whatsapp_status"] == "Pending"


def test_single_user_cannot_create_duplicate_bill(two_users):
    """A single user cannot create a duplicate bill for the exact same gala and period."""
    u1_id, _ = two_users

    g1 = create_or_update_gala(
        gala_number="G401",
        tenant_name="Tenant Dup",
        phone_number="9876533333",
        monthly_rent=5000.0,
        user_id=u1_id,
    )

    create_bill(
        gala_id=g1["id"],
        year=2030,
        start_month=1,
        end_month=3,
        monthly_rent=5000.0,
        billing_type="quarter",
        cycle_code="Q1",
        user_id=u1_id,
    )

    with pytest.raises(BillingDuplicateError):
        create_bill(
            gala_id=g1["id"],
            year=2030,
            start_month=1,
            end_month=3,
            monthly_rent=5000.0,
            billing_type="quarter",
            cycle_code="Q1",
            user_id=u1_id,
        )


def test_receipt_number_unique_per_user_and_independent_sequences(two_users):
    """User 1 and User 2 have independent receipt sequences starting at 0001, both with RCPT/.../0001."""
    u1_id, u2_id = two_users
    fixed_time = datetime(2031, 5, 15, 10, 0, 0)

    # User 1 creates first receipt
    r1 = create_numbered_receipt(
        tenant_name="Tenant R1",
        amount=5000.0,
        house_no="G501",
        receipt_type="signed",
        reference_time=fixed_time,
        user_id=u1_id,
    )
    assert r1["receipt_no"] == "RCPT/2031/05/0001"
    assert r1["user_id"] == u1_id

    # User 2 creates first receipt in same month -> independent sequence starts at 0001!
    r2 = create_numbered_receipt(
        tenant_name="Tenant R2",
        amount=7000.0,
        house_no="G501",
        receipt_type="signed",
        reference_time=fixed_time,
        user_id=u2_id,
    )
    assert r2["receipt_no"] == "RCPT/2031/05/0001"
    assert r2["user_id"] == u2_id
    assert r1["id"] != r2["id"]

    # User 1 creates second receipt in same month -> sequence 0002
    r1_2 = create_numbered_receipt(
        tenant_name="Tenant R1 Second",
        amount=5000.0,
        house_no="G502",
        receipt_type="unsigned",
        reference_time=fixed_time,
        user_id=u1_id,
    )
    assert r1_2["receipt_no"] == "RCPT/2031/05/0002"

    # User 2 list receipts shows only User 2's receipts
    u2_receipts = list_numbered_receipts(user_id=u2_id)
    assert len(u2_receipts) == 1
    assert u2_receipts[0]["id"] == r2["id"]

    # Get receipt by number scoped to user
    shared_receipt_no = "RCPT/2031/05/0001"
    lookup_u1 = get_numbered_receipt(shared_receipt_no, user_id=u1_id)
    assert lookup_u1["tenant_name"] == "Tenant R1"

    lookup_u2 = get_numbered_receipt(shared_receipt_no, user_id=u2_id)
    assert lookup_u2["tenant_name"] == "Tenant R2"


def test_direct_sql_uniqueness_constraint(two_users):
    """Verify database schema constraint: inserting same (bill_no, user_id) raises IntegrityError."""
    u1_id, u2_id = two_users
    conn = get_receipts_connection()

    # User 1 and User 2 can both have same receipt_no
    conn.execute(
        "INSERT INTO receipts (receipt_no, user_id, tenant_name, amount, house_no, month, year, receipt_type) "
        "VALUES ('RCPT/TEST/001', ?, 'T1', 100, 'H1', '01', 2026, 'signed')",
        (u1_id,),
    )
    conn.execute(
        "INSERT INTO receipts (receipt_no, user_id, tenant_name, amount, house_no, month, year, receipt_type) "
        "VALUES ('RCPT/TEST/001', ?, 'T2', 200, 'H2', '01', 2026, 'signed')",
        (u2_id,),
    )
    conn.commit()

    # But inserting duplicate for the same user must raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO receipts (receipt_no, user_id, tenant_name, amount, house_no, month, year, receipt_type) "
            "VALUES ('RCPT/TEST/001', ?, 'T3', 300, 'H3', '01', 2026, 'signed')",
            (u1_id,),
        )
    conn.close()
