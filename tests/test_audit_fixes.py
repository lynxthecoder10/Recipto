"""Comprehensive automated test suite validating all audit and remediation fixes."""

import io
import pytest
from app import app
from models.db import (
    create_bill,
    create_or_update_gala,
    create_user,
    get_bill_by_no,
    get_gala_by_id,
    get_user_by_email,
    init_billing_store,
    init_receipt_store,
    list_bills,
    list_galas,
    update_bill_payment_status,
)
from werkzeug.security import generate_password_hash


@pytest.fixture(autouse=True)
def setup_db():
    """Ensure database stores are ready before each test."""
    init_receipt_store()
    init_billing_store()


@pytest.fixture
def client():
    """Test client with testing mode enabled."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client


# ===========================================================================
# 1. Authentication & Session Tests
# ===========================================================================

def test_unauthenticated_routes_redirect_to_login(client):
    """Verify all protected routes redirect unauthenticated visitors to login."""
    protected_urls = [
        "/",
        "/galas",
        "/billing",
        "/payments",
        "/history",
        "/reports",
        "/settings",
    ]
    for url in protected_urls:
        res = client.get(url)
        assert res.status_code == 302
        assert "/login" in res.headers["Location"]


def test_user_signup_and_login_flow(client):
    """Verify signup with password hashing, login, and session persistence."""
    import time
    unique_email = f"owner_{int(time.time() * 1000)}@recipto.com"

    # 1. Successful Signup
    res = client.post(
        "/signup",
        data={
            "name": "Industrial Owner",
            "email": unique_email,
            "password": "SecurePassword123",
            "confirm_password": "SecurePassword123",
            "role": "owner",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert b"Welcome to Recipto" in res.data or b"Dashboard" in res.data

    # Verify user saved in DB with hashed password
    user = get_user_by_email(unique_email)
    assert user is not None
    assert user["name"] == "Industrial Owner"
    assert user["password_hash"] != "SecurePassword123"
    assert user["password_hash"].startswith("scrypt:") or user["password_hash"].startswith("pbkdf2:")

    # 2. Duplicate Signup Rejected
    res_dup = client.post(
        "/signup",
        data={
            "name": "Another Name",
            "email": unique_email,
            "password": "SecurePassword123",
            "confirm_password": "SecurePassword123",
        },
        follow_redirects=True,
    )
    assert b"already exists" in res_dup.data

    # 3. Logout
    res_logout = client.get("/logout", follow_redirects=True)
    assert b"signed out" in res_logout.data

    # 4. Login with Wrong Password fails
    res_wrong = client.post(
        "/login",
        data={"email": unique_email, "password": "WrongPassword!"},
        follow_redirects=True,
    )
    assert b"Invalid email or password" in res_wrong.data

    # 5. Login with Correct Password succeeds
    res_login = client.post(
        "/login",
        data={"email": unique_email, "password": "SecurePassword123"},
        follow_redirects=True,
    )
    assert res_login.status_code == 200
    assert b"Welcome back" in res_login.data
    assert b"Dashboard Overview" in res_data if (res_data := res_login.data) else True


# ===========================================================================
# 2. Multi-Tenant Data Scoping Tests
# ===========================================================================

def test_data_scoping_between_users(client):
    """Ensure User A cannot see or modify User B's properties or billing records."""
    import time
    email_a = f"usera_{int(time.time() * 1000)}@test.com"
    email_b = f"userb_{int(time.time() * 1000)}@test.com"

    # Register User A
    client.post(
        "/signup",
        data={
            "name": "Owner A",
            "email": email_a,
            "password": "PasswordA123",
            "confirm_password": "PasswordA123",
        },
    )
    user_a = get_user_by_email(email_a)

    # User A creates Gala A1
    gala_a = create_or_update_gala(
        gala_number="G-A1",
        tenant_name="Tenant Alpha",
        phone_number="9876543210",
        monthly_rent=1500.0,
        user_id=user_a["id"],
    )

    # Register User B
    client.get("/logout")
    client.post(
        "/signup",
        data={
            "name": "Owner B",
            "email": email_b,
            "password": "PasswordB123",
            "confirm_password": "PasswordB123",
        },
    )
    user_b = get_user_by_email(email_b)

    # User B creates Gala B1
    gala_b = create_or_update_gala(
        gala_number="G-B1",
        tenant_name="Tenant Beta",
        phone_number="9876543211",
        monthly_rent=2500.0,
        user_id=user_b["id"],
    )

    # Scoping check 1: List Galas per user
    galas_for_a = list_galas(user_id=user_a["id"])
    gala_numbers_a = [g["gala_number"] for g in galas_for_a]
    assert "G-A1" in gala_numbers_a
    assert "G-B1" not in gala_numbers_a

    galas_for_b = list_galas(user_id=user_b["id"])
    gala_numbers_b = [g["gala_number"] for g in galas_for_b]
    assert "G-B1" in gala_numbers_b
    assert "G-A1" not in gala_numbers_b

    # Scoping check 2: User B attempting to fetch Gala A via get_gala_by_id
    assert get_gala_by_id(gala_a["id"], user_id=user_b["id"]) is None
    assert get_gala_by_id(gala_b["id"], user_id=user_b["id"]) is not None


# ===========================================================================
# 3. Payment Status Normalization & Calculation Tests
# ===========================================================================

def test_payment_status_lifecycle_and_normalization():
    """Verify bills transition from 'pending' -> 'partial' -> 'paid' accurately."""
    import time
    email = f"paytest_{int(time.time() * 1000)}@test.com"
    pwd_hash = generate_password_hash("Pass12345")
    user = create_user(email=email, password_hash=pwd_hash, name="Payment Tester")

    gala = create_or_update_gala(
        gala_number="G-PAY1",
        tenant_name="Payment Tenant",
        phone_number="9998887770",
        monthly_rent=500.0,
        user_id=user["id"],
    )

    # 1. Create a 3-month bill (Q1) = Rs. 1500.00
    bill = create_bill(
        gala_id=gala["id"],
        year=2026,
        start_month=1,
        end_month=3,
        monthly_rent=500.0,
        billing_type="quarter",
        cycle_code="Q1",
        user_id=user["id"],
    )
    assert bill["amount"] == 1500.0
    assert bill["payment_status"] == "pending"
    assert bill["pending_amount"] == 1500.0

    # 2. Record Partial Payment: Rs. 500.00
    updated_partial = update_bill_payment_status(
        bill_no=bill["bill_no"],
        amount_paid=500.0,
        payment_method="UPI",
        user_id=user["id"],
    )
    assert updated_partial["payment_status"] == "partial"
    assert updated_partial["amount_paid"] == 500.0
    assert updated_partial["pending_amount"] == 1000.0

    # 3. Record Full Payment: Total Rs. 1500.00
    updated_full = update_bill_payment_status(
        bill_no=bill["bill_no"],
        amount_paid=1500.0,
        payment_method="Bank",
        user_id=user["id"],
    )
    assert updated_full["payment_status"] == "paid"
    assert updated_full["amount_paid"] == 1500.0
    assert updated_full["pending_amount"] == 0.0


# ===========================================================================
# 4. Input Validation Tests
# ===========================================================================

def test_save_gala_validation(client):
    """Verify negative rent is rejected."""
    import time
    email = f"val_{int(time.time() * 1000)}@test.com"
    client.post(
        "/signup",
        data={
            "name": "Val Owner",
            "email": email,
            "password": "Pass12345!",
            "confirm_password": "Pass12345!",
        },
    )

    # Attempt saving gala with negative rent
    res = client.post(
        "/galas",
        data={
            "gala_number": "G-BAD",
            "tenant_name": "Test Tenant",
            "phone_number": "1234567890",
            "monthly_rent": "-500",
        },
        follow_redirects=True,
    )
    assert b"cannot be negative" in res.data or b"error" in res.data


def test_payment_negative_amount_rejected(client):
    """Verify negative amount paid is rejected in payment endpoint."""
    import time
    email = f"payval_{int(time.time() * 1000)}@test.com"
    client.post(
        "/signup",
        data={
            "name": "Pay Owner",
            "email": email,
            "password": "Pass12345!",
            "confirm_password": "Pass12345!",
        },
    )

    res = client.post(
        "/payments/update/BILL/2026/Q1/0001",
        data={"amount_paid": "-100", "payment_method": "Cash"},
        follow_redirects=True,
    )
    assert b"cannot be negative" in res.data or b"error" in res.data
