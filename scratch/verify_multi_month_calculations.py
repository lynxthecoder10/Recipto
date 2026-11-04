"""Verification script for Multi-Month Rent Calculation & UI Month Range Selection."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from seed_demo_data import main as seed_main
from models.db import list_galas, list_bills, get_bill_by_no, get_receipts_connection
from utils.bill_logic import calculate_months_count, calculate_total_bill_amount

class TestMultiMonthCalculations(unittest.TestCase):

    def setUp(self):
        seed_main()
        self.client = app.test_client()
        app.config["TESTING"] = True
        self.galas = list_galas()
        self.g001 = next(g for g in self.galas if g["gala_number"] == "G001")

    def test_01_period_calculations_formula(self):
        print("\n--- Test 01: Month Range & Bill Calculation Formulas ---")
        
        # 1. Single month (Jan - Jan: 1 month)
        self.assertEqual(calculate_months_count(1, 1), 1)
        self.assertEqual(calculate_total_bill_amount(600.0, 1, 1), 600.0)
        print("  Jan-Jan (1 month) @ Rs.600 -> Total Rs.600.00 [OK]")

        # 2. Jan - Mar (3 months)
        self.assertEqual(calculate_months_count(1, 3), 3)
        self.assertEqual(calculate_total_bill_amount(600.0, 1, 3), 1800.0)
        print("  Jan-Mar (3 months) @ Rs.600 -> Total Rs.1,800.00 [OK]")

        # 3. Apr - Jun (3 months)
        self.assertEqual(calculate_months_count(4, 6), 3)
        self.assertEqual(calculate_total_bill_amount(600.0, 4, 6), 1800.0)
        print("  Apr-Jun (3 months) @ Rs.600 -> Total Rs.1,800.00 [OK]")

        # 4. Jan - Jun (6 months)
        self.assertEqual(calculate_months_count(1, 6), 6)
        self.assertEqual(calculate_total_bill_amount(600.0, 1, 6), 3600.0)
        print("  Jan-Jun (6 months) @ Rs.600 -> Total Rs.3,600.00 [OK]")

        # 5. Jan - Dec (12 months)
        self.assertEqual(calculate_months_count(1, 12), 12)
        self.assertEqual(calculate_total_bill_amount(600.0, 1, 12), 7200.0)
        print("  Jan-Dec (12 months) @ Rs.600 -> Total Rs.7,200.00 [OK]")

    def test_02_bill_generation_6_months(self):
        print("\n--- Test 02: 6-Month Bill Generation (Jan-Jun: Rs.3600) ---")
        res = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "start_month": "1",  # January
            "end_month": "6",    # June
            "year": "2026"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF"))
        print(f"  Downloaded PDF ({len(res.data)} bytes)")

        # Verify SQLite storage
        bills = list_bills()
        b = next(bill for bill in bills if bill["gala_id"] == self.g001["id"] and bill["start_month"] == 1 and bill["end_month"] == 6)
        self.assertEqual(b["amount"], 3600.0, f"Expected 3600.0, stored {b['amount']}")
        self.assertEqual(b["start_month"], 1)
        self.assertEqual(b["end_month"], 6)
        print(f"  SQLite Check: Stored Total Amount = Rs. {b['amount']:.2f} for period {b['start_month']}-{b['end_month']} [OK]")

    def test_03_bill_generation_12_months(self):
        print("\n--- Test 03: 12-Month Bill Generation (Jan-Dec: Rs.7200) ---")
        res = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "start_month": "1",   # January
            "end_month": "12",    # December
            "year": "2026"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "application/pdf")

        # Verify SQLite storage
        bills = list_bills()
        b = next(bill for bill in bills if bill["gala_id"] == self.g001["id"] and bill["start_month"] == 1 and bill["end_month"] == 12)
        self.assertEqual(b["amount"], 7200.0, f"Expected 7200.0, stored {b['amount']}")
        print(f"  SQLite Check: Stored Total Amount = Rs. {b['amount']:.2f} [OK]")

    def test_04_payment_status_from_total_amount(self):
        print("\n--- Test 04: Payment Status & Pending Calculation ---")
        # Create 6-month bill (Jan-Jun: Rs. 3600)
        self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "start_month": "1",
            "end_month": "6",
            "year": "2026"
        })
        bills = list_bills()
        bill_no = bills[0]["bill_no"]

        # 1. Initial Pending State
        b = get_bill_by_no(bill_no)
        self.assertEqual(b["amount"], 3600.0)
        
        res_dash = self.client.get("/billing")
        self.assertIn(b"Rs. 3600.00", res_dash.data)
        print(f"  Dashboard Check: Initial Total Amount displayed as Rs. 3600.00 [OK]")

        # 2. Partial Payment (1000 out of 3600 -> Pending = 2600)
        self.client.post(f"/billing/payment/{bill_no}", data={
            "amount_paid": "1000",
            "payment_method": "UPI"
        })
        b = get_bill_by_no(bill_no)
        self.assertEqual(b["payment_status"], "Half Paid")
        self.assertEqual(b["amount_paid"], 1000.0)
        self.assertEqual(b["pending_amount"], 2600.0)
        print(f"  Partial Payment Check: Paid Rs.1000 -> Status: Half Paid, Pending: Rs. {b['pending_amount']:.2f} [OK]")

        # 3. Full Payment (3600 out of 3600 -> Pending = 0)
        self.client.post(f"/billing/payment/{bill_no}", data={
            "amount_paid": "3600",
            "payment_method": "Bank Transfer"
        })
        b = get_bill_by_no(bill_no)
        self.assertEqual(b["payment_status"], "Full Paid")
        self.assertEqual(b["amount_paid"], 3600.0)
        self.assertEqual(b["pending_amount"], 0.0)
        print(f"  Full Payment Check: Paid Rs.3600 -> Status: Full Paid, Pending: Rs. {b['pending_amount']:.2f} [OK]")

    def test_05_duplicate_bill_search_and_redownload(self):
        print("\n--- Test 05: Duplicate Bill Search & Redownload ---")
        # Create 3-month bill (Jan-Mar: Rs. 1800)
        self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "start_month": "1",
            "end_month": "3",
            "year": "2026"
        })
        bills = list_bills()
        bill_no = bills[0]["bill_no"]

        # Search on duplicate bill page
        res = self.client.get(f"/receipts/history?gala_number=G001&start_month=1&end_month=3&year=2026")
        self.assertEqual(res.status_code, 200)
        self.assertIn(bill_no.encode('utf-8'), res.data)
        self.assertIn(b"Rs. 1800.00", res.data)
        print("  Duplicate Search Check: Bill found on history page with Total Amount Rs. 1800.00 [OK]")

        # Download existing PDF
        res_dl = self.client.post(f"/billing/download_existing/{bill_no}")
        self.assertEqual(res_dl.status_code, 200)
        self.assertEqual(res_dl.content_type, "application/pdf")
        print("  Duplicate Download Check: Redownloaded corrected PDF bill [OK]")

if __name__ == "__main__":
    unittest.main()
