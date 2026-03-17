"""
Skill: get-reports
Download the last 2 financial reports (10-Q or 10-K) from SEC EDGAR
into directories named [company]/[date].pdf
Then extract full text into [company]/[date]_report.txt with [page X] markers.
"""
import os
import re
import json
import subprocess
import time
from skills.base import BaseSkill
from utils import logger, ensure_company_dir, save_json
from config import SEC_USER_AGENT


class GetReportsSkill(BaseSkill):
    name = "get-reports"
    description = (
        "Download the last 2 financial reports from SEC EDGAR "
        "into [company]/[date].pdf"
    )

    def _sec_request(self, url: str) -> str:
        """Make an SEC EDGAR request with proper User-Agent."""
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "-L",
                    "-H", f"User-Agent: {SEC_USER_AGENT}",
                    "-H", "Accept: application/json, text/html",
                    "--max-time", "30",
                    url,
                ],
                capture_output=True, text=True, timeout=45,
            )
            return result.stdout
        except Exception as e:
            logger.warning(f"SEC request failed for {url}: {e}")
            return ""

    def _download_file(self, url: str, output_path: str) -> bool:
        """Download a file from a URL."""
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "-L",
                    "-H", f"User-Agent: {SEC_USER_AGENT}",
                    "--max-time", "120",
                    "-o", output_path,
                    url,
                ],
                capture_output=True, text=True, timeout=150,
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                logger.info(f"Downloaded: {output_path} ({os.path.getsize(output_path)} bytes)")
                return True
            else:
                logger.warning(f"Download too small or missing: {output_path}")
                if os.path.exists(output_path):
                    os.remove(output_path)
                return False
        except Exception as e:
            logger.warning(f"Download failed for {url}: {e}")
            return False

    def _convert_html_to_pdf(self, html_path: str, pdf_path: str) -> bool:
        """Convert an HTML filing to PDF using wkhtmltopdf."""
        try:
            result = subprocess.run(
                [
                    "wkhtmltopdf",
                    "--quiet",
                    "--page-size", "A4",
                    "--margin-top", "10mm",
                    "--margin-bottom", "10mm",
                    "--margin-left", "10mm",
                    "--margin-right", "10mm",
                    "--disable-javascript",
                    html_path, pdf_path,
                ],
                capture_output=True, text=True, timeout=120,
            )
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
                logger.info(f"Converted to PDF: {pdf_path}")
                return True
            return False
        except Exception as e:
            logger.warning(f"HTML to PDF conversion failed: {e}")
            return False

    def _search_sec_efts(self, ticker: str) -> list:
        """Search SEC EDGAR full-text search for recent 10-Q/10-K filings."""
        url = (
            f"https://efts.sec.gov/LATEST/search-index?"
            f"q=%22{ticker}%22&dateRange=custom&"
            f"forms=10-Q,10-K&from=0&size=6"
        )
        raw = self._sec_request(url)
        try:
            data = json.loads(raw)
            return data.get("hits", {}).get("hits", [])
        except (json.JSONDecodeError, AttributeError):
            return []

    def _search_sec_submissions(self, ticker: str) -> list:
        """Search SEC EDGAR company submissions API."""
        # First get the CIK number
        tickers_url = "https://www.sec.gov/files/company_tickers.json"
        raw = self._sec_request(tickers_url)
        cik = None
        try:
            tickers_data = json.loads(raw)
            for entry in tickers_data.values():
                if entry.get("ticker", "").upper() == ticker.upper():
                    cik = str(entry["cik_str"]).zfill(10)
                    break
        except (json.JSONDecodeError, AttributeError):
            pass

        if not cik:
            logger.warning(f"Could not find CIK for {ticker}")
            return []

        logger.info(f"Found CIK for {ticker}: {cik}")

        # Get submissions
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        raw = self._sec_request(submissions_url)
        time.sleep(0.5)  # Rate limiting

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, AttributeError):
            logger.warning(f"Failed to parse submissions for {ticker}")
            return []

        # Extract recent 10-Q and 10-K filings
        filings = []
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        for i, form in enumerate(forms):
            if form in ("10-Q", "10-K") and i < len(dates):
                filing = {
                    "form": form,
                    "date": dates[i],
                    "accession": accessions[i] if i < len(accessions) else "",
                    "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
                    "cik": cik,
                }
                filings.append(filing)
                if len(filings) >= 2:
                    break

        return filings

    def _download_filing(self, filing: dict, company_dir: str) -> dict:
        """Download a single SEC filing and convert to PDF if needed."""
        cik = filing["cik"].lstrip("0")
        accession_clean = filing["accession"].replace("-", "")
        primary_doc = filing["primary_doc"]
        date = filing["date"]

        # Build the document URL
        doc_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik}/{accession_clean}/{primary_doc}"
        )
        logger.info(f"Downloading filing from: {doc_url}")

        # Determine file extension
        ext = os.path.splitext(primary_doc)[1].lower()
        temp_path = os.path.join(company_dir, f"{date}_temp{ext}")
        pdf_path = os.path.join(company_dir, f"{date}.pdf")

        # If it's already a PDF
        if ext == ".pdf":
            if self._download_file(doc_url, pdf_path):
                return {"success": True, "path": pdf_path, "date": date}
        else:
            # Download HTML and convert
            if self._download_file(doc_url, temp_path):
                if self._convert_html_to_pdf(temp_path, pdf_path):
                    # Clean up temp file
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    return {"success": True, "path": pdf_path, "date": date}
                else:
                    # If conversion fails, try the filing index to find PDF
                    logger.info("HTML conversion failed, searching for direct PDF link...")
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

        # Fallback: try to find a PDF in the filing index
        index_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik}/{accession_clean}/"
        )
        index_html = self._sec_request(index_url)
        time.sleep(0.3)

        # Look for PDF links
        pdf_links = re.findall(r'href="([^"]*\.pdf)"', index_html, re.IGNORECASE)
        for pdf_link in pdf_links:
            if not pdf_link.startswith("http"):
                pdf_link = f"https://www.sec.gov{pdf_link}"
            if self._download_file(pdf_link, pdf_path):
                return {"success": True, "path": pdf_path, "date": date}

        # Last resort: save HTML as-is and convert
        if not os.path.exists(pdf_path):
            # Download the main filing page itself
            main_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik}/{accession_clean}/{primary_doc}"
            )
            html_path = os.path.join(company_dir, f"{date}_filing.html")
            if self._download_file(main_url, html_path):
                if self._convert_html_to_pdf(html_path, pdf_path):
                    try:
                        os.remove(html_path)
                    except OSError:
                        pass
                    return {"success": True, "path": pdf_path, "date": date}

        return {"success": False, "path": None, "date": date}

    def _extract_text_with_page_markers(self, pdf_path: str, txt_path: str) -> bool:
        """
        Extract text from PDF page-by-page, inserting [page X] markers
        at each page boundary. Saves to txt_path.
        """
        try:
            # Get page count
            info = subprocess.run(
                ["pdfinfo", pdf_path],
                capture_output=True, text=True, timeout=30,
            )
            page_count = 0
            for line in info.stdout.split("\n"):
                if line.startswith("Pages:"):
                    page_count = int(line.split(":")[1].strip())
                    break

            if page_count == 0:
                logger.warning(f"Could not determine page count for {pdf_path}")
                # Fallback: extract all at once with a single marker
                result = subprocess.run(
                    ["pdftotext", "-layout", pdf_path, "-"],
                    capture_output=True, text=True, timeout=120,
                )
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write("[page 1]\n")
                    f.write(result.stdout)
                return True

            # Extract page by page
            with open(txt_path, "w", encoding="utf-8") as f:
                for page_num in range(1, page_count + 1):
                    result = subprocess.run(
                        [
                            "pdftotext", "-layout",
                            "-f", str(page_num),
                            "-l", str(page_num),
                            pdf_path, "-",
                        ],
                        capture_output=True, text=True, timeout=60,
                    )
                    f.write(f"\n[page {page_num}]\n")
                    f.write(result.stdout)

            file_size = os.path.getsize(txt_path)
            logger.info(
                f"Extracted text with page markers: {txt_path} "
                f"({page_count} pages, {file_size} bytes)"
            )
            return True

        except Exception as e:
            logger.error(f"Text extraction failed for {pdf_path}: {e}")
            return False

    def execute(self, ticker: str, company_name: str = "", **kwargs) -> dict:
        logger.info(f"Getting last 2 financial reports for {ticker}")
        company_dir = ensure_company_dir(ticker)

        # Search SEC EDGAR for filings
        filings = self._search_sec_submissions(ticker)

        if not filings:
            logger.warning(f"No filings found via submissions API, trying EFTS...")
            # Could try alternate search here
            return {
                "success": False,
                "error": f"No 10-Q/10-K filings found for {ticker}",
                "ticker": ticker,
                "reports": [],
            }

        logger.info(f"Found {len(filings)} filings for {ticker}")
        for f in filings:
            logger.info(f"  {f['form']} filed {f['date']}: {f['primary_doc']}")

        # Download each filing
        downloaded = []
        for filing in filings[:2]:
            time.sleep(1)  # SEC rate limiting: max 10 req/sec
            result = self._download_filing(filing, company_dir)
            downloaded.append({
                "form": filing["form"],
                "filing_date": filing["date"],
                "downloaded": result["success"],
                "pdf_path": result["path"],
                "date": result["date"],
            })

        # Save metadata
        save_json(ticker, "reports_metadata.json", {
            "ticker": ticker,
            "company_name": company_name,
            "filings_found": len(filings),
            "reports_downloaded": [d for d in downloaded if d["downloaded"]],
            "all_attempts": downloaded,
        })

        successful = [d for d in downloaded if d["downloaded"]]
        dates = [d["date"] for d in successful]

        logger.info(
            f"Downloaded {len(successful)}/{len(downloaded)} reports for {ticker}: "
            f"{dates}"
        )

        # Extract text from each PDF with [page X] markers
        for d in successful:
            pdf_path = d["pdf_path"]
            date = d["date"]
            txt_path = os.path.join(company_dir, f"{date}_report.txt")
            if pdf_path and os.path.exists(pdf_path):
                ok = self._extract_text_with_page_markers(pdf_path, txt_path)
                d["txt_path"] = txt_path if ok else None
                if ok:
                    logger.info(f"Text extracted: {txt_path}")
                else:
                    logger.warning(f"Text extraction failed for {date}")

        return {
            "success": len(successful) > 0,
            "result": successful,
            "ticker": ticker,
            "report_dates": dates,
            "reports": successful,
        }