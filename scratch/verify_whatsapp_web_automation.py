"""Verification suite for WhatsApp Web Browser Automation."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from app import app, _enrich_bill
from seed_demo_data import main as seed_main
from models.db import list_galas, list_bills, get_bill_by_no, update_bill_whatsapp_status
from utils.whatsapp_automation import (
    sanitize_phone_number,
    generate_bill_message,
    send_whatsapp_message,
    send_bulk_messages,
)

class TestWhatsAppAutomation(unittest.TestCase):

    def setUp(self):
        seed_main()
        self.client = app.test_client()
        app.config["TESTING"] = True
        self.galas = list_galas()
        self.g001 = next(g for g in self.galas if g["gala_number"] == "G001")
        self.g004 = next(g for g in self.galas if g["gala_number"] == "G004")  # Blank phone

    def test_01_configuration_presence(self):
        print("\n--- Test 01: Configuration Variables ---")
        self.assertTrue(hasattr(config, "WHATSAPP_LOAD_TIME"))
        self.assertTrue(hasattr(config, "BEFORE_ENTER"))
        self.assertTrue(hasattr(config, "AFTER_SEND"))
        self.assertTrue(hasattr(config, "BETWEEN_MESSAGES"))
        self.assertTrue(hasattr(config, "WHATSAPP_AUTOMATION_ENABLED"))
        print(f"  config.py loaded successfully: LOAD_TIME={config.WHATSAPP_LOAD_TIME}s [OK]")

    def test_02_phone_sanitization_and_message_generation(self):
        print("\n--- Test 02: Phone Sanitization & Message Content ---")
        
        # Phone sanitization
        p1 = sanitize_phone_number("8779033522")
        self.assertEqual(p1, "918779033522")
        
        p2 = sanitize_phone_number("+91 (877) 903-3522")
        self.assertEqual(p2, "918779033522")

        p3 = sanitize_phone_number("")
        self.assertIsNone(p3)

        # Message generation for Bill
        bill = {
            "bill_no": "BILL/2026/Q1/0001",
            "tenant_name": "Alice",
            "gala_number": "G001",
            "period_label": "Jan - Mar 2026",
            "amount": 1800.0,
            "pending_amount": 1800.0,
        }

        msg_gen = generate_bill_message(bill, is_reminder=False)
        self.assertIn("Dear Alice,", msg_gen)
        self.assertIn("Your rent bill has been generated.", msg_gen)
        self.assertIn("Bill No: BILL/2026/Q1/0001", msg_gen)
        self.assertIn("Gala: G001", msg_gen)
        self.assertIn("Billing Period: Jan - Mar 2026", msg_gen)
        self.assertIn("Total Amount: ₹1800.00", msg_gen)
        print("  Bill Generation Message content validated [OK]")

        # Message generation for Reminder
        msg_rem = generate_bill_message(bill, is_reminder=True)
        self.assertIn("Dear Alice,", msg_rem)
        self.assertIn("This is a reminder that your rent payment is still pending.", msg_rem)
        self.assertIn("Pending Amount: ₹1800.00", msg_rem)
        self.assertIn("Billing Period: Jan - Mar 2026", msg_rem)
        print("  Payment Reminder Message content validated [OK]")

    def test_03_sqlite_whatsapp_status_tracking(self):
        print("\n--- Test 03: SQLite Delivery Status Tracking ---")
        
        # Generate Q1 bill
        res = self.client.post("/receipts/download", data={
            "gala_id": str(self.g001["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1"],
            "year": "2026"
        })
        self.assertEqual(res.status_code, 200)

        # Check default status in SQLite
        bill = get_bill_by_no("BILL/2026/Q1/0001")
        self.assertIsNotNone(bill)
        self.assertIn("whatsapp_status", bill)

        # Update status helper
        updated = update_bill_whatsapp_status("BILL/2026/Q1/0001", "Sent")
        self.assertTrue(updated)
        
        bill_sent = get_bill_by_no("BILL/2026/Q1/0001")
        self.assertEqual(bill_sent["whatsapp_status"], "Sent")
        print("  SQLite whatsapp_status updated to 'Sent' [OK]")

    def test_04_missing_phone_graceful_handling(self):
        print("\n--- Test 04: Graceful Handling for Missing Phone ---")
        
        # Generate bill for G004 (blank phone number)
        res = self.client.post("/receipts/download", data={
            "gala_id": str(self.g004["id"]),
            "billing_type": "quarter",
            "quarters": ["Q1"],
            "year": "2026"
        })
        self.assertEqual(res.status_code, 200)

        # Attempt to send message to blank phone
        bill_g004 = get_bill_by_no("BILL/2026/Q1/0001")
        success = send_whatsapp_message(
            phone=self.g004["phone_number"],
            tenant_name=self.g004["tenant_name"],
            bill=bill_g004,
        )
        self.assertFalse(success)

        # Verify SQLite status marked Failed
        bill_failed = get_bill_by_no("BILL/2026/Q1/0001")
        self.assertEqual(bill_failed["whatsapp_status"], "Failed")
        print("  Missing phone marked whatsapp_status = 'Failed' without crashing [OK]")

if __name__ == "__main__":
    unittest.main()
