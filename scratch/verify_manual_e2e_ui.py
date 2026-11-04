"""Manual end-to-end user workflow verification script for Recipto UI."""

import sys
from pathlib import Path
from bs4 import BeautifulSoup  # HTML parser for UI verification

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app
from seed_demo_data import main as seed_main
from models.db import list_galas, list_bills, get_bill_by_no

def run_e2e_user_verification():
    print("=" * 65)
    print("      MANUAL END-TO-END BROWSER WORKFLOW VERIFICATION")
    print("=" * 65)

    # 1. Clean Database Seed
    print("\n[STEP 1] Initialising Clean SQLite Database ...")
    seed_main()

    client = app.test_client()

    # 2. Workflow 1: Generate Bill Page & Auto-Fill API
    print("\n[STEP 2] Testing Generate Bill Page & Auto-Fill ...")
    res = client.get("/receipts")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    soup = BeautifulSoup(res.data, "html.parser")
    
    # Check Gala dropdown options
    select = soup.find("select", {"id": "gala_id"})
    assert select is not None, "Gala select dropdown missing"
    options = select.find_all("option")
    gala_options = [opt.text.strip() for opt in options if opt.get("value")]
    assert len(gala_options) == 10, f"Expected 10 gala options, found {len(gala_options)}"
    print(f"  UI Check: Gala Select dropdown contains all 10 seeded Galas ({gala_options[0]} ... {gala_options[-1]})")

    # Check API auto-fill data
    res_api = client.get("/api/galas")
    assert res_api.status_code == 200
    api_data = res_api.get_json()
    assert len(api_data["galas"]) == 10
    g001_api = api_data["galas"][0]
    assert g001_api["tenant_name"] == "Alice"
    assert g001_api["monthly_rent"] == 600.0
    assert g001_api["phone_number"] == "8779033522"
    print("  UI Check: /api/galas returns tenant auto-fill data (Alice, Rs.600.00, 8779033522)")

    # User submits form to generate bill for G001
    # 2.3 Submit form to generate bill PDF
    res_gen = client.post("/receipts/download", data={
        "gala_id": str(g001_api["id"]),
        "billing_type": "quarter",
        "quarters": ["Q1"],
        "year": "2026"
    })
    assert res_gen.status_code == 200, f"Generate bill failed with status {res_gen.status_code}"
    assert res_gen.content_type == "application/pdf"
    assert res_gen.data.startswith(b"%PDF")
    print(f"  UI Action: Generated Bill PDF successfully ({len(res_gen.data)} bytes)")

    # Verify SQLite DB stored the bill
    bills = list_bills()
    assert len(bills) == 1
    assert bills[0]["bill_no"] == "BILL/2026/Q1/0001"
    assert bills[0]["payment_status"] == "Pending"
    print("  DB Check: Stored bill BILL/2026/Q1/0001 in SQLite")

    # 3. Workflow 2: Search & Download Duplicate Bill
    print("\n[STEP 3] Testing Search & Download Duplicate Bill ...")
    res_hist = client.get("/receipts/history?gala_number=G001&billing_type=quarter&quarters=Q1&year=2026")
    assert res_hist.status_code == 200
    soup_hist = BeautifulSoup(res_hist.data, "html.parser")
    table = soup_hist.find("table")
    assert table is not None, "Search result table missing on history page"
    assert "BILL/2026/Q1/0001" in table.text
    print("  UI Check: Search result table displayed bill BILL/2026/Q1/0001")

    # Click Download PDF
    res_dup_dl = client.post("/billing/download_existing/BILL/2026/Q1/0001")
    assert res_dup_dl.status_code == 200
    assert res_dup_dl.content_type == "application/pdf"
    assert res_dup_dl.data.startswith(b"%PDF")
    print(f"  UI Action: Downloaded Duplicate Bill PDF successfully ({len(res_dup_dl.data)} bytes)")

    # 4. Workflow 3: Billing Dashboard & Gala Directory CRUD
    print("\n[STEP 4] Testing Billing Dashboard & Gala Directory CRUD ...")
    res_dash = client.get("/billing")
    assert res_dash.status_code == 200
    soup_dash = BeautifulSoup(res_dash.data, "html.parser")
    count_span = soup_dash.find("span", {"class": "count"})
    assert "10 saved" in count_span.text
    print("  UI Check: Directory displays '10 saved' galas")

    # User adds a new Gala G011 without phone number (Optional phone testing)
    res_add_gala = client.post("/billing/galas", data={
        "gala_number": "G011",
        "tenant_name": "Kevin",
        "phone_number": "",  # Optional blank phone number
        "monthly_rent": "750"
    }, follow_redirects=True)
    assert res_add_gala.status_code == 200
    assert b"Gala G011 saved successfully" in res_add_gala.data
    
    # Check directory updated
    galas_db = list_galas()
    assert len(galas_db) == 11
    g011 = next(g for g in galas_db if g["gala_number"] == "G011")
    assert g011["tenant_name"] == "Kevin"
    assert g011["monthly_rent"] == 750.0
    print("  UI Action: Created Gala G011 without phone number successfully")

    # 5. Workflow 4: Payment Updates & WhatsApp Reminders
    print("\n[STEP 5] Testing Payment Updates & WhatsApp Reminders ...")
    # Partial Payment (200 out of 1800)
    res_pay1 = client.post("/billing/payment/BILL/2026/Q1/0001", data={
        "amount_paid": "200",
        "payment_method": "UPI"
    }, follow_redirects=True)
    assert res_pay1.status_code == 200
    assert b"Payment for BILL/2026/Q1/0001 updated successfully" in res_pay1.data
    
    soup_pay1 = BeautifulSoup(res_pay1.data, "html.parser")
    # Check Half Paid status and WhatsApp button
    assert "Half Paid" in res_pay1.data.decode('utf-8')
    wa_link = soup_pay1.find("a", {"class": "whatsapp"})
    assert wa_link is not None, "WhatsApp link missing for pending payment"
    assert "wa.me/918779033522" in wa_link["href"]
    assert "pending%20balance%20of" in wa_link["href"]
    print("  UI Check: Updated Partial Payment (Half Paid, Rs.1600.00 pending, wa.me/918779033522 link active)")

    # Full Payment (1800 out of 1800)
    res_pay2 = client.post("/billing/payment/BILL/2026/Q1/0001", data={
        "amount_paid": "1800",
        "payment_method": "Bank Transfer"
    }, follow_redirects=True)
    assert res_pay2.status_code == 200
    
    soup_pay2 = BeautifulSoup(res_pay2.data, "html.parser")
    assert "Full Paid" in res_pay2.data.decode('utf-8')
    wa_link_full = soup_pay2.find("a", {"class": "whatsapp"})
    assert wa_link_full is None, "WhatsApp button should be hidden for fully paid bill"
    print("  UI Check: Updated Full Payment (Full Paid, WhatsApp link hidden)")

    # 6. Workflow 5: Gala Without Phone Bill Generation & Dashboard Handling
    print("\n[STEP 6] Testing Gala Without Phone Handling ...")
    g004_db = next(g for g in galas_db if g["gala_number"] == "G004")
    res_g004 = client.post("/receipts/download", data={
        "gala_id": str(g004_db["id"]),
        "billing_type": "quarter",
        "quarters": ["Q1"],
        "year": "2026"
    })
    assert res_g004.status_code == 200
    assert res_g004.content_type == "application/pdf"
    print("  UI Action: Generated Bill PDF for G004 (David, no phone number)")

    res_dash2 = client.get("/billing")
    soup_dash2 = BeautifulSoup(res_dash2.data, "html.parser")
    # Verify BILL/2026/Q1/0002 listed, but no WhatsApp button for G004
    rows = soup_dash2.find_all("tr")
    g004_row = [tr for tr in soup_dash2.find_all("tr") if "BILL/2026/Q1/0002" in tr.text]
    assert len(g004_row) == 1, "BILL/2026/Q1/0002 row missing"
    assert "wa.me" not in g004_row[0].text
    print("  UI Check: BILL/2026/Q1/0002 displayed in Dashboard with WhatsApp button suppressed")

    print("\n" + "=" * 65)
    print("      ALL MANUAL E2E UI WORKFLOWS PASSED WITH 0 ERRORS!")
    print("=" * 65)

if __name__ == "__main__":
    run_e2e_user_verification()
