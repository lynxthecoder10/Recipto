"""Flask application for multi-shop billing and receipt generation."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path

from flask import Flask, render_template, request, send_file, send_from_directory, url_for
from werkzeug.exceptions import NotFound
from werkzeug.utils import secure_filename

from models.db import (
    ReceiptStorageError,
    create_numbered_receipt,
    create_receipt,
    get_numbered_receipt,
    init_db,
    init_receipt_store,
    list_numbered_receipts,
    preview_receipt_number,
)
from utils.pdf_generator import PdfGenerationError, generate_pdf, generate_pdf_bytes


# Keep project paths independent of the directory from which Flask is launched.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
GENERATED_RECEIPTS_DIR = os.path.join(PROJECT_ROOT, "generated_receipts")
DEFAULT_SHOPS = "Main Shop,North Branch,South Branch"


def get_shop_choices() -> tuple[str, ...]:
    """Read configurable shop choices from RECEIPT_SHOPS or use safe defaults."""
    configured_shops = os.getenv("RECEIPT_SHOPS", DEFAULT_SHOPS)
    shops = tuple(shop.strip() for shop in configured_shops.split(",") if shop.strip())
    return shops or ("Main Shop",)


def receipt_for_template(stored_receipt: dict[str, object]) -> tuple[dict[str, object], datetime]:
    """Convert a structured stored receipt into the fields expected by existing templates."""
    receipt_month = datetime(int(stored_receipt["year"]), int(stored_receipt["month"]), 1)
    created_at = str(stored_receipt.get("created_at", ""))
    receipt_date = created_at[:10] or receipt_month.date().isoformat()
    return (
        {
            "bill_number": stored_receipt["receipt_no"],
            "customer_name": stored_receipt["tenant_name"],
            "shop_name": stored_receipt["house_no"],
            "amount": stored_receipt["amount"],
            "date": receipt_date,
        },
        receipt_month,
    )


def index_template_context(form_values: object | None = None, error: str | None = None) -> dict[str, object]:
    """Return common context for the receipt form, including batch-month choices."""
    current_time = datetime.now()
    context: dict[str, object] = {
        "form_values": form_values or {},
        "current_month": f"{current_time.month:02d}",
        "current_year": current_time.year,
        "month_options": [
            (f"{month:02d}", datetime(current_time.year, month, 1).strftime("%B"))
            for month in range(1, 13)
        ],
    }
    if error:
        context["error"] = error
    return context


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY", "change-this-secret-in-production"),
        SHOP_CHOICES=get_shop_choices(),
        GENERATED_RECEIPTS_DIR=GENERATED_RECEIPTS_DIR,
    )

    # Ensure application storage exists before requests are accepted.
    os.makedirs(GENERATED_RECEIPTS_DIR, exist_ok=True)
    init_db()
    init_receipt_store()

    @app.get("/")
    def receipt_form():
        """Display the receipt preview form, optionally prefilled for editing."""
        return render_template(
        "index.html",
            **index_template_context(request.args),
        )

    @app.post("/preview")
    def preview_receipt():
        """Render a receipt as HTML without saving it or generating a PDF."""
        submitted = {
            "tenant_name": request.form.get("tenant_name", "").strip(),
            "amount": request.form.get("amount", "").strip(),
            "house_no": request.form.get("house_no", "").strip(),
            "receipt_type": request.form.get("receipt_type", "").strip().lower(),
        }
        error, receipt_data = validate_pdf_submission(submitted)
        if error:
            return render_template("index.html", **index_template_context(submitted, error)), 400

        current_time = datetime.now()
        try:
            receipt_no = preview_receipt_number(current_time)
        except (sqlite3.Error, ReceiptStorageError):
            return (
                render_template(
                    "index.html",
                    **index_template_context(
                        submitted,
                        "The next receipt number could not be prepared. Please try again.",
                    ),
                ),
                500,
            )

        receipt = {
            "bill_number": receipt_no,
            "customer_name": receipt_data["tenant_name"],
            "shop_name": receipt_data["house_no"],
            "amount": receipt_data["amount"],
            "date": current_time.date().isoformat(),
        }
        template_name = (
            "receipt_signed.html"
            if receipt_data["receipt_type"] == "signed"
            else "receipt_unsigned.html"
        )
        receipt_html = render_template(
            template_name,
            receipt=receipt,
            receipt_no=receipt_no,
            generated_at=current_time.strftime("%d %b %Y, %I:%M %p"),
            rent_month=current_time.strftime("%B %Y"),
            signature_path=url_for("static", filename="signature.png"),
        )
        return render_template(
            "preview.html",
            form_data=submitted,
            receipt_no=receipt_no,
            rent_month=current_time.strftime("%B %Y"),
            receipt_html=receipt_html,
        )

    @app.post("/generate-batch")
    def generate_batch_receipts():
        """Generate one stored receipt PDF for every month in a selected same-year range."""
        submitted = {
            "tenant_name": request.form.get("tenant_name", "").strip(),
            "amount": request.form.get("amount", "").strip(),
            "house_no": request.form.get("house_no", "").strip(),
            "receipt_type": request.form.get("receipt_type", "").strip().lower(),
            "start_month": request.form.get("start_month", "").strip(),
            "end_month": request.form.get("end_month", "").strip(),
            "year": request.form.get("year", "").strip(),
        }
        error, batch_data = validate_batch_submission(submitted)
        if error:
            return render_template("index.html", **index_template_context(submitted, error)), 400

        template_name = (
            "receipt_signed.html"
            if batch_data["receipt_type"] == "signed"
            else "receipt_unsigned.html"
        )
        safe_tenant_name = secure_filename(batch_data["tenant_name"]) or "tenant"
        zip_buffer = BytesIO()

        try:
            with tempfile.TemporaryDirectory(prefix="rent_receipts_") as temporary_directory:
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for month in range(batch_data["start_month"], batch_data["end_month"] + 1):
                        receipt_time = datetime(batch_data["year"], month, 1)
                        stored_receipt = create_numbered_receipt(
                            tenant_name=batch_data["tenant_name"],
                            amount=batch_data["amount"],
                            house_no=batch_data["house_no"],
                            receipt_type=batch_data["receipt_type"],
                            reference_time=receipt_time,
                        )
                        receipt, receipt_month = receipt_for_template(stored_receipt)
                        html = render_template(
                            template_name,
                            receipt=receipt,
                            receipt_no=stored_receipt["receipt_no"],
                            generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
                            rent_month=receipt_month.strftime("%B %Y"),
                            signature_path=Path(
                                os.path.join(PROJECT_ROOT, "static", "signature.png")
                            ).as_uri(),
                        )
                        pdf_bytes = generate_pdf_bytes(html_content=html)
                        pdf_filename = (
                            f"receipt_{safe_tenant_name}_{stored_receipt['receipt_no'].replace('/', '_')}.pdf"
                        )
                        temporary_pdf_path = os.path.join(temporary_directory, pdf_filename)
                        with open(temporary_pdf_path, "wb") as temporary_pdf_file:
                            temporary_pdf_file.write(pdf_bytes)
                        archive.write(temporary_pdf_path, arcname=pdf_filename)
        except ReceiptStorageError as exc:
            return {"error": str(exc)}, 500
        except PdfGenerationError as exc:
            return {"error": str(exc)}, 500
        except OSError:
            return {"error": "The batch PDF archive could not be created."}, 500

        zip_buffer.seek(0)
        archive_filename = f"receipts_{safe_tenant_name}_{batch_data['year']}.zip"
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=archive_filename,
            mimetype="application/zip",
        )

    @app.post("/generate")
    def generate_receipt():
        """Validate input, persist a receipt, and produce its PDF document."""
        submitted = {
            "customer_name": request.form.get("customer_name", "").strip(),
            "shop_name": request.form.get("shop_name", "").strip(),
            "amount": request.form.get("amount", "").strip(),
            "date": request.form.get("date", "").strip(),
            "receipt_type": request.form.get("receipt_type", "unsigned").strip().lower(),
        }

        error, receipt_data = validate_submission(submitted, app.config["SHOP_CHOICES"])
        if error:
            return (
                render_template(
                    "form.html",
                    shops=app.config["SHOP_CHOICES"],
                    error=error,
                    submitted=submitted,
                ),
                400,
            )

        # Save first so every issued bill number has an auditable database record.
        try:
            receipt = create_receipt(**receipt_data)
        except sqlite3.Error:
            return (
                render_template(
                    "form.html",
                    shops=app.config["SHOP_CHOICES"],
                    error="The receipt could not be saved. Please try again.",
                    submitted=submitted,
                ),
                500,
            )
        template_name = (
            "receipt_signed.html"
            if submitted["receipt_type"] == "signed"
            else "receipt_unsigned.html"
        )
        current_rent_month = datetime.now().strftime("%B %Y")
        html = render_template(
            template_name,
            receipt=receipt,
            generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
            rent_month=current_rent_month,
            signature_path=Path(os.path.join(PROJECT_ROOT, "static", "signature.png")).as_uri(),
        )

        try:
            pdf_path = generate_pdf(
                html_content=html,
                bill_number=receipt["bill_number"],
                output_dir=app.config["GENERATED_RECEIPTS_DIR"],
            )
        except PdfGenerationError as exc:
            return (
                render_template(
                    "form.html",
                    shops=app.config["SHOP_CHOICES"],
                    error=(
                        f"Receipt #{receipt['bill_number']} was saved, but its PDF could not "
                        f"be generated. {exc}"
                    ),
                    submitted=submitted,
                ),
                500,
            )

        return render_template(
            "form.html",
            shops=app.config["SHOP_CHOICES"],
            success_message=(
                f"Receipt #{receipt['bill_number']} generated successfully. PDF saved to: {pdf_path}"
            ),
            download_url=url_for(
                "download_receipt", receipt_identifier=os.path.basename(pdf_path)
            ),
        )

    @app.post("/generate-pdf")
    def generate_pdf_download():
        """Render a rent receipt and return it immediately as a PDF download."""
        requested_receipt_no = request.form.get("receipt_no", "").strip()
        if requested_receipt_no:
            try:
                stored_receipt = get_numbered_receipt(requested_receipt_no)
            except sqlite3.Error:
                return {"error": "The receipt database could not be read."}, 500
            if stored_receipt is None:
                return {"error": "Receipt not found."}, 404
        else:
            submitted = {
                "tenant_name": request.form.get("tenant_name", "").strip(),
                "amount": request.form.get("amount", "").strip(),
                "house_no": request.form.get("house_no", "").strip(),
                "receipt_type": request.form.get("receipt_type", "").strip().lower(),
            }
            error, receipt_data = validate_pdf_submission(submitted)
            if error:
                return {"error": error}, 400

            # Save the receipt before generating its PDF so the structured number is persistent.
            try:
                stored_receipt = create_numbered_receipt(
                    tenant_name=receipt_data["tenant_name"],
                    amount=receipt_data["amount"],
                    house_no=receipt_data["house_no"],
                    receipt_type=receipt_data["receipt_type"],
                    reference_time=datetime.now(),
                )
            except ReceiptStorageError as exc:
                return {"error": str(exc)}, 500

        receipt, receipt_month = receipt_for_template(stored_receipt)
        template_name = (
            "receipt_signed.html"
            if stored_receipt["receipt_type"] == "signed"
            else "receipt_unsigned.html"
        )
        generated_at = datetime.now()
        html = render_template(
            template_name,
            receipt=receipt,
            receipt_no=stored_receipt["receipt_no"],
            generated_at=generated_at.strftime("%d %b %Y, %I:%M %p"),
            rent_month=receipt_month.strftime("%B %Y"),
            signature_path=Path(os.path.join(PROJECT_ROOT, "static", "signature.png")).as_uri(),
        )

        try:
            pdf_bytes = generate_pdf_bytes(html_content=html)
        except PdfGenerationError as exc:
            return {"error": str(exc)}, 500

        safe_tenant_name = secure_filename(receipt["customer_name"]) or "tenant"
        filename = f"receipt_{safe_tenant_name}_{receipt_month.strftime('%B_%Y')}.pdf"
        return send_file(
            BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )

    @app.get("/history")
    def receipt_history():
        """Display the browser-driven history view backed by the receipts API."""
        return render_template("history.html")

    @app.get("/receipts")
    def list_receipts():
        """Return all structured receipts in newest-first order."""
        try:
            return {"receipts": list_numbered_receipts()}
        except sqlite3.Error:
            return {"error": "The receipt database could not be read."}, 500

    @app.get("/receipts/<path:receipt_identifier>")
    def download_receipt(receipt_identifier: str):
        """Download a legacy PDF or return a structured receipt by its full number."""
        if receipt_identifier.lower().endswith(".pdf"):
            try:
                return send_from_directory(
                    app.config["GENERATED_RECEIPTS_DIR"],
                    receipt_identifier,
                    as_attachment=True,
                )
            except NotFound:
                return {"error": "Receipt PDF not found."}, 404

        try:
            receipt = get_numbered_receipt(receipt_identifier)
        except sqlite3.Error:
            return {"error": "The receipt database could not be read."}, 500
        if receipt is None:
            return {"error": "Receipt not found."}, 404
        return receipt

    return app


def validate_submission(
    submitted: dict[str, str], shop_choices: tuple[str, ...]
) -> tuple[str | None, dict[str, object] | None]:
    """Validate form fields and normalize values for database storage."""
    if not submitted["customer_name"]:
        return "Customer name is required.", None
    if submitted["shop_name"] not in shop_choices:
        return "Please select a valid shop.", None
    if submitted["receipt_type"] not in {"signed", "unsigned"}:
        return "Please select a valid receipt type.", None

    try:
        amount = Decimal(submitted["amount"])
    except (InvalidOperation, ValueError):
        return "Amount must be a valid number.", None

    if amount <= 0:
        return "Amount must be greater than zero.", None
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if submitted["date"]:
        try:
            receipt_date = date.fromisoformat(submitted["date"]).isoformat()
        except ValueError:
            return "Date must use the YYYY-MM-DD format.", None
    else:
        # Preserve the exact issue time when the user does not provide a date.
        receipt_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return None, {
        "customer_name": submitted["customer_name"],
        "shop_name": submitted["shop_name"],
        "amount": float(amount),
        "receipt_date": receipt_date,
    }


def validate_pdf_submission(submitted: dict[str, str]) -> tuple[str | None, dict[str, object] | None]:
    """Validate the compact form payload accepted by the PDF download endpoint."""
    if not submitted["tenant_name"]:
        return "Tenant name is required.", None
    if not submitted["house_no"]:
        return "House or Gala number is required.", None
    if submitted["receipt_type"] not in {"signed", "unsigned"}:
        return "Receipt type must be signed or unsigned.", None

    try:
        amount = Decimal(submitted["amount"])
    except (InvalidOperation, ValueError):
        return "Amount must be a valid number.", None

    if amount <= 0:
        return "Amount must be greater than zero.", None

    normalized_amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return None, {
        "tenant_name": submitted["tenant_name"],
        "house_no": submitted["house_no"],
        "amount": float(normalized_amount),
        "receipt_type": submitted["receipt_type"],
    }


def validate_batch_submission(
    submitted: dict[str, str]
) -> tuple[str | None, dict[str, object] | None]:
    """Validate core receipt fields plus a same-year inclusive month range."""
    error, receipt_data = validate_pdf_submission(submitted)
    if error:
        return error, None

    try:
        start_month = int(submitted["start_month"])
        end_month = int(submitted["end_month"])
        year = int(submitted["year"])
    except ValueError:
        return "Start month, end month, and year are required.", None

    if not 1 <= start_month <= 12 or not 1 <= end_month <= 12:
        return "Months must be between January and December.", None
    if start_month > end_month:
        return "End month must be the same as or after the start month.", None
    if not 2000 <= year <= 9999:
        return "Please enter a valid four-digit year.", None

    return None, {
        **receipt_data,
        "start_month": start_month,
        "end_month": end_month,
        "year": year,
    }


app = create_app()


if __name__ == "__main__":
    # Bind publicly for Render while retaining a local default of port 5000.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
