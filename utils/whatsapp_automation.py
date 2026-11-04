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


import sys

def activate_browser_window() -> bool:
    """Attempt to bring the active browser window (WhatsApp Web) to the foreground on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        matching_windows = []

        def enum_handler(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.lower()
                    if "whatsapp" in title or any(b in title for b in ["chrome", "edge", "firefox", "brave", "opera"]):
                        matching_windows.append((hwnd, buff.value))
            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(EnumWindowsProc(enum_handler), 0)

        if matching_windows:
            whatsapp_windows = [w for w in matching_windows if "whatsapp" in w[1].lower()]
            target_hwnd, title = whatsapp_windows[0] if whatsapp_windows else matching_windows[0]
            user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(target_hwnd)
            logger.info(f"[WhatsApp Automation] Activated browser window: {title}")
            return True
    except Exception as exc:
        logger.warning(f"[WhatsApp Automation] Could not activate browser window: {exc}")
    return False


def send_whatsapp_message(
    phone: str,
    tenant_name: str,
    bill: dict[str, Any],
    is_reminder: bool = False,
    pdf_path: str | None = None,
) -> bool:
    """Send a single WhatsApp message via Web browser automation and update SQLite status."""
    bill_dict = dict(bill) if hasattr(bill, "keys") else bill
    bill_no = bill_dict.get("bill_no", "")
    user_id = bill_dict.get("user_id")
    digits = sanitize_phone_number(phone)

    if not digits:
        logger.error(
            f"[WhatsApp Automation] Invalid/missing phone number '{phone}' for bill {bill_no}. Updating SQLite whatsapp_status = 'Failed'."
        )
        if bill_no:
            update_bill_whatsapp_status(bill_no, "Failed", user_id=user_id)
        return False

    if not config.WHATSAPP_AUTOMATION_ENABLED:
        logger.info(f"[WhatsApp Automation] Automation disabled in config. Skipping {bill_no}.")
        return False

    message_text = generate_bill_message(bill_dict, is_reminder=is_reminder)
    encoded_text = urllib.parse.quote(message_text)

    logger.info("=" * 60)
    logger.info(f"[WhatsApp Automation] STARTING AUTOMATION FOR BILL: {bill_no}")
    logger.info(f"[WhatsApp Automation] Target: {tenant_name} ({digits}) | Mode: {'Reminder' if is_reminder else 'Bill Notice'}")

    try:
        # Step 1: Pre-fill URL so WhatsApp Web natively injects the message into the chat input
        logger.info(f"[WhatsApp Automation] Step 1: Launching WhatsApp Web with pre-filled message URL...")
        url = f"https://web.whatsapp.com/send?phone={digits}&text={encoded_text}"
        webbrowser.open(url)

        # Step 2: Initial load wait
        load_time = getattr(config, "WHATSAPP_LOAD_TIME", 10)
        logger.info(f"[WhatsApp Automation] Step 2: Waiting {load_time}s for WhatsApp Web page and chat to load...")
        time.sleep(load_time)
        activate_browser_window()

        # Step 3: Copy message to clipboard for paste verification and fallback
        logger.info("[WhatsApp Automation] Step 3: Copying message text to clipboard for paste verification...")
        try:
            pyperclip.copy(message_text)
            time.sleep(0.3)
            clipboard_content = pyperclip.paste()
            if clipboard_content != message_text:
                logger.warning("[WhatsApp Automation] Warning: Clipboard content does not match message text.")
            else:
                logger.info("[WhatsApp Automation] Clipboard copy verified successfully.")
        except Exception as clip_err:
            logger.warning(f"[WhatsApp Automation] Clipboard copy warning: {clip_err}")

        # Step 4: Wait / Retry loop for chat input & dispatch
        max_retries = getattr(config, "WHATSAPP_MAX_RETRIES", 3)
        retry_interval = getattr(config, "WHATSAPP_RETRY_INTERVAL", 3.0)
        before_enter = getattr(config, "BEFORE_ENTER", 1.5)
        dispatched = False

        for attempt in range(1, max_retries + 1):
            logger.info(f"[WhatsApp Automation] Step 4: Paste & Send attempt {attempt}/{max_retries}...")
            activate_browser_window()
            time.sleep(0.5)

            # Trigger paste fallback via Ctrl+V in case URL pre-fill was delayed
            try:
                pyautogui.hotkey("ctrl", "v")
                pyautogui.press("space")
                pyautogui.press("backspace")
            except Exception as paste_err:
                logger.warning(f"[WhatsApp Automation] Attempt {attempt}: Paste action failed: {paste_err}")

            time.sleep(before_enter)

            # Dispatch Enter to send
            try:
                pyautogui.press("enter")
                logger.info(f"[WhatsApp Automation] Attempt {attempt}: Enter key pressed successfully.")
                # Verify that the message was sent by checking the input field is cleared
                time.sleep(0.5)  # give UI a moment to clear
                pyautogui.hotkey("ctrl", "a")
                pyautogui.hotkey("ctrl", "c")
                time.sleep(0.2)
                post_send_content = pyperclip.paste()
                if post_send_content:
                    logger.warning(f"[WhatsApp Automation] Attempt {attempt}: Message not cleared after send (still '{post_send_content[:30]}...'). Retrying.")
                    raise RuntimeError("Message not cleared after send")
                else:
                    logger.info(f"[WhatsApp Automation] Attempt {attempt}: Message cleared, send confirmed.")
                dispatched = True
                break
            except Exception as send_err:
                logger.warning(f"[WhatsApp Automation] Attempt {attempt}: Send action failed: {send_err}")
                if attempt < max_retries:
                    logger.info(f"[WhatsApp Automation] Retrying in {retry_interval}s...")
                    time.sleep(retry_interval)

        if not dispatched:
            raise RuntimeError("Failed to send message: send action was not confirmed across all retry attempts.")

        # Step 5: Allow time for message delivery across the network
        after_send = getattr(config, "AFTER_SEND", 4.0)
        logger.info(f"[WhatsApp Automation] Step 5: Waiting {after_send}s for network transmission...")
        time.sleep(after_send)

        # Close tab
        logger.info("[WhatsApp Automation] Closing WhatsApp Web tab via Ctrl+W...")
        activate_browser_window()
        pyautogui.hotkey("ctrl", "w")
        time.sleep(1.0)

        # Step 6: Update database status
        logger.info(f"[WhatsApp Automation] Step 6: Updating SQLite DB -> whatsapp_status = 'Sent' for {bill_no}")
        if bill_no:
            update_bill_whatsapp_status(bill_no, "Sent", user_id=user_id)
        logger.info("=" * 60)
        return True

    except Exception as exc:
        logger.error(f"[WhatsApp Automation] ERROR during automation for {bill_no}: {exc}", exc_info=True)
        logger.error(f"[WhatsApp Automation] Updating SQLite DB -> whatsapp_status = 'Failed' for {bill_no}")
        if bill_no:
            update_bill_whatsapp_status(bill_no, "Failed", user_id=user_id)
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
