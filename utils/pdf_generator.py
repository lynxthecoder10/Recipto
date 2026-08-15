"""PDF generation utilities.

Primary Engine: Headless Browser HTML-to-PDF (MS Edge / Chrome) for pixel-perfect receipt book rendering.
Fallback Engines: wkhtmltopdf, ReportLab.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class PdfGenerationError(Exception):
    """Raised when PDF creation fails completely."""


def _generate_pdf_edge(html_content: str) -> bytes:
    """Render HTML to PDF using MS Edge / Chrome Headless for pixel-perfect visual fidelity."""
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    executable = next((p for p in edge_paths if Path(p).is_file()), None)
    if not executable:
        print("[pdf_generator] ERROR: No Edge/Chrome executable found on system!")
        raise PdfGenerationError("No browser executable found for HTML rendering.")

    print(f"[pdf_generator] Using Edge HTML renderer for receipt_signed.html")
    print(f"[pdf_generator] Edge executable found: {executable}")
    print(f"[pdf_generator] Rendering receipt_signed.html")

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".html", encoding="utf-8") as html_file:
        html_file.write(html_content)
        html_path = html_file.name

    pdf_path = html_path.replace(".html", ".pdf")
    file_url = Path(html_path).as_uri()

    try:
        cmd = [
            executable,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            file_url,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if result.returncode != 0 or not Path(pdf_path).is_file():
            err_msg = result.stderr.decode("utf-8", errors="ignore")
            print(f"[pdf_generator] Browser PDF printing failed: {err_msg}")
            raise PdfGenerationError(f"Browser PDF printing failed: {err_msg}")

        with open(pdf_path, "rb") as pf:
            pdf_bytes = pf.read()
        print("[pdf_generator] PDF generated successfully via Edge HTML renderer")
        return pdf_bytes
    finally:
        for p in (html_path, pdf_path):
            try:
                if Path(p).is_file():
                    os.remove(p)
            except OSError:
                pass


def _extract_field(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        raw = match.group(1)
        clean = re.sub(r"<[^>]+>", "", raw).strip()
        clean = clean.replace("<br/>", " ").replace("<br>", " ")
        return clean or default
    return default


def _generate_pdf_reportlab(html_content: str) -> bytes:
    """Fallback ReportLab PDF generator matching physical receipt layout."""
    bill_no = _extract_field(r'Bill No\..*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_content, "BILL-0001")
    date = _extract_field(r'Date.*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_content, datetime.now().strftime("%d/%m/%Y"))
    tenant = _extract_field(r'Tenant Name.*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_content, "Tenant")
    house = _extract_field(r'Gala No\..*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_content, "Gala")
    monthly_rent = _extract_field(r'Monthly Rent.*?<td[^>]*class=["\']value["\'][^>]*>(?:Rs\.\s*)?(.*?)</td>', html_content, "0.00")
    period = _extract_field(r'Billing Period.*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_content, "Period")
    num_months = _extract_field(r'Months.*?<td[^>]*class=["\']value["\'][^>]*>(.*?)</td>', html_content, "1")
    total_amount = _extract_field(r'Total Amount.*?<td[^>]*class=["\']value["\'][^>]*>.*?Rs\.\s*([\d\.]+)', html_content, "")

    if not total_amount:
        total_amount = _extract_field(r'sum of <strong>Rs\.\s*([\d\.]+)', html_content, "0.00")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    header_title_style = ParagraphStyle("HeaderTitle", parent=styles["Title"], fontSize=14, leading=16, alignment=1)
    header_sub_style = ParagraphStyle("HeaderSub", parent=styles["Normal"], fontSize=9, leading=12, alignment=1)
    section_title_style = ParagraphStyle("SecTitle", parent=styles["Heading2"], fontSize=12, leading=14, alignment=1)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14)
    right_style = ParagraphStyle("Right", parent=styles["Normal"], fontSize=8, leading=10, alignment=2)

    def build_receipt_block(copy_label: str):
        block = []
        block.append(Paragraph(copy_label.upper(), right_style))
        block.append(Spacer(1, 2))
        block.append(Paragraph("<b>M. KANDA KUMARAN</b>", header_title_style))
        block.append(Paragraph("PROPRIETOR", header_sub_style))
        block.append(Paragraph("Thiru Murugan Compound, Lake Road, Bhandup (W), Mumbai - 400078", header_sub_style))
        block.append(Spacer(1, 4))
        block.append(Paragraph("<b>RENT RECEIPT</b>", section_title_style))
        block.append(HRFlowable(width="100%", thickness=1.5, color=colors.black, spaceAfter=8, spaceBefore=4))

        table_data = [
            [Paragraph("<b>Bill No.</b>", body_style), Paragraph(bill_no, body_style), Paragraph("<b>Date</b>", body_style), Paragraph(date, body_style)],
            [Paragraph("<b>Tenant Name</b>", body_style), Paragraph(tenant, body_style), "", ""],
            [Paragraph("<b>Gala No.</b>", body_style), Paragraph(house, body_style), Paragraph("<b>Monthly Rent</b>", body_style), Paragraph(f"Rs. {monthly_rent}", body_style)],
            [Paragraph("<b>Billing Period</b>", body_style), Paragraph(period, body_style), Paragraph("<b>Months</b>", body_style), Paragraph(num_months, body_style)],
            [Paragraph("<b>Total Amount</b>", body_style), Paragraph(f"<b>Rs. {total_amount}</b>", body_style), "", ""],
        ]

        t = Table(table_data, colWidths=[100, 180, 80, 140])
        t.setStyle(TableStyle([
            ("SPAN", (1, 1), (3, 1)),
            ("SPAN", (1, 4), (3, 4)),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("LINEBELOW", (1, 0), (1, 0), 0.5, colors.black),
            ("LINEBELOW", (3, 0), (3, 0), 0.5, colors.black),
            ("LINEBELOW", (1, 1), (3, 1), 0.5, colors.black),
            ("LINEBELOW", (1, 2), (1, 2), 0.5, colors.black),
            ("LINEBELOW", (3, 2), (3, 2), 0.5, colors.black),
            ("LINEBELOW", (1, 3), (1, 3), 0.5, colors.black),
            ("LINEBELOW", (3, 3), (3, 3), 0.5, colors.black),
            ("LINEBELOW", (1, 4), (3, 4), 0.5, colors.black),
        ]))
        block.append(t)
        block.append(Spacer(1, 8))

        stmt = f"Received from <b>{tenant}</b> a sum of <b>Rs. {total_amount}</b> (Monthly Rent: Rs. {monthly_rent} × {num_months} Month(s)) for the rent of Gala No. <b>{house}</b> for the period <b>{period}</b>."
        block.append(Paragraph(stmt, body_style))
        block.append(Spacer(1, 10))

        footer_data = [
            [Paragraph("___________________<br/><b>Received By</b>", body_style), Paragraph("___________________<br/><b>Date</b>", body_style), Paragraph("___________________<br/><b>Signature</b>", body_style)]
        ]
        ft = Table(footer_data, colWidths=[160, 160, 180])
        block.append(ft)

        rules_style = ParagraphStyle("Rules", parent=styles["Normal"], fontSize=8, leading=10)
        block.append(Spacer(1, 6))
        block.append(Paragraph("<b>Rules & Conditions:</b>", rules_style))
        block.append(Paragraph("1. This receipt is valid only for the amount and period stated above.<br/>2. Please preserve this receipt for your records.<br/>3. Subject to Mumbai Jurisdiction.", rules_style))
        return block

    story = []
    story.extend(build_receipt_block("Owner Copy"))
    story.append(Spacer(1, 15))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black, spaceAfter=15, dash=[4, 4]))
    story.extend(build_receipt_block("Tenant Copy"))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    if not pdf_bytes:
        raise PdfGenerationError("ReportLab did not return a valid PDF document.")
    return bytes(pdf_bytes)


def generate_pdf_bytes(*, html_content: str) -> bytes:
    """Convert rendered receipt HTML to PDF bytes for a Flask download response.

    Uses Headless Browser (MS Edge / Chrome) for pixel-perfect HTML/CSS rendering.
    Falls back to ReportLab if no browser is available.
    """
    try:
        pdf_bytes = _generate_pdf_edge(html_content)
        print("[pdf_generator] Pixel-perfect PDF generated via Edge Headless HTML engine")
        return pdf_bytes
    except Exception as exc:
        print(f"[pdf_generator] Browser rendering fallback due to: {exc}. Using ReportLab engine.")
        return _generate_pdf_reportlab(html_content)
