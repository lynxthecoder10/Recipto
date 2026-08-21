"""WhatsApp Web Automation Module using webbrowser, pyautogui, and pyperclip.

Executes offline browser automation to dispatch WhatsApp bill notices and payment reminders directly.
Updates SQLite DB with whatsapp_status ('Sent', 'Failed', 'Pending').
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.parse
import webbrowser
from typing import Any

import pyautogui
import pyperclip

import config
from models.db import update_bill_whatsapp_status

# Configure PyAutoGUI safety settings
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.5

logger = logging.getLogger(__name__)


def sanitize_phone_number(phone: str) -> str | None:
    """Format raw phone string into standard E.164 digits (e.g. 918779033522)."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def generate_bill_message(bill: dict[str, Any], is_reminder: bool = False) -> str:
    """Generate the exact text message required for bill generation or payment reminder."""
    tenant_name = bill.get("tenant_name", "Tenant")
    gala_number = bill.get("gala_number", bill.get("house_no", ""))
    bill_no = bill.get("bill_no", "")
    period_label = bill.get("period_label", bill.get("rent_month", bill.get("cycle", "")))
    total_amount = bill.get("amount", 0.0)
    pending_amount = bill.get("pending_amount", total_amount)

    if is_reminder:
        msg = (
            f"Dear {tenant_name},\n\n"
            f"This is a reminder that your rent payment is still pending.\n\n"
            f"Pending Amount: ₹{pending_amount:.2f}\n"
            f"Billing Period: {period_label}\n\n"
            f"Thank you."
        )
    else:
        msg = (
            f"Dear {tenant_name},\n\n"
            f"Your rent bill has been generated.\n\n"
            f"Bill No: {bill_no}\n"
            f"Gala: {gala_number}\n"
            f"Billing Period: {period_label}\n"
            f"Total Amount: ₹{total_amount:.2f}\n\n"
            f"Please make the payment at your earliest convenience.\n\n"
            f"Thank you."
        )
    return msg


def build_whatsapp_deeplink(phone: str, bill: dict[str, Any], is_reminder: bool = False) -> str | None:
    """Generate WhatsApp Web URL (browser-only, never desktop app)."""
    digits = sanitize_phone_number(phone)
    if not digits:
        return None
    msg = generate_bill_message(bill, is_reminder=is_reminder)
    return f"https://web.whatsapp.com/send?phone={digits}&text={urllib.parse.quote(msg)}"


def send_whatsapp_message(
    phone: str,
    tenant_name: str,
    bill: dict[str, Any],
    is_reminder: bool = False,
    pdf_path: str | None = None,
) -> bool:
    """Send a single WhatsApp message via Web browser automation and update SQLite status."""
    bill_no = bill.get("bill_no", "")
    digits = sanitize_phone_number(phone)

    if not digits:
        print(f"[WhatsApp Automation] Invalid/missing phone number for bill {bill_no}. Updating SQLite whatsapp_status = 'Failed'.")
        if bill_no:
            update_bill_whatsapp_status(bill_no, "Failed")
        return False

    if not config.WHATSAPP_AUTOMATION_ENABLED:
        print(f"[WhatsApp Automation] Automation disabled in config. Skipping {bill_no}.")
        return False

    message_text = generate_bill_message(bill, is_reminder=is_reminder)
    encoded_text = urllib.parse.quote(message_text)

    try:
        print(f"\n=======================================================")
        print(f"[WhatsApp Automation] STARTING AUTOMATION FOR BILL: {bill_no}")
        print(f"[WhatsApp Automation] Target Phone: {digits} ({tenant_name})")
        print(f"[WhatsApp Automation] Step 1: Launching WhatsApp Web browser URL with pre-filled message...")
        
        # Build WhatsApp Web URL without pre-filled text for automation
        url = f"https://web.whatsapp.com/send?phone={digits}"
        webbrowser.open(url)

        print(f"[WhatsApp Automation] Step 2: Waiting {config.WHATSAPP_LOAD_TIME}s for WhatsApp Web page load...")
        time.sleep(config.WHATSAPP_LOAD_TIME)

        # Type the message directly into WhatsApp Web
        print(f"[WhatsApp Automation] Step 3: Typing message via pyautogui...")
        # Brief pause to ensure input box is focused
        time.sleep(1)
        pyautogui.typewrite(message_text, interval=0.02)
        # Wait a moment after typing before sending
        time.sleep(config.BEFORE_ENTER)
        # Ensure the chat input is ready before sending
        time.sleep(config.BEFORE_ENTER)
        print(f"[WhatsApp Automation] Step 4: Pressing Enter to send message...")
        pyautogui.press("enter")
        time.sleep(config.AFTER_SEND)

        print(f"[WhatsApp Automation] Step 5: Message dispatched! Closing browser tab via Ctrl+W...")
        pyautogui.hotkey("ctrl", "w")
        time.sleep(1)

        print(f"[WhatsApp Automation] Step 6: Updating SQLite DB -> whatsapp_status = 'Sent' for {bill_no}")
        print(f"=======================================================\n")
        
        # Update status in SQLite
        if bill_no:
            update_bill_whatsapp_status(bill_no, "Sent")
        return True

    except Exception as exc:
        print(f"[WhatsApp Automation] ERROR during automation for {bill_no}: {exc}")
        print(f"[WhatsApp Automation] Updating SQLite DB -> whatsapp_status = 'Failed' for {bill_no}")
        if bill_no:
            update_bill_whatsapp_status(bill_no, "Failed")
        return False


def send_bulk_messages(
    list_of_bills: list[dict[str, Any]], is_reminder: bool = False
) -> dict[str, int]:
    """Iterate through a list of bill dictionaries and send messages sequentially."""
    results = {"sent": 0, "failed": 0}

    for idx, bill in enumerate(list_of_bills):
        if idx > 0:
            time.sleep(config.BETWEEN_MESSAGES)

        phone = bill.get("phone_number", "")
        tenant_name = bill.get("tenant_name", "Tenant")
        success = send_whatsapp_message(
            phone=phone,
            tenant_name=tenant_name,
            bill=bill,
            is_reminder=is_reminder,
        )

        if success:
            results["sent"] += 1
        else:
            results["failed"] += 1

    return results
