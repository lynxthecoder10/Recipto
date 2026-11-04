"""Verification suite for Two Billing Modes (Quarter Cycles & Custom Month Range)."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from seed_demo_data import main as seed_main
from models.db import list_galas, list_bills, get_bill_by_no
from utils.bill_logic import parse_quarter_selection, format_period_label

class TestTwoBillingModes(unittest.TestCase):

    def setUp(self):
        seed_main()
        self.client = app.test_client()
        app.config["TESTING"] = True
        self.galas = list_galas()
        self.g001 = next(g for g in self.galas if g["gala_number"] == "G001")

    def test_01_quarter_cycle_parsing_and_consecutive_validation(self):
        print("\n--- Test 01: Quarter Cycle Parsing & Validation ---")
        
        # Q1 (3 mos: 1-3)
        s, e, code = parse_quarter_selection(["Q1"])
        self.assertEqual((s, e, code), (1, 3, "Q1"))
        print("  Q1 -> Start: 1, End: 3, Code: Q1 [OK]")

        # Q1 + Q2 (6 mos: 1-6)
        s, e, code = parse_quarter_selection(["Q1", "Q2"])
        self.assertEqual((s, e, code), (1, 6, "Q1-Q2"))
        print("  Q1+Q2 -> Start: 1, End: 6, Code: Q1-Q2 [OK]")

        # Q2 + Q3 + Q4 (9 mos: 4-12)
        s, e, code = parse_quarter_selection(["Q2", "Q3", "Q4"])
        self.assertEqual((s, e, code), (4, 12, "Q2-Q4"))
        print("  Q2+Q3+Q4 -> Start: 4, End: 12, Code: Q2-Q4 [OK]")

        # Q1 + Q2 + Q3 + Q4 (12 mos: 1-12)
        s, e, code = parse_quarter_selection(["Q1", "Q2", "Q3", "Q4"])
        self.assertEqual((s, e, code), (1, 12, "Q1-Q4"))
        print("  Q1+Q2+Q3+Q4 -> Start: 1, End: 12, Code: Q1-Q4 [OK]")

        # Non-consecutive: Q1 + Q3
        with self.assertRaises(ValueError) as ctx:
            parse_quarter_selection(["Q1", "Q3"])
        self.assertIn("consecutive", str(ctx.exception))
        print(f"  Non-consecutive Q1+Q3 rejected: '{ctx.exception}' [OK]")

        # Empty selection
        with self.assertRaises(ValueError) as ctx:
            parse_quarter_selection([])
        self.assertIn("at least one quarter", str(ctx.exception))
        print(f"  Empty quarter selection rejected: '{ctx.exception}' [OK]")

    def test_02_quarter_mode_bill_generation(self):
        print("\n--- Test 02: Quarter Mode Bill Generation & Calculations ---")
        
        # 1. Single Quarter Q1 (Rs. 1800)
        res1 = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1"],
            "year": "2026"
        })
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.content_type, "application/pdf")
        
        bills = list_bills()
        b1 = next(b for b in bills if b["start_month"] == 1 and b["end_month"] == 3)
        self.assertEqual(b1["amount"], 1800.0)
        self.assertEqual(b1["cycle"], "Q1")
        print("  Quarter Mode Q1: PDF Generated, Stored Total Rs. 1,800.00 [OK]")

        # 2. Multiple Quarters Q2+Q3 (2 separate bills of Rs. 1800 each)
        res2 = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q2", "Q3"],
            "year": "2026"
        })
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.content_type, "application/zip")
        
        bills = list_bills()
        b2_q2 = next(b for b in bills if b["cycle"] == "Q2")
        b2_q3 = next(b for b in bills if b["cycle"] == "Q3")
        self.assertEqual(b2_q2["amount"], 1800.0)
        self.assertEqual(b2_q3["amount"], 1800.0)
        print("  Quarter Mode Q2+Q3: Zip Archive Returned with 2 separate PDFs (Rs 1800 each) [OK]")

    def test_03_custom_mode_bill_generation(self):
        print("\n--- Test 03: Custom Month Range Mode Bill Generation ---")
        
        # February - May (4 months @ Rs 600 -> Rs 2400)
        res = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "custom",
            "start_month": "2",  # February
            "end_month": "5",    # May
            "year": "2026"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "application/pdf")

        bills = list_bills()
        b = next(b for b in bills if b["start_month"] == 2 and b["end_month"] == 5)
        self.assertEqual(b["amount"], 2400.0)
        print("  Custom Mode Feb-May (4 mos): PDF Generated, Stored Total Rs. 2,400.00 [OK]")

    def test_04_duplicate_detection_across_modes(self):
        print("\n--- Test 04: Duplicate Detection & Differentiation ---")
        
        # Generate Q1 (1-3)
        self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1"],
            "year": "2026"
        })

        # Try to generate Q1 again -> Should fail with Duplicate Error
        res_dup = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1"],
            "year": "2026"
        }, follow_redirects=True)
        self.assertIn(b"duplicate bill already exists", res_dup.data)
        print("  Duplicate Check: Re-submitting Q1 rejected with Duplicate Error [OK]")

        # Generate Q2 for same gala & year -> Should succeed as distinct Q2 bill
        res_q2 = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q2"],
            "year": "2026"
        })
        self.assertEqual(res_q2.status_code, 200)
        print("  Differentiation Check: Q2 successfully created as distinct from Q1 [OK]")

    def test_05_duplicate_bill_search_and_backward_compatibility(self):
        print("\n--- Test 05: History Search & Backward Compatibility ---")
        
        # Generate Q1 and Q2
        self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1", "Q2"],
            "year": "2026"
        })

        # Search on duplicate bill page for Q1
        res_q = self.client.get(f"/receipts/history?gala_number=G001&billing_type=quarter&quarters=Q1&year=2026")
        self.assertEqual(res_q.status_code, 200)
        self.assertIn(b"BILL/2026/Q1/0001", res_q.data)
        self.assertIn(b"Rs. 1800.00", res_q.data)
        print("  Quarter Search: Found Q1 bill displaying Rs. 1800.00 [OK]")

        # Search in Custom mode (start=1, end=3)
        res_c = self.client.get(f"/receipts/history?gala_number=G001&billing_type=custom&start_month=1&end_month=3&year=2026")
        self.assertEqual(res_c.status_code, 200)
        self.assertIn(b"Rs. 1800.00", res_c.data)
        print("  Custom Search: Found bill matching (1, 3) period [OK]")

if __name__ == "__main__":
    unittest.main()
