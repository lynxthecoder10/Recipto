"""Application Configuration Settings."""

import os

# WhatsApp Web Automation Timing (in seconds)
WHATSAPP_LOAD_TIME = int(os.environ.get("WHATSAPP_LOAD_TIME", "30"))
BEFORE_ENTER = float(os.environ.get("BEFORE_ENTER", "1.0"))
AFTER_SEND = float(os.environ.get("AFTER_SEND", "3.0"))
BETWEEN_MESSAGES = float(os.environ.get("BETWEEN_MESSAGES", "2.0"))

# Flag to enable/disable desktop browser automation
WHATSAPP_AUTOMATION_ENABLED = os.environ.get("WHATSAPP_AUTOMATION_ENABLED", "1") in ("1", "true", "True")
