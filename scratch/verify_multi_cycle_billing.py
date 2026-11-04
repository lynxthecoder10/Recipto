"""Comprehensive verification suite for Multi-Cycle Billing, Physical Receipt PDF, and WhatsApp Automation."""

import io
import sys
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import app, _enrich_bill
from seed_demo_data import main as seed_main
from models.db import list_galas, list_bills, get_bill_by_no
from utils.whatsapp_service import WhatsAppService

class TestMultiCycleBilling(unittest.TestCase):

    def setUp(self):
        seed_main()
        self.client = app.test_client()
        app.config["TESTING"] = True
        self.galas = list_galas()
        self.g001 = next(g for g in self.galas if g["gala_number"] == "G001")

    def test_01_multi_cycle_independent_bill_generation(self):
        print("\n--- Test 01: Multi-Cycle Independent Bill Generation ---")
        
        # User selects Q1 + Q2
        res = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1", "Q2"],
            "year": "2026"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "application/zip")
        print(f"  Downloaded Zip Archive ({len(res.data)} bytes)")

        # Verify Zip contents contain two separate PDFs
        with zipfile.ZipFile(io.BytesIO(res.data)) as zf:
            filenames = zf.namelist()
            self.assertEqual(len(filenames), 2)
            self.assertIn("BILL_2026_Q1_0001.pdf", filenames)
            self.assertIn("BILL_2026_Q2_0001.pdf", filenames)
            print(f"  Zip file contains: {filenames} [OK]")

        # Verify SQLite DB has 2 separate, independent records
        bills = list_bills()
        self.assertEqual(len(bills), 2)
        
        q1_bill = next(b for b in bills if b["cycle"] == "Q1")
        q2_bill = next(b for b in bills if b["cycle"] == "Q2")

        self.assertEqual(q1_bill["bill_no"], "BILL/2026/Q1/0001")
        self.assertEqual(q1_bill["amount"], 1800.0)
        self.assertEqual(q1_bill["start_month"], 1)
        self.assertEqual(q1_bill["end_month"], 3)

        self.assertEqual(q2_bill["bill_no"], "BILL/2026/Q2/0001")
        self.assertEqual(q2_bill["amount"], 1800.0)
        self.assertEqual(q2_bill["start_month"], 4)
        self.assertEqual(q2_bill["end_month"], 6)

        print("  SQLite DB Check: Stored Bill 1 (Q1: Rs.1800) and Bill 2 (Q2: Rs.1800) independently [OK]")

    def test_02_independent_duplicate_search(self):
        print("\n--- Test 02: Independent Duplicate Search per Cycle ---")
        
        # Generate Q1 and Q2
        self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1", "Q2"],
            "year": "2026"
        })

        # Search Q1
        res_q1 = self.client.get("/receipts/history?gala_number=G001&billing_type=quarter&quarters=Q1&year=2026")
        self.assertEqual(res_q1.status_code, 200)
        self.assertIn(b"BILL/2026/Q1/0001", res_q1.data)
        self.assertIn(b"Rs. 1800.00", res_q1.data)
        print("  Search Q1: Returned BILL/2026/Q1/0001 (Rs. 1800.00) [OK]")

        # Search Q2
        res_q2 = self.client.get("/receipts/history?gala_number=G001&billing_type=quarter&quarters=Q2&year=2026")
        self.assertEqual(res_q2.status_code, 200)
        self.assertIn(b"BILL/2026/Q2/0001", res_q2.data)
        self.assertIn(b"Rs. 1800.00", res_q2.data)
        print("  Search Q2: Returned BILL/2026/Q2/0001 (Rs. 1800.00) [OK]")

        # Search Q3 (not generated)
        res_q3 = self.client.get("/receipts/history?gala_number=G001&billing_type=quarter&quarters=Q3&year=2026")
        self.assertEqual(res_q3.status_code, 200)
        self.assertIn(b"No bill found matching", res_q3.data)
        print("  Search Q3: Returned 'No bill found' as expected [OK]")

    def test_03_independent_payment_tracking(self):
        print("\n--- Test 03: Independent Payment Tracking per Cycle ---")
        
        # Generate Q1 and Q2
        self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1", "Q2"],
            "year": "2026"
        })

        # Pay Q1 in full (Rs. 1800)
        self.client.post("/billing/payment/BILL/2026/Q1/0001", data={
            "amount_paid": "1800",
            "payment_method": "UPI"
        })

        # Verify Q1 is Full Paid
        b1 = _enrich_bill(get_bill_by_no("BILL/2026/Q1/0001"))
        self.assertEqual(b1["payment_status"], "Full Paid")
        self.assertEqual(b1["pending_amount"], 0.0)

        # Verify Q2 remains Pending
        b2 = _enrich_bill(get_bill_by_no("BILL/2026/Q2/0001"))
        self.assertEqual(b2["payment_status"], "Pending")
        self.assertEqual(b2["pending_amount"], 1800.0)

        print("  Payment Check: Q1 is 'Full Paid' (Pending: 0), Q2 remains 'Pending' (Pending: 1800) [OK]")

    def test_04_whatsapp_automation_formatting(self):
        print("\n--- Test 04: WhatsApp Automation Formatting ---")
        
        bill = {
            "bill_no": "BILL/2026/Q1/0001",
            "tenant_name": "Alice",
            "gala_number": "G001",
            "period_label": "Jan - Mar 2026",
            "num_months": 3,
            "monthly_rent": 600.0,
            "amount": 1800.0,
            "pending_amount": 1800.0,
            "payment_status": "Pending"
        }

        url = WhatsAppService.build_whatsapp_deeplink("8779033522", bill)
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("https://wa.me/918779033522?text="))
        self.assertIn("BILL/2026/Q1/0001", url)
        print("  WhatsApp Deep Link formatting validated [OK]")
        print("  WhatsApp Deep Link formatting validated [OK]")

if __name__ == "__main__":
    unittest.main()
