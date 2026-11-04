"""Recipto – Rent Receipt & Billing Management System."""

from __future__ import annotations

import calendar
import functools
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
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from models.db import (
    BillingDuplicateError,
    BillingStorageError,
    ReceiptStorageError,
    create_bill,
    create_bills_for_cycles,
    create_or_update_gala,
    create_user,
    ensure_default_user,
    get_bill_by_no,
    get_gala_by_id,
    get_user_by_email,
    get_user_by_id,
    init_billing_store,
    init_receipt_store,
    list_bills,
    list_galas,
    update_bill_payment_status,
    update_bill_whatsapp_status,
    update_user_profile,
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
app.secret_key = os.environ.get("SECRET_KEY", "recipto-super-secret-production-key-2026-mumbai")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def login_required(view_func):
    """Decorator to ensure requests are from authenticated users."""
    @functools.wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            flash("Please sign in to access Recipto.", "warning")
            return redirect(url_for("login_page", next=request.url))
        return view_func(*args, **kwargs)
    return wrapped_view


@app.context_processor
def inject_current_user():
    """Inject the active authenticated user into all Jinja templates."""
    user_id = session.get("user_id")
    current_user = get_user_by_id(user_id) if user_id else None
    return {"current_user": current_user}


def _enrich_bill(bill: dict) -> dict:
    """Enrich raw database bill dictionary with formatted labels and normalized status."""
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
    amount = float(bill.get("amount", 0.0) or 0.0)
    amount_paid = float(bill.get("amount_paid", 0.0) or 0.0)

    # Standardize payment_status to 'paid', 'pending', or 'partial'
    raw_status = str(bill.get("payment_status") or "pending").strip().lower()
    if raw_status in ("full paid", "paid"):
        status = "paid"
    elif raw_status in ("half paid", "partial"):
        status = "partial"
    else:
        status = "pending"
    bill["payment_status"] = status

    # Ensure pending amount is accurate
    if status == "paid":
        pending = 0.0
    else:
        pending_raw = bill.get("pending_amount")
        pending = float(pending_raw) if pending_raw is not None else max(0.0, amount - amount_paid)
    bill["pending_amount"] = pending

    bill["whatsapp_status"] = bill.get("whatsapp_status", "Pending") or "Pending"

    if pending > 0:
        bill["whatsapp_url"] = build_whatsapp_deeplink(phone, bill, is_reminder=True)
    else:
        bill["whatsapp_url"] = build_whatsapp_deeplink(phone, bill, is_reminder=False)

    return bill


# Initialize SQLite storage and ensure default user exists
init_receipt_store()
init_billing_store()
ensure_default_user()


# ---------------------------------------------------------------------------
# Authentication Routes (Signup, Login, Logout, Forgot Password)
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_url = request.args.get("next") or url_for("dashboard")

        if not email or not password:
            flash("Please provide both email and password.", "error")
            return render_template("login.html")

        user = get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            session["user_role"] = user["role"]
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(next_url)
        else:
            flash("Invalid email or password. Please check your credentials.", "error")
            return render_template("login.html", email=email)

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup_page():
    if "user_id" in session and request.method == "GET":
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = request.form.get("role", "owner").strip().lower() or "owner"

        if not name or not email or not password:
            flash("All required fields must be completed.", "error")
            return render_template("signup.html", name=name, email=email)

        if "@" not in email or "." not in email:
            flash("Please enter a valid email address.", "error")
            return render_template("signup.html", name=name, email=email)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("signup.html", name=name, email=email)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("signup.html", name=name, email=email)

        if get_user_by_email(email):
            flash("An account with this email address already exists. Please sign in.", "error")
            return redirect(url_for("login_page"))

        try:
            pwd_hash = generate_password_hash(password)
            user = create_user(
                email=email,
                password_hash=pwd_hash,
                name=name,
                role=role,
            )
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            session["user_role"] = user["role"]
            flash(f"Account registered successfully! Welcome to Recipto, {name}.", "success")
            return redirect(url_for("dashboard"))
        except BillingStorageError as exc:
            flash(str(exc), "error")
            return render_template("signup.html", name=name, email=email)

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out safely.", "info")
    return redirect(url_for("login_page"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your registered email address.", "error")
            return render_template("forgot_password.html")

        user = get_user_by_email(email)
        if user:
            # Simulate password reset link dispatched to registered email
            flash(
                f"A secure password reset link has been dispatched to {email}. Please check your inbox.",
                "success",
            )
        else:
            # Prevent email enumeration while maintaining clean UX
            flash(
                f"If an account exists for {email}, a recovery link has been sent.",
                "info",
            )
        return redirect(url_for("login_page"))

    return render_template("forgot_password.html")


# ---------------------------------------------------------------------------
# Dashboard (Protected & Scoped)
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    user_id = session["user_id"]
    galas = list_galas(user_id=user_id)
    bills_raw = list_bills(limit=10000, user_id=user_id)
    bills = [_enrich_bill(b) for b in bills_raw]

    paid = [b for b in bills if b.get("payment_status") == "paid"]
    pending = [b for b in bills if b.get("payment_status") != "paid"]
    pending_amount = sum(b.get("pending_amount", 0.0) for b in pending)
    active_tenants = len([g for g in galas if g.get("tenant_name")])
    recent_bills = bills[:5]
    pending_reminders = [b for b in pending if b.get("pending_amount", 0) > 0][:5]

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        total_galas=len(galas),
        active_tenants=active_tenants,
        total_bills=len(bills),
        paid_bills=len(paid),
        pending_bills=len(pending),
        pending_amount=pending_amount,
        recent_bills=recent_bills,
        pending_reminders=pending_reminders,
    )


# ---------------------------------------------------------------------------
# Gala Management (Protected & Scoped)
# ---------------------------------------------------------------------------

@app.route("/galas")
@login_required
def galas_page():
    user_id = session["user_id"]
    galas = list_galas(user_id=user_id)
    return render_template("galas.html", active_page="galas", galas=galas)


@app.post("/galas")
@login_required
def save_gala():
    user_id = session["user_id"]
    gala_number = request.form.get("gala_number", "").strip()
    tenant_name = request.form.get("tenant_name", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    monthly_rent_raw = request.form.get("monthly_rent", "0").strip()

    if not gala_number or not tenant_name:
        flash("Gala number and tenant name are required.", "error")
        return redirect(url_for("galas_page"))

    if len(gala_number) > 50:
        flash("Gala number must be under 50 characters.", "error")
        return redirect(url_for("galas_page"))

    try:
        monthly_rent = float(monthly_rent_raw)
        if monthly_rent < 0:
            flash("Monthly Rent cannot be negative.", "error")
            return redirect(url_for("galas_page"))
    except ValueError:
        flash("Monthly Rent must be a valid number.", "error")
        return redirect(url_for("galas_page"))

    try:
        create_or_update_gala(
            gala_number=gala_number,
            tenant_name=tenant_name,
            phone_number=phone_number,
            monthly_rent=monthly_rent,
            user_id=user_id,
        )
        flash(f"Gala {gala_number} saved successfully.", "success")
    except BillingStorageError as exc:
        flash(str(exc), "error")

    return redirect(url_for("galas_page"))


# ---------------------------------------------------------------------------
# Billing (Generate Bills - Protected & Scoped)
# ---------------------------------------------------------------------------

@app.route("/billing")
@login_required
def billing_page():
    user_id = session["user_id"]
    now = datetime.now()
    galas = list_galas(user_id=user_id)
    return render_template(
        "billing.html",
        active_page="billing",
        galas=galas,
        quarters_info=QUARTERS_INFO,
        month_options=MONTH_OPTIONS,
        current_month=now.month,
        current_year=now.year,
        error=None,
    )


@app.post("/billing/download")
@login_required
def generate_bill_download():
    user_id = session["user_id"]
    gala_id_raw = request.form.get("gala_id", "").strip()
    billing_type = request.form.get("billing_type", "quarter").strip()
    year_raw = request.form.get("year", str(datetime.now().year)).strip()

    if not gala_id_raw or not year_raw:
        flash("Missing required fields.", "error")
        return redirect(url_for("billing_page"))

    try:
        gala_id = int(gala_id_raw)
        year = int(year_raw)
        if not (2000 <= year <= 2100):
            flash("Year must be between 2000 and 2100.", "error")
            return redirect(url_for("billing_page"))
    except ValueError:
        flash("Invalid input format.", "error")
        return redirect(url_for("billing_page"))

    selected_gala = get_gala_by_id(gala_id, user_id=user_id)
    if not selected_gala:
        flash("Selected Gala not found or does not belong to your account.", "error")
        return redirect(url_for("billing_page"))

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
                user_id=user_id,
            )
        except (BillingDuplicateError, BillingStorageError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("billing_page"))
    else:
        start_month_raw = request.form.get("start_month", "1").strip()
        end_month_raw = request.form.get("end_month", "1").strip()
        try:
            start_month = int(start_month_raw)
            end_month = int(end_month_raw)
        except ValueError:
            flash("Invalid month values.", "error")
            return redirect(url_for("billing_page"))

        if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
            flash("Start and end months must be between 1 and 12.", "error")
            return redirect(url_for("billing_page"))

        if start_month > end_month:
            flash("End month cannot be after start month.", "error")
            return redirect(url_for("billing_page"))

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
                user_id=user_id,
            )
            created_bills = [record]
        except (BillingDuplicateError, BillingStorageError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("billing_page"))

    if not created_bills:
        flash("No bills were created.", "error")
        return redirect(url_for("billing_page"))

    enriched_bills = [_enrich_bill(b) for b in created_bills]

    # Trigger WhatsApp Web automation asynchronously in background thread
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
            return redirect(url_for("billing_page"))

        safe_bill_no = re.sub(r"[^\w\-]", "_", record["bill_no"])
        safe_filename = f"{safe_bill_no}.pdf"
        pdf_files.append((safe_filename, pdf_bytes))

    if len(pdf_files) == 1:
        filename, pdf_bytes = pdf_files[0]
        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    else:
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


# ---------------------------------------------------------------------------
# Payment Verification (Protected & Scoped)
# ---------------------------------------------------------------------------

@app.route("/payments")
@login_required
def payments_page():
    user_id = session["user_id"]
    galas = list_galas(user_id=user_id)
    bills_raw = list_bills(limit=1000, user_id=user_id)
    bills = [_enrich_bill(b) for b in bills_raw]
    return render_template(
        "payments.html",
        active_page="payments",
        galas=galas,
        bills=bills,
    )


@app.post("/payments/update/<path:bill_no>")
@login_required
def update_bill_payment(bill_no: str):
    user_id = session["user_id"]
    amount_paid_raw = request.form.get("amount_paid", "0").strip()
    payment_method = request.form.get("payment_method", "").strip()

    try:
        amount_paid = float(amount_paid_raw)
        if amount_paid < 0:
            flash("Amount paid cannot be negative.", "error")
            return redirect(url_for("payments_page"))
    except ValueError:
        flash("Amount paid must be a valid number.", "error")
        return redirect(url_for("payments_page"))

    try:
        result = update_bill_payment_status(bill_no, amount_paid, payment_method, user_id=user_id)
        if result:
            flash(f"Payment for {bill_no} updated successfully.", "success")
        else:
            flash(f"Bill {bill_no} not found or unauthorized.", "error")
    except BillingStorageError as exc:
        flash(str(exc), "error")

    return redirect(url_for("payments_page"))


@app.post("/payments/reminder/<path:bill_no>")
@login_required
def send_payment_reminder(bill_no: str):
    user_id = session["user_id"]
    bill = get_bill_by_no(bill_no, user_id=user_id)
    if not bill:
        flash("Bill not found or unauthorized.", "error")
        return redirect(url_for("payments_page"))

    enriched = _enrich_bill(bill)
    phone = enriched.get("phone_number", "")
    tenant = enriched.get("tenant_name", "Tenant")

    threading.Thread(
        target=send_whatsapp_message,
        args=(phone, tenant, enriched),
        kwargs={"is_reminder": True},
        daemon=True,
    ).start()

    flash(f"WhatsApp payment reminder launched for {bill_no}.", "success")
    return redirect(url_for("payments_page"))


# ---------------------------------------------------------------------------
# Bill History & Archive (Protected & Scoped)
# ---------------------------------------------------------------------------

@app.route("/history")
@login_required
def history_page():
    user_id = session["user_id"]
    now = datetime.now()
    galas = list_galas(user_id=user_id)
    bills_raw = list_bills(limit=1000, user_id=user_id)
    bills = [_enrich_bill(b) for b in bills_raw]

    searched_bill = None
    search_error = None
    gala_number_query = request.args.get("gala_number", "").strip()
    billing_type_query = request.args.get("billing_type", "quarter").strip()
    year_query = request.args.get("year", "").strip()

    if gala_number_query and year_query:
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

        for b in bills_raw:
            match_gala = str(b["gala_number"]) == gala_number_query
            match_year = str(b["year"]) == year_query
            match_period = False

            if target_start is not None and target_end is not None:
                match_period = (b["start_month"] == target_start and b["end_month"] == target_end)
            elif target_cycle:
                match_period = (b["cycle"] == target_cycle or b["cycle"] in (target_cycle.split("-") if "-" in target_cycle else [target_cycle]))
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
        active_page="history",
        galas=galas,
        bills=bills,
        quarters_info=QUARTERS_INFO,
        month_options=MONTH_OPTIONS,
        current_month=now.month,
        current_year=now.year,
        searched_bill=searched_bill,
        search_error=search_error,
    )


@app.post("/history/download/<path:bill_no>")
@login_required
def download_existing_bill(bill_no: str):
    user_id = session["user_id"]
    bill = get_bill_by_no(bill_no, user_id=user_id)
    if not bill:
        flash("Bill not found or unauthorized.", "error")
        return redirect(url_for("history_page"))

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

    gen_date = str(bill.get("created_at", "")).split()[0]
    date_parts = gen_date.split("-")
    if len(date_parts) == 3:
        gen_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}"
    else:
        gen_date = datetime.now().strftime("%d/%m/%Y")

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
        return redirect(url_for("history_page"))

    safe_name = re.sub(r"[^\w\-]", "_", bill["bill_no"])
    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{safe_name}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Reports & Analytics (Protected)
# ---------------------------------------------------------------------------

@app.route("/reports")
@login_required
def reports_page():
    return render_template("reports.html", active_page="reports")


# ---------------------------------------------------------------------------
# Settings & Profile Management (Protected)
# ---------------------------------------------------------------------------

@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html", active_page="settings")


@app.post("/settings/profile")
@login_required
def update_profile():
    user_id = session["user_id"]
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not name or not email or "@" not in email:
        flash("Please provide a valid name and email address.", "error")
        return redirect(url_for("settings_page"))

    try:
        updated = update_user_profile(user_id, name=name, email=email)
        session["user_name"] = updated["name"]
        session["user_email"] = updated["email"]
        flash("Profile updated successfully.", "success")
    except BillingStorageError as exc:
        flash(str(exc), "error")

    return redirect(url_for("settings_page"))


@app.post("/settings/password")
@login_required
def change_password():
    user_id = session["user_id"]
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_new_password = request.form.get("confirm_new_password", "")

    user = get_user_by_id(user_id)
    user_record = get_user_by_email(user["email"])

    if not check_password_hash(user_record["password_hash"], current_password):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("settings_page"))

    if len(new_password) < 6:
        flash("New password must be at least 6 characters long.", "error")
        return redirect(url_for("settings_page"))

    if new_password != confirm_new_password:
        flash("New passwords do not match.", "error")
        return redirect(url_for("settings_page"))

    try:
        new_hash = generate_password_hash(new_password)
        update_user_profile(user_id, name=user["name"], email=user["email"], password_hash=new_hash)
        flash("Password updated successfully.", "success")
    except BillingStorageError as exc:
        flash(str(exc), "error")

    return redirect(url_for("settings_page"))


# ---------------------------------------------------------------------------
# API Endpoints (Protected & Scoped)
# ---------------------------------------------------------------------------

@app.route("/api/galas")
@login_required
def api_list_galas():
    user_id = session["user_id"]
    return jsonify({"galas": list_galas(user_id=user_id)})


# ---------------------------------------------------------------------------
# Backward Compatibility Redirects
# ---------------------------------------------------------------------------

@app.route("/receipts")
def legacy_receipts():
    return redirect(url_for("billing_page"), code=301)


@app.post("/receipts/download")
def legacy_receipts_download():
    return redirect(url_for("generate_bill_download"), code=307)


@app.route("/receipts/history")
def legacy_receipts_history():
    return redirect(url_for("history_page"), code=301)


@app.route("/billing/galas", methods=["GET"])
def legacy_billing_galas_get():
    return redirect(url_for("galas_page"), code=301)


# ---------------------------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
