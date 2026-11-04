# Recipto – Rent Receipt & Billing Management System

Recipto is a web-based rent receipt and billing management system built with Flask. It allows landlords or property managers to manage "Galas" (units), tenant details, track payments, generate automated PDF rent receipts, and send these receipts to tenants automatically via WhatsApp Web.

## Features

- **Dashboard**: Get a quick overview of total units, active tenants, generated bills, paid vs. pending amounts, and recent reminders.
- **Gala Management**: Easily register and manage properties or galas, including the tenant's name, phone number, and monthly rent.
- **Billing & PDF Generation**: 
  - Generate quarterly or custom-period bills.
  - Automatically calculate rent based on the number of months.
  - Generates polished PDF rent receipts with signatures.
  - Supports bulk generation and downloads via ZIP files.
- **WhatsApp Automation**:
  - Leverages desktop automation (`pyautogui` & `pyperclip`) to open WhatsApp Web and send generated receipts and payment reminders directly to tenants.
- **Payment Verification**: Track payments, log payment methods, and easily follow up on pending dues.
- **Bill History**: Search past bills by Gala, year, or billing cycle, and download duplicate receipts anytime.

## Prerequisites

- **Python 3.8+**
- **wkhtmltopdf**: Used by `pdfkit` to generate PDF receipts. Ensure this is installed on your system and available in your system's PATH.
- **WhatsApp Desktop** (or a logged-in WhatsApp Web tab) if you intend to use the WhatsApp automation feature.

## Installation

1. **Clone the repository:**
   ```bash
   git clone <your-repository-url>
   cd receipt-system
   ```

2. **Set up a virtual environment (Recommended):**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Configuration (Environment Variables)

Recipto uses the following environment variables (which you can set in your terminal or via a `.env` file if you integrate a tool like `python-dotenv`):

- `SECRET_KEY`: Used by Flask for session management. Provide a strong secret for production. (Default: `recipto-dev-secret-change-in-production`)
- `WHATSAPP_AUTOMATION_ENABLED`: Set to `1`, `true`, or `True` to enable desktop WhatsApp automation. Set to `0` or `false` to disable. (Default: `1`)
- **WhatsApp Timings**:
  - `WHATSAPP_LOAD_TIME`: Seconds to wait for WhatsApp Web/Desktop to load. (Default: `45`)
  - `BEFORE_ENTER`: Seconds to wait before hitting Enter to send a message. (Default: `3.0`)
  - `AFTER_SEND`: Seconds to wait after sending before moving to the next message. (Default: `12.0`)
  - `BETWEEN_MESSAGES`: Seconds to wait between sending messages to different tenants. (Default: `2.0`)

## Running the Application

1. **Start the Flask development server:**
   ```bash
   python app.py
   # or
   flask run
   ```

2. Open your web browser and navigate to `http://127.0.0.1:5000/`.

## Architecture & Maintenance

For details on the system architecture, how the database is structured, and how the WhatsApp automation operates, please refer to the [TECHNICAL_DOCS.md](TECHNICAL_DOCS.md).

For step-by-step instructions on how to use the web application from an end-user perspective, refer to the [USER_WORKFLOW.md](USER_WORKFLOW.md).
