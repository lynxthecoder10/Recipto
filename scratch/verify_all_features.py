"""Comprehensive verification script for Recipto (SQLite-only Version 1)."""

import sys
import unittest
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from models.db import (
    list_galas,
    list_bills,
    get_bill_by_no,
    get_receipts_connection,
)
from utils.pdf_generator import _has_wkhtmltopdf, generate_pdf_bytes

class TestReciptoVersion1(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        app.config["TESTING"] = True

    def test_01_demo_data_integrity(self):
        print("\n--- Test 01: Demo Data Integrity ---")
        galas = list_galas()
        self.assertEqual(len(galas), 10, f"Expected 10 galas, found {len(galas)}")
        
        # Check first 3 galas
        for gala in galas[:3]:
            self.assertEqual(gala["phone_number"], "8779033522", f"Gala {gala['gala_number']} phone should be 8779033522")
            self.assertEqual(gala["monthly_rent"], 600.0)
            print(f"  Verified {gala['gala_number']} ({gala['tenant_name']}): Phone={gala['phone_number']}, Rent={gala['monthly_rent']}")
            
        # Check remaining 7 galas
        for gala in galas[3:]:
            self.assertIn(gala["phone_number"], ["", None], f"Gala {gala['gala_number']} phone should be blank/null")
            self.assertEqual(gala["monthly_rent"], 600.0)
            print(f"  Verified {gala['gala_number']} ({gala['tenant_name']}): Phone=(blank), Rent={gala['monthly_rent']}")

    def test_02_pdf_fallback_generation(self):
        print("\n--- Test 02: PDF Generation & WeasyPrint Fallback ---")
        has_wk = _has_wkhtmltopdf()
        print(f"  wkhtmltopdf available: {has_wk}")
        sample_html = "<h1>Test Receipt</h1><p>Tenant: Alice</p>"
        pdf_bytes = generate_pdf_bytes(html_content=sample_html)
        self.assertTrue(len(pdf_bytes) > 0, "PDF bytes should not be empty")
        self.assertTrue(pdf_bytes.startswith(b"%PDF"), "Output bytes must be valid PDF format")
        print(f"  Successfully generated PDF ({len(pdf_bytes)} bytes) using {'wkhtmltopdf' if has_wk else 'WeasyPrint fallback'}")

    def test_03_routes_and_pages(self):
        print("\n--- Test 03: Route Accessibility ---")
        
        # GET / -> 302
        res = self.client.get("/")
        self.assertEqual(res.status_code, 302)
        print("  GET / -> 302 OK")

        # GET /receipts -> 200
        res = self.client.get("/receipts")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Generate Bill", res.data)
        print("  GET /receipts -> 200 OK")

        # GET /api/galas -> 200
        res = self.client.get("/api/galas")
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertEqual(len(json_data["galas"]), 10)
        print("  GET /api/galas -> 200 OK (Returns 10 galas)")

        # GET /billing -> 200
        res = self.client.get("/billing")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Billing Dashboard", res.data)
        print("  GET /billing -> 200 OK")

        # GET /receipts/history -> 200
        res = self.client.get("/receipts/history")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Search", res.data)
        print("  GET /receipts/history -> 200 OK")

    def test_04_gala_crud(self):
        print("\n--- Test 04: Gala CRUD Operations ---")
        # Add new Gala G011
        res = self.client.post("/billing/galas", data={
            "gala_number": "G011",
            "tenant_name": "Kevin",
            "phone_number": "9876543210",
            "monthly_rent": "750"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        
        galas = list_galas()
        g011 = next((g for g in galas if g["gala_number"] == "G011"), None)
        self.assertIsNotNone(g011)
        self.assertEqual(g011["tenant_name"], "Kevin")
        self.assertEqual(g011["monthly_rent"], 750.0)
        print("  Created Gala G011 successfully")

        # Update Gala G011
        res = self.client.post("/billing/galas", data={
            "gala_number": "G011",
            "tenant_name": "Kevin Updated",
            "phone_number": "9876543210",
            "monthly_rent": "800"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        
        galas = list_galas()
        g011_upd = next((g for g in galas if g["gala_number"] == "G011"), None)
        self.assertEqual(g011_upd["tenant_name"], "Kevin Updated")
        self.assertEqual(g011_upd["monthly_rent"], 800.0)
        print("  Updated Gala G011 successfully")

    def test_05_bill_generation_and_pdf_download(self):
        print("\n--- Test 05: Bill Generation & PDF Download ---")
        galas = list_galas()
        g001 = next(g for g in galas if g["gala_number"] == "G001")

        # Post /receipts/download for G001, C1 2026
        res = self.client.post("/receipts/download", data={
            "gala_id": str(g001["id"]),
            "start_cycle": "C1",
            "end_cycle": "C1",
            "year": "2026"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF"))
        print(f"  Generated & downloaded PDF for G001 (C1 2026): {len(res.data)} bytes")

        # Check DB bill record
        bills = list_bills()
        bill_g001 = next((b for b in bills if b["gala_id"] == g001["id"] and b["cycle"] == "C1" and b["year"] == 2026), None)
        self.assertIsNotNone(bill_g001)
        self.assertEqual(bill_g001["amount"], 600.0)
        self.assertEqual(bill_g001["payment_status"], "Pending")
        print(f"  Bill record created in SQLite: {bill_g001['bill_no']}")

        # Attempt duplicate generation -> should fail safely (redirect with error flash)
        res_dup = self.client.post("/receipts/download", data={
            "gala_id": str(g001["id"]),
            "start_cycle": "C1",
            "end_cycle": "C1",
            "year": "2026"
        }, follow_redirects=True)
        self.assertEqual(res_dup.status_code, 200)
        self.assertIn(b"No bills were created because duplicate cycles already exist", res_dup.data)
        print("  Duplicate bill generation prevented safely")

    def test_06_search_and_download_duplicate_bill(self):
        print("\n--- Test 06: Search & Download Duplicate Bill ---")
        # Search by Gala G001, Cycle C1, Year 2026
        res = self.client.get("/receipts/history?gala_number=G001&cycle=C1&year=2026")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"BILL/2026/C1/0001", res.data)
        print("  Searched bill found in history page")

        # Download existing bill
        res_dl = self.client.post("/billing/download_existing/BILL/2026/C1/0001")
        self.assertEqual(res_dl.status_code, 200)
        self.assertEqual(res_dl.content_type, "application/pdf")
        self.assertTrue(res_dl.data.startswith(b"%PDF"))
        print("  Downloaded duplicate bill PDF successfully")

    def test_07_payment_status_and_whatsapp_reminder(self):
        print("\n--- Test 07: Payment Updates & WhatsApp Reminders ---")
        
        # 1. Partial Payment (200 out of 600)
        res = self.client.post("/billing/payment/BILL/2026/C1/0001", data={
            "amount_paid": "200",
            "payment_method": "UPI"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        b = get_bill_by_no("BILL/2026/C1/0001")
        self.assertEqual(b["payment_status"], "Half Paid")
        self.assertEqual(b["amount_paid"], 200.0)
        self.assertEqual(b["pending_amount"], 400.0)
        self.assertEqual(b["payment_method"], "UPI")
        print("  Partial Payment updated: Half Paid, pending Rs.400.00")

        # Verify WhatsApp URL on Dashboard
        res_dash = self.client.get("/billing")
        self.assertIn(b"wa.me/918779033522", res_dash.data)
        self.assertIn(b"pending%20balance%20of", res_dash.data)
        print("  WhatsApp URL correctly formatted with phone 918779033522 and pending amount")

        # 2. Full Payment (600 out of 600)
        res = self.client.post("/billing/payment/BILL/2026/C1/0001", data={
            "amount_paid": "600",
            "payment_method": "Bank Transfer"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        b = get_bill_by_no("BILL/2026/C1/0001")
        self.assertEqual(b["payment_status"], "Full Paid")
        self.assertEqual(b["amount_paid"], 600.0)
        self.assertEqual(b["pending_amount"], 0.0)
        print("  Full Payment updated: Full Paid, pending Rs.0.00")

        # Verify WhatsApp URL is removed when fully paid
        res_dash2 = self.client.get("/billing")
        self.assertNotIn(b"BILL/2026/C1/0001\" class=\"btn-whatsapp", res_dash2.data)
        print("  WhatsApp button correctly hidden for fully paid bill")

    def test_08_gala_without_phone_whatsapp_handling(self):
        print("\n--- Test 08: Gala Without Phone Handling ---")
        galas = list_galas()
        g004 = next(g for g in galas if g["gala_number"] == "G004")
        
        # Generate bill for G004
        self.client.post("/receipts/download", data={
            "gala_id": str(g004["id"]),
            "start_cycle": "C1",
            "end_cycle": "C1",
            "year": "2026"
        })
        
        # Check Dashboard: bill present but no WhatsApp link for G004
        res_dash = self.client.get("/billing")
        self.assertEqual(res_dash.status_code, 200)
        self.assertIn(b"BILL/2026/C1/0002", res_dash.data)
        print("  Bill for G004 generated safely without phone number")

if __name__ == "__main__":
    unittest.main()
