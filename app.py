"""Recipto – Rent Receipt & Billing Management System."""

from __future__ import annotations

import calendar
import io
import os
import re
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from models.db import (
    BillingDuplicateError,
    BillingStorageError,
    ReceiptStorageError,
    create_bill,
    create_bills_for_cycles,
    create_or_update_gala,
    get_bill_by_no,
    init_billing_store,
    init_receipt_store,
    list_bills,
    list_galas,
    update_bill_payment_status,
    update_bill_whatsapp_status,
)
from utils.bill_logic import (
    MONTH_OPTIONS,
    QUARTERS_INFO,
    calculate_months_count,
    format_period_label,
    get_cycle,
    get_cycle_code,
    get_cycle_months,
    parse_quarter_selection,
)
from utils.pdf_generator import PdfGenerationError, generate_pdf_bytes
from utils.whatsapp_automation import (
    build_whatsapp_deeplink,
    send_bulk_messages,
    send_whatsapp_message,
)

PROJECT_ROOT = Path(__file__).resolve().parent

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "recipto-dev-secret-change-in-production")


def _enrich_bill(bill: dict) -> dict:
    start = bill.get("start_month", 1)
    end = bill.get("end_month", 1)
    year = bill.get("year", datetime.now().year)
    cycle = bill.get("cycle", "")
    mode = "custom" if cycle.startswith("M") or "Custom" in cycle else "quarter"
    try:
        num_months = calculate_months_count(start, end)
        period = format_period_label(start, end, year, mode=mode)
    except ValueError:
        num_months = 1
        period = str(cycle)

    bill["num_months"] = num_months
    bill["period_label"] = period

    phone = bill.get("phone_number", "")
    tenant_name = bill.get("tenant_name", "Tenant")
    amount = bill.get("amount", 0.0) or 0.0
    amount_paid = bill.get("amount_paid", 0.0) or 0.0
    pending_raw = bill.get("pending_amount")
    pending = pending_raw if pending_raw is not None else max(0.0, amount - amount_paid)
    bill["pending_amount"] = pending
    bill["whatsapp_status"] = bill.get("whatsapp_status", "Pending") or "Pending"
    
    if pending > 0:
        bill["whatsapp_url"] = build_whatsapp_deeplink(phone, bill)
    else:
        bill["whatsapp_url"] = None

    return bill


init_receipt_store()
init_billing_store()


@app.route("/")
def home():
    return redirect(url_for("generate_bill_form"))


@app.route("/receipts")
def generate_bill_form():
    now = datetime.now()
    galas = list_galas()
    return render_template(
        "index.html",
        galas=galas,
        quarters_info=QUARTERS_INFO,
        month_options=MONTH_OPTIONS,
        current_month=now.month,
        current_year=now.year,
        error=None,
    )


@app.post("/receipts/download")
def generate_bill_download():
    gala_id_raw = request.form.get("gala_id", "").strip()
    billing_type = request.form.get("billing_type", "quarter").strip()
    year_raw = request.form.get("year", str(datetime.now().year)).strip()

    if not gala_id_raw or not year_raw:
        flash("Missing required fields.", "error")
        return redirect(url_for("generate_bill_form"))

    try:
        gala_id = int(gala_id_raw)
        year = int(year_raw)
    except ValueError:
        flash("Invalid input format.", "error")
        return redirect(url_for("generate_bill_form"))

    galas = list_galas()
    selected_gala = next((g for g in galas if g["id"] == gala_id), None)
    if not selected_gala:
        flash("Selected Gala not found.", "error")
        return redirect(url_for("generate_bill_form"))

    monthly_rent = selected_gala["monthly_rent"]

    created_bills = []
    if billing_type == "quarter":
        selected_quarters = request.form.getlist("quarters")
        if not selected_quarters and request.form.get("start_cycle"):
            start_c = request.form.get("start_cycle")
            end_c = request.form.get("end_cycle", start_c)
            selected_quarters = [start_c]
            if start_c != end_c and end_c:
                selected_quarters.append(end_c)

        try:
            created_bills = create_bills_for_cycles(
                gala_id=gala_id,
                year=year,
                selected_quarters=selected_quarters,
                monthly_rent=monthly_rent,
            )
        except (BillingDuplicateError, BillingStorageError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("generate_bill_form"))
    else:
        start_month_raw = request.form.get("start_month", "1").strip()
        end_month_raw = request.form.get("end_month", "1").strip()
        try:
            start_month = int(start_month_raw)
            end_month = int(end_month_raw)
        except ValueError:
            flash("Invalid month values.", "error")
            return redirect(url_for("generate_bill_form"))

        if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
            flash("Start and end months must be between 1 and 12.", "error")
            return redirect(url_for("generate_bill_form"))

        if start_month > end_month:
            flash("End month cannot be after start month.", "error")
            return redirect(url_for("generate_bill_form"))

        cycle_code = get_cycle_code(start_month, end_month)
        try:
            record = create_bill(
                gala_id=gala_id,
                year=year,
                start_month=start_month,
                end_month=end_month,
                monthly_rent=monthly_rent,
                billing_type="custom",
                cycle_code=cycle_code,
            )
            created_bills = [record]
        except (BillingDuplicateError, BillingStorageError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("generate_bill_form"))

    if not created_bills:
        flash("No bills were created.", "error")
        return redirect(url_for("generate_bill_form"))

    enriched_bills = [_enrich_bill(b) for b in created_bills]

    # Automatically trigger WhatsApp Web automation in background thread
    threading.Thread(
        target=send_bulk_messages,
        args=(enriched_bills,),
        kwargs={"is_reminder": False},
        daemon=True,
    ).start()

    sig_file = PROJECT_ROOT / "static" / "signature.png"
    signature_path = sig_file.as_uri() if sig_file.is_file() else None
    template_name = "receipt_signed.html"

    pdf_files = []
    for record in created_bills:
        start_m = record["start_month"]
        end_m = record["end_month"]
        b_year = record["year"]
        num_months = calculate_months_count(start_m, end_m)
        rent_month = format_period_label(start_m, end_m, b_year, mode=billing_type)
        total_amount = record["amount"]

        receipt_html = render_template(
            template_name,
            receipt_no=record["bill_no"],
            tenant_name=selected_gala["tenant_name"],
            monthly_rent=f"{monthly_rent:.2f}",
            num_months=num_months,
            amount=f"{total_amount:.2f}",
            house_no=selected_gala["gala_number"],
            rent_month=rent_month,
            date=datetime.now().strftime("%d/%m/%Y"),
            signature_path=signature_path,
        )

        try:
            pdf_bytes = generate_pdf_bytes(html_content=receipt_html)
        except PdfGenerationError as exc:
            flash(f"PDF generation failed: {exc}", "error")
            return redirect(url_for("generate_bill_form"))

        safe_filename = f"{re.sub(r'[^\w\-]', '_', record['bill_no'])}.pdf"
        pdf_files.append((safe_filename, pdf_bytes))

    if len(pdf_files) == 1:
        filename, pdf_bytes = pdf_files[0]
        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    else:
        # Package multiple generated bill PDFs into a zip archive
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename, pdf_bytes in pdf_files:
                zf.writestr(filename, pdf_bytes)
        zip_buffer.seek(0)
        zip_name = f"bills_{year}_{selected_gala['gala_number']}.zip"
        response = make_response(zip_buffer.getvalue())
        response.headers["Content-Type"] = "application/zip"
        response.headers["Content-Disposition"] = f'attachment; filename="{zip_name}"'
        return response


@app.route("/receipts/history")
def duplicate_bill():
    now = datetime.now()
    galas = list_galas()
    
    searched_bill = None
    search_error = None
    gala_number_query = request.args.get("gala_number", "").strip()
    billing_type_query = request.args.get("billing_type", "quarter").strip()
    year_query = request.args.get("year", "").strip()
    
    if gala_number_query and year_query:
        bills = list_bills(limit=1000)
        found = None
        
        target_start = None
        target_end = None
        target_cycle = None

        if billing_type_query == "quarter":
            selected_q = request.args.getlist("quarters")
            if not selected_q and request.args.get("cycle"):
                selected_q = [request.args.get("cycle")]
            if selected_q:
                try:
                    start_m, end_m, cycle_c = parse_quarter_selection(selected_q)
                    target_start = start_m
                    target_end = end_m
                    target_cycle = cycle_c
                except ValueError:
                    pass
        else:
            s_raw = request.args.get("start_month", "").strip()
            e_raw = request.args.get("end_month", "").strip()
            if s_raw.isdigit() and e_raw.isdigit():
                target_start = int(s_raw)
                target_end = int(e_raw)

        for b in bills:
            match_gala = str(b["gala_number"]) == gala_number_query
            match_year = str(b["year"]) == year_query
            match_period = False

            if target_start is not None and target_end is not None:
                match_period = (b["start_month"] == target_start and b["end_month"] == target_end)
            elif target_cycle:
                match_period = (b["cycle"] == target_cycle)
            else:
                match_period = True

            if match_gala and match_year and match_period:
                found = b
                break
        
        if found:
            searched_bill = _enrich_bill(found)
        else:
            search_error = "No bill found matching the selected Gala, Period, and Year."

    return render_template(
        "history.html",
        galas=galas,
        quarters_info=QUARTERS_INFO,
        month_options=MONTH_OPTIONS,
        current_month=now.month,
        current_year=now.year,
        searched_bill=searched_bill,
        search_error=search_error,
    )


@app.post("/billing/download_existing/<path:bill_no>")
def download_existing_bill(bill_no: str):
    bill = get_bill_by_no(bill_no)
    if not bill:
        flash("Bill not found.", "error")
        return redirect(url_for("duplicate_bill"))
    
    start_m = bill.get("start_month", 1)
    end_m = bill.get("end_month", 1)
    year = bill.get("year", datetime.now().year)
    cycle = bill.get("cycle", "")
    mode = "custom" if cycle.startswith("M") or "Custom" in cycle else "quarter"

    monthly_rent = bill.get("monthly_rent", 0.0)
    total_amount = bill.get("amount", 0.0)
    num_months = calculate_months_count(start_m, end_m)
    rent_month = format_period_label(start_m, end_m, year, mode=mode)

    template_name = "receipt_signed.html"
    sig_file = PROJECT_ROOT / "static" / "signature.png"
    signature_path = sig_file.as_uri() if sig_file.is_file() else None

    gen_date = bill["created_at"].split()[0]
    date_parts = gen_date.split("-")
    if len(date_parts) == 3:
        gen_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}"
    
    receipt_html = render_template(
        template_name,
        receipt_no=bill["bill_no"],
        tenant_name=bill["tenant_name"],
        monthly_rent=f"{monthly_rent:.2f}",
        num_months=num_months,
        amount=f"{total_amount:.2f}",
        house_no=bill["gala_number"],
        rent_month=rent_month,
        date=gen_date,
        signature_path=signature_path,
    )

    try:
        pdf_bytes = generate_pdf_bytes(html_content=receipt_html)
    except PdfGenerationError as exc:
        flash(f"PDF generation failed: {exc}", "error")
        return redirect(url_for("duplicate_bill"))

    safe_name = re.sub(r"[^\w\-]", "_", bill["bill_no"])
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{safe_name}.pdf"'
    return response


@app.route("/api/galas")
def api_list_galas():
    return jsonify({"galas": list_galas()})


@app.route("/billing")
def billing_dashboard():
    galas = list_galas()
    bills_raw = list_bills()
    bills = [_enrich_bill(b) for b in bills_raw]
    now = datetime.now()

    return render_template(
        "billing.html",
        galas=galas,
        bills=bills,
        quarters_info=QUARTERS_INFO,
        month_options=MONTH_OPTIONS,
        current_month=now.month,
        current_year=now.year,
        searched_bill=None,
        search_error=None,
    )


@app.post("/billing/galas")
def save_gala():
    gala_number = request.form.get("gala_number", "").strip()
    tenant_name = request.form.get("tenant_name", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    monthly_rent_raw = request.form.get("monthly_rent", "0").strip()

    if not gala_number or not tenant_name:
        flash("Gala number and tenant name are required.", "error")
        return redirect(url_for("billing_dashboard"))

    try:
        monthly_rent = float(monthly_rent_raw)
    except ValueError:
        flash("Monthly Rent must be a number.", "error")
        return redirect(url_for("billing_dashboard"))

    try:
        create_or_update_gala(
            gala_number=gala_number,
            tenant_name=tenant_name,
            phone_number=phone_number,
            monthly_rent=monthly_rent,
        )
        flash(f"Gala {gala_number} saved successfully.", "success")
    except BillingStorageError as exc:
        flash(str(exc), "error")

    return redirect(url_for("billing_dashboard"))


@app.post("/billing/payment/<path:bill_no>")
def update_bill_payment(bill_no: str):
    amount_paid_raw = request.form.get("amount_paid", "0").strip()
    payment_method = request.form.get("payment_method", "").strip()

    try:
        amount_paid = float(amount_paid_raw)
    except ValueError:
        flash("Amount paid must be a number.", "error")
        return redirect(url_for("billing_dashboard"))

    try:
        result = update_bill_payment_status(bill_no, amount_paid, payment_method)
        if result:
            flash(f"Payment for {bill_no} updated successfully.", "success")
        else:
            flash(f"Bill {bill_no} not found.", "error")
    except BillingStorageError as exc:
        flash(str(exc), "error")

    return redirect(url_for("billing_dashboard"))


@app.post("/billing/send_reminder/<path:bill_no>")
def send_payment_reminder(bill_no: str):
    bill = get_bill_by_no(bill_no)
    if not bill:
        flash("Bill not found.", "error")
        return redirect(url_for("billing_dashboard"))

    enriched = _enrich_bill(bill)
    phone = enriched.get("phone_number", "")
    tenant = enriched.get("tenant_name", "Tenant")

    threading.Thread(
        target=send_whatsapp_message,
        args=(phone, tenant, enriched),
        kwargs={"is_reminder": True},
        daemon=True,
    ).start()

    flash(f"WhatsApp Web payment reminder launched for {bill_no}.", "success")
    return redirect(url_for("billing_dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
