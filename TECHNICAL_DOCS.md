# Recipto Technical Documentation

This document outlines the technical architecture, directory structure, and maintenance guidelines for the Recipto system.

## 1. System Architecture

Recipto is built as a monolithic web application using the **Flask** (Python) framework. 

- **Backend**: Python 3.8+ with Flask.
- **Database**: SQLite. Interactions are managed using raw SQL queries mapped to Python dictionaries (in `models/db.py`) rather than an ORM, keeping dependencies lightweight.
- **Frontend**: Server-side rendered HTML using Jinja2 templates (`templates/`) styled with CSS (`static/`).
- **PDF Generation**: HTML templates are rendered into strings and converted to PDFs using `pdfkit` (a Python wrapper for `wkhtmltopdf`).
- **Automation**: Desktop GUI automation is used for WhatsApp messaging, leveraging `pyautogui` for keystrokes/clicks and `pyperclip` for clipboard management.

## 2. Directory Structure

```text
receipt-system/
├── app.py                      # Main Flask application entry point and routing
├── config.py                   # Application configuration & env vars
├── models/
│   └── db.py                   # Database initialization and CRUD operations
├── utils/
│   ├── bill_logic.py           # Logic for parsing months, quarters, and cycles
│   ├── pdf_generator.py        # Wrapper around pdfkit to render PDFs
│   └── whatsapp_automation.py  # pyautogui logic for sending WhatsApp messages
├── templates/                  # Jinja2 HTML templates for the UI
│   ├── base.html
│   ├── dashboard.html
│   ├── receipt_signed.html     # Template used for the PDF generation
│   └── ...
├── static/                     # Static assets (CSS, JS, images)
│   └── signature.png           # Landlord signature for receipts
├── receipts.db                 # SQLite database file (generated at runtime)
└── requirements.txt            # Python dependencies
```

## 3. Database Schema

The SQLite database (`receipts.db`) is initialized automatically. It consists of two primary tables:

1. **`galas`**: Stores property units and tenant details.
   - `id` (INTEGER PRIMARY KEY)
   - `gala_number` (TEXT UNIQUE)
   - `tenant_name` (TEXT)
   - `phone_number` (TEXT)
   - `monthly_rent` (REAL)
   - Timestamps (`created_at`, `updated_at`)

2. **`bills`**: Stores generated rent receipts.
   - `id` (INTEGER PRIMARY KEY)
   - `bill_no` (TEXT UNIQUE)
   - `gala_id` (INTEGER, FOREIGN KEY)
   - `year`, `start_month`, `end_month`, `cycle` (Billing periods)
   - `amount` (REAL, total calculated rent)
   - `amount_paid` (REAL)
   - `payment_status` (TEXT: pending/paid)
   - `payment_method` (TEXT)
   - `whatsapp_status` (TEXT: Pending/Sent)
   - Timestamps

*Note: Schema creation logic is found in `models/db.py` under `init_receipt_store()` and `init_billing_store()`.*

## 4. WhatsApp Automation Workflow

The WhatsApp feature does not use the official WhatsApp Business API (which costs money per message). Instead, it uses **Desktop GUI Automation**.

**How it works (`utils/whatsapp_automation.py`):**
1. The app constructs a `wa.me` deep link with the tenant's phone number and a pre-filled text message.
2. The automation script is launched in a separate background thread (to prevent blocking the Flask server).
3. The script opens the default web browser to the `wa.me` link.
4. It waits for the specified `WHATSAPP_LOAD_TIME` (default 45s).
5. It simulates pressing the `Enter` key via `pyautogui` to send the message.

**Maintenance Note:** Because this relies on screen coordinates and active windows, the server running this code must have an active GUI session, an unlocked screen, and an authenticated WhatsApp Web session in the default browser. If UI elements in WhatsApp Web change, the automation timings in `config.py` may need adjusting.

## 5. Adding Enhancements

### Adding a New Route
To add a new page (e.g., Reports):
1. Define the route in `app.py`:
   ```python
   @app.route("/reports")
   def reports_page():
       return render_template("reports.html", active_page="reports")
   ```
2. Create `reports.html` in the `templates/` directory, extending `base.html`.

### Updating the PDF Template
To modify how the receipt looks:
1. Open `templates/receipt_signed.html`.
2. Edit the HTML/CSS. Note that `wkhtmltopdf` relies on an older WebKit engine, so modern CSS features (like CSS Grid or Flexbox) might behave unpredictably. Stick to basic CSS or tables for layout.

### Modifying Bill Logic
If you need to change how months are counted or quarters are calculated, update the helper functions in `utils/bill_logic.py`.
