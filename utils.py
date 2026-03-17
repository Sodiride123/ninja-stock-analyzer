"""
Shared utilities for the Quarterly Earnings Research Application.
"""
import os
import re
import json
import subprocess
import logging
from datetime import datetime
from config import REPORTS_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("earnings_app")


def ensure_company_dir(company_ticker: str) -> str:
    """Create and return the company-specific directory path."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", company_ticker.upper())
    company_dir = os.path.join(REPORTS_DIR, safe_name)
    os.makedirs(company_dir, exist_ok=True)
    return company_dir


def save_markdown(company_ticker: str, filename: str, content: str) -> str:
    """Save markdown content to the company directory."""
    company_dir = ensure_company_dir(company_ticker)
    filepath = os.path.join(company_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Saved markdown: {filepath}")
    return filepath


def load_markdown(company_ticker: str, filename: str) -> str:
    """Load markdown content from the company directory."""
    company_dir = ensure_company_dir(company_ticker)
    filepath = os.path.join(company_dir, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def save_json(company_ticker: str, filename: str, data: dict) -> str:
    """Save JSON data to the company directory."""
    company_dir = ensure_company_dir(company_ticker)
    filepath = os.path.join(company_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"Saved JSON: {filepath}")
    return filepath


def load_json(company_ticker: str, filename: str) -> dict:
    """Load JSON data from the company directory."""
    company_dir = ensure_company_dir(company_ticker)
    filepath = os.path.join(company_dir, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_pdf_text(pdf_path: str, first_n_pages: int = None) -> str:
    """Extract text from a PDF file using pdftotext."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    cmd = ["pdftotext", "-layout"]
    if first_n_pages:
        cmd.extend(["-l", str(first_n_pages)])
    cmd.extend([pdf_path, "-"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr}")
    return result.stdout


def extract_pdf_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    result = subprocess.run(
        ["pdfinfo", pdf_path], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdfinfo failed: {result.stderr}")
    for line in result.stdout.split("\n"):
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    return 0


def format_date(date_str: str) -> str:
    """Normalize a date string to YYYY-MM-DD format."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def today_str() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name)


def list_company_files(company_ticker: str) -> list:
    """List all files in a company's directory."""
    company_dir = ensure_company_dir(company_ticker)
    if not os.path.exists(company_dir):
        return []
    return sorted(os.listdir(company_dir))


def get_report_dates(company_ticker: str) -> list:
    """Get the dates of downloaded reports for a company."""
    files = list_company_files(company_ticker)
    dates = []
    for f in files:
        if f.endswith(".pdf"):
            # Extract date from filename like 2024-01-25.pdf
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", f)
            if date_match:
                dates.append(date_match.group(1))
    return sorted(dates)