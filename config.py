"""Application Configuration Settings."""

import os

# WhatsApp Web Automation Timing (in seconds)
WHATSAPP_LOAD_TIME = int(os.environ.get("WHATSAPP_LOAD_TIME", "15"))
WHATSAPP_MAX_RETRIES = int(os.environ.get("WHATSAPP_MAX_RETRIES", "3"))
WHATSAPP_RETRY_INTERVAL = float(os.environ.get("WHATSAPP_RETRY_INTERVAL", "3.0"))
BEFORE_ENTER = float(os.environ.get("BEFORE_ENTER", "1.5"))
AFTER_SEND = float(os.environ.get("AFTER_SEND", "4.0"))
BETWEEN_MESSAGES = float(os.environ.get("BETWEEN_MESSAGES", "2.0"))

# Flag to enable/disable desktop browser automation
WHATSAPP_AUTOMATION_ENABLED = os.environ.get("WHATSAPP_AUTOMATION_ENABLED", "1") in ("1", "true", "True")
