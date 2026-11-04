# Recipto User Workflow Guide

This document outlines the standard day-to-day operations and workflows for using the Recipto Billing Management System.

## 1. Managing Galas (Properties)

Before you can bill a tenant, you must add their Gala (property unit) to the system.

**To add or update a Gala:**
1. Navigate to the **Galas** section from the sidebar.
2. Under "Add New Gala", fill in the form:
   - **Gala Number**: A unique identifier for the property (e.g., A-101).
   - **Tenant Name**: Full name of the current tenant.
   - **Phone Number**: The tenant's WhatsApp-enabled phone number (include country code if necessary for deep links).
   - **Monthly Rent**: The agreed-upon monthly rent amount.
3. Click **Save Gala**. The system will save this information. If the Gala Number already exists, it will update the existing tenant and rent details.

## 2. Generating Bills & Receipts

You can generate bills and receipts for tenants on a quarterly basis or for custom months.

**To generate a bill:**
1. Navigate to the **Billing** section.
2. Select the **Gala** from the dropdown menu.
3. Choose the **Billing Type**:
   - **Quarterly**: Select which quarter(s) you are billing for.
   - **Custom Months**: Select a start month and an end month (e.g., January to March).
4. Select the **Year**.
5. Click **Generate Bill**.

**What happens next?**
- The system calculates the total amount (Monthly Rent x Number of Months).
- It creates a database entry for the bill, initially marked as "Pending".
- It generates a signed PDF receipt. If you generated multiple bills at once, it will package them into a `.zip` file for download.
- **WhatsApp Automation**: If enabled, the system will automatically open a background automation process that opens WhatsApp Web and sends the bill notification to the tenant's registered phone number. *(Please ensure your computer is unlocked and WhatsApp Web is logged in if you are using desktop automation).*

## 3. Tracking Payments

Once a tenant has paid their bill, you need to record it in the system.

**To record a payment:**
1. Navigate to the **Payments** section.
2. You will see a list of recent bills. Locate the pending bill.
3. In the "Action" column for that bill, enter the **Amount Paid** and select the **Payment Method** (Cash, UPI, Bank Transfer, etc.).
4. Click **Update**. The system will deduct the amount paid from the pending amount. If the pending amount reaches 0, the bill is marked as "Paid".

**To send a payment reminder:**
- If a bill is overdue, locate the bill in the **Payments** section.
- Click the **Send Reminder** button.
- This will trigger the WhatsApp automation to send a polite reminder message to the tenant.

## 4. Viewing Bill History

You can search for past bills to review details or redownload the PDF receipt.

**To search history:**
1. Navigate to the **History** section.
2. Use the "Search Bill" form to select a **Gala**, **Billing Type**, and **Year**.
3. Click **Search**.
4. If a matching bill is found, it will be displayed below the form.
5. You can click **Download PDF** to regenerate and save the receipt to your computer.

## 5. Dashboard Overview

The **Dashboard** is your homepage. It provides a real-time snapshot of your rental business:
- **Total Galas & Active Tenants**
- **Total Bills Generated**
- **Paid vs. Pending Bills**
- **Total Pending Amount**: A sum of all outstanding balances.
- **Recent Reminders & Bills**: Quick links to the latest generated invoices and tenants who owe rent.
