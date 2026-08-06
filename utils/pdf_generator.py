"""PDF creation helpers backed by pdfkit and the wkhtmltopdf executable."""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
from datetime import datetime

import pdfkit


DEFAULT_WINDOWS_WKHTMLTOPDF_PATH = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
DEFAULT_LINUX_WKHTMLTOPDF_PATH = "/usr/bin/wkhtmltopdf"


class PdfGenerationError(RuntimeError):
    """Raised when a receipt cannot be converted into a PDF."""


def get_wkhtmltopdf_path() -> str | None:
    """Find wkhtmltopdf from the environment, then the current platform's standard path."""
    configured_path = os.getenv("WKHTMLTOPDF_PATH", "").strip().strip('"')
    system_path = (
        DEFAULT_LINUX_WKHTMLTOPDF_PATH
        if platform.system() == "Linux"
        else DEFAULT_WINDOWS_WKHTMLTOPDF_PATH
    )
    candidate_paths = (configured_path, system_path, shutil.which("wkhtmltopdf"))

    for candidate_path in candidate_paths:
        if candidate_path and os.path.isfile(candidate_path):
            return os.path.abspath(candidate_path)
    return None


def get_pdfkit_configuration() -> pdfkit.configuration:
    """Build a pdfkit configuration using the detected Windows executable path."""
    wkhtmltopdf_path = get_wkhtmltopdf_path()
    print(f"[pdf_generator] wkhtmltopdf path: {wkhtmltopdf_path or 'not found'}")
    if not wkhtmltopdf_path:
        raise PdfGenerationError(
            "wkhtmltopdf was not found. Set WKHTMLTOPDF_PATH, install it at "
            f"{DEFAULT_LINUX_WKHTMLTOPDF_PATH} on Linux, or install it at "
            f"{DEFAULT_WINDOWS_WKHTMLTOPDF_PATH} on Windows."
        )
    return pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)


def generate_pdf_bytes(*, html_content: str) -> bytes:
    """Convert rendered receipt HTML to PDF bytes for a Flask download response."""
    options = {
        "page-size": "A4",
        "margin-top": "0mm",
        "margin-right": "0mm",
        "margin-bottom": "0mm",
        "margin-left": "0mm",
        "encoding": "UTF-8",
        "enable-local-file-access": None,
    }

    try:
        pdf_bytes = pdfkit.from_string(
            html_content,
            False,
            configuration=get_pdfkit_configuration(),
            options=options,
        )
    except PdfGenerationError:
        raise
    except (OSError, IOError) as exc:
        raise PdfGenerationError(f"wkhtmltopdf failed to generate the PDF: {exc}") from exc

    if not pdf_bytes:
        raise PdfGenerationError("wkhtmltopdf did not return a valid PDF document.")
    return bytes(pdf_bytes)


def generate_pdf(*, html_content: str, bill_number: int, output_dir: str | os.PathLike[str]) -> str:
    """Write receipt HTML to a temporary file and convert it to a unique A4 PDF.

    The temporary HTML file is always removed after conversion, including when
    wkhtmltopdf reports an error.
    """
    absolute_output_dir = os.path.abspath(os.fspath(output_dir))
    os.makedirs(absolute_output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    pdf_filename = f"receipt_{bill_number}_{timestamp}.pdf"
    pdf_path = os.path.join(absolute_output_dir, pdf_filename)
    temporary_html_path = ""
    options = {
        "page-size": "A4",
        "margin-top": "12mm",
        "margin-right": "12mm",
        "margin-bottom": "12mm",
        "margin-left": "12mm",
        "encoding": "UTF-8",
        "enable-local-file-access": None,
    }

    try:
        # Create a physical HTML document so local static assets work reliably on Windows.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".html",
            prefix=f"receipt_{bill_number}_",
            dir=absolute_output_dir,
            delete=False,
        ) as temporary_html_file:
            temporary_html_file.write(html_content)
            temporary_html_path = temporary_html_file.name

        if not os.path.isfile(temporary_html_path) or os.path.getsize(temporary_html_path) == 0:
            raise PdfGenerationError("Temporary receipt HTML could not be created.")

        configuration = get_pdfkit_configuration()
        pdfkit.from_file(
            temporary_html_path,
            pdf_path,
            configuration=configuration,
            options=options,
        )
    except PdfGenerationError:
        raise
    except (OSError, IOError) as exc:
        raise PdfGenerationError(f"wkhtmltopdf failed to generate the PDF: {exc}") from exc
    finally:
        if temporary_html_path and os.path.exists(temporary_html_path):
            os.remove(temporary_html_path)

    if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
        raise PdfGenerationError("wkhtmltopdf did not create a valid PDF file.")

    return pdf_path
