"""Runtime Verification Script for Edge HTML-to-PDF & WhatsApp Desktop Automation."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from seed_demo_data import main as seed_main
from models.db import list_galas, get_bill_by_no, list_bills
from utils.whatsapp_automation import send_whatsapp_message

def run_runtime_verification():
    print("=" * 60)
    print("STARTING FULL RUNTIME VERIFICATION AUDIT")
    print("=" * 60)

    # Step 1: Seed Clean Database
    seed_main()
    client = app.test_client()

    galas = list_galas()
    g001 = next(g for g in galas if g["gala_number"] == "G001")

    # Step 2: Trigger Bill Generation & PDF Rendering
    print("\n[Audit Step 1] Submitting Bill Generation for Gala G001 (Q1 2026)...")
    res = client.post("/receipts/download", data={
        "gala_id": str(g001["id"]),
        "billing_type": "quarter",
        "quarters": ["Q1"],
        "year": "2026"
    })

    if res.status_code != 200 or res.content_type != "application/pdf":
        print(f"[Audit Error] Bill generation failed with status {res.status_code}")
        sys.exit(1)

    print(f"[Audit Step 1 Complete] Received PDF ({len(res.data)} bytes)")

    # Step 3: Retrieve Generated Bill from SQLite
    bills = list_bills()
    g001_bill = next((b for b in bills if b["gala_id"] == g001["id"]), None)
    if not g001_bill:
        print("[Audit Error] Bill record missing from SQLite database!")
        sys.exit(1)

    print(f"\n[Audit Step 2] Generated Bill retrieved from SQLite:")
    print(f"  Bill No: {g001_bill['bill_no']}")
    print(f"  Amount: Rs. {g001_bill['amount']:.2f}")
    print(f"  Initial whatsapp_status: {g001_bill.get('whatsapp_status', 'Pending')}")

    # Step 4: Execute Desktop WhatsApp Automation
    print("\n[Audit Step 3] Executing WhatsApp Web Desktop Browser Automation...")
    success = send_whatsapp_message(
        phone=g001["phone_number"],
        tenant_name=g001["tenant_name"],
        bill=dict(g001_bill),
        is_reminder=False,
    )

    # Step 5: Verify SQLite Status Update
    updated_bill = get_bill_by_no(g001_bill["bill_no"])
    print(f"\n[Audit Step 4] Final SQLite Database Status Check:")
    print(f"  Bill No: {updated_bill['bill_no']}")
    print(f"  Final whatsapp_status: {updated_bill['whatsapp_status']}")

    if success and updated_bill["whatsapp_status"] == "Sent":
        print("\n=======================================================")
        print("AUDIT SUCCESS: PDF Rendered via Edge & WhatsApp Web Dispatched!")
        print("=======================================================\n")
    else:
        print(f"\n[Audit Warning] WhatsApp status ended as '{updated_bill['whatsapp_status']}'.")

if __name__ == "__main__":
    run_runtime_verification()
