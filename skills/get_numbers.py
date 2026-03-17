"""
Skill: get-numbers
Read the pre-extracted text from [company]/[date]_report.txt
and extract financial numbers to produce [company]/[date]_numbers.md

Uses [page X] markers in the text file for fast navigation.
Ripgrep (rg) is available for fast text searching.
"""
import os
from skills.base import BaseSkill
from utils import logger, save_markdown, ensure_company_dir


class GetNumbersSkill(BaseSkill):
    name = "get-numbers"
    description = (
        "Read accounting numbers from the pre-extracted report text "
        "and produce [company]/[date]_numbers.md"
    )

    def _load_report_text(self, company_dir: str, report_date: str) -> str:
        """Load pre-extracted text file, fall back to PDF extraction."""
        txt_path = os.path.join(company_dir, f"{report_date}_report.txt")
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()

        # Fallback: extract from PDF on the fly
        logger.warning(f"No pre-extracted text, falling back to PDF for {report_date}")
        from utils import extract_pdf_text
        pdf_path = os.path.join(company_dir, f"{report_date}.pdf")
        if os.path.exists(pdf_path):
            return extract_pdf_text(pdf_path)
        return ""

    def _find_financial_pages(self, full_text: str) -> str:
        """
        Use [page X] markers to locate financial statement pages.
        Returns a focused excerpt around the financial tables.
        """
        markers = [
            "CONSOLIDATED BALANCE SHEET",
            "CONSOLIDATED STATEMENTS OF INCOME",
            "CONSOLIDATED STATEMENTS OF OPERATIONS",
            "CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME",
            "CONSOLIDATED STATEMENTS OF CASH FLOWS",
            "CONDENSED CONSOLIDATED BALANCE SHEET",
            "CONDENSED CONSOLIDATED STATEMENTS OF INCOME",
            "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS",
            "CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS",
            "FINANCIAL STATEMENTS",
            "Balance Sheets",
            "Statements of Income",
            "Statements of Operations",
            "Statements of Cash Flows",
            "Revenue",
            "Total revenue",
            "Net income",
            "Earnings per share",
        ]

        text_upper = full_text.upper()
        found_positions = []

        for marker in markers:
            pos = text_upper.find(marker.upper())
            if pos != -1:
                found_positions.append(pos)

        if found_positions:
            # Start slightly before the earliest marker
            start = max(0, min(found_positions) - 500)
            # Take a generous chunk
            excerpt = full_text[start:start + 30000]
            return excerpt

        # Fallback: return the middle-to-end section where financials usually are
        mid = len(full_text) // 3
        return full_text[mid:mid + 30000]

    def execute(self, ticker: str, report_date: str, **kwargs) -> dict:
        logger.info(f"Extracting financial numbers for {ticker} report {report_date}")

        company_dir = ensure_company_dir(ticker)

        # Load pre-extracted text
        full_text = self._load_report_text(company_dir, report_date)
        if not full_text:
            return {
                "success": False,
                "error": f"No report text found for {report_date}",
                "ticker": ticker,
                "report_date": report_date,
            }

        financial_text = self._find_financial_pages(full_text)
        logger.info(f"Financial text excerpt: {len(financial_text)} chars")

        system_prompt = """You are a senior financial analyst specializing in reading 
SEC filings (10-Q and 10-K reports). Your task is to extract key financial numbers 
from the report text and organize them into a clear, structured markdown document.

The text contains [page X] markers showing page boundaries — use these to reference
where you found each number.

You also have access to `rg` (ripgrep) for fast text searching if needed.

Generate a markdown document with the following structure:

# Financial Numbers: [Company] - [Period]

## Income Statement
| Metric | Current Period | Prior Period | YoY Change |
|--------|---------------|-------------|------------|
(Extract: Revenue, Cost of Revenue, Gross Profit, Operating Income, Net Income, EPS)

## Balance Sheet
| Metric | Current Period | Prior Period |
|--------|---------------|-------------|
(Extract: Total Assets, Total Liabilities, Total Equity, Cash & Equivalents, Total Debt)

## Cash Flow Statement
| Metric | Current Period | Prior Period |
|--------|---------------|-------------|
(Extract: Operating Cash Flow, Capital Expenditures, Free Cash Flow, Dividends Paid)

## Key Ratios
| Ratio | Value |
|-------|-------|
(Calculate where possible: Gross Margin, Operating Margin, Net Margin, Debt-to-Equity, 
Current Ratio)

## Key Metrics Summary
- Revenue: $X (±Y% YoY)
- Net Income: $X (±Y% YoY)
- EPS: $X (±Y% YoY)
- Free Cash Flow: $X
- Cash Position: $X

## Notable Items
List any extraordinary items, one-time charges, restructuring costs, or 
other notable financial items found in the report.

IMPORTANT RULES:
- Only report numbers you can actually find in the text
- Use exact numbers from the filing - do NOT estimate or fabricate
- If a number is not found, write "Not found in filing"
- Include the units (millions, billions, per share, etc.)
- Note the reporting period clearly (Q1/Q2/Q3/Q4 and fiscal year)
- Reference [page X] where you found key numbers
- If the text does not contain clear financial tables, extract whatever 
  numerical financial data you can find and note limitations"""

        user_prompt = f"""Company: {ticker}
Report date: {report_date}

Here is the extracted text from the SEC filing that contains or is near 
the financial statements:

{financial_text[:25000]}

Extract all key financial numbers and organize them into the markdown 
structure described. Only include numbers you can actually find in this text.
Reference [page X] markers where you found key data."""

        markdown = self.claude.call_for_markdown(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4096,
            temperature=0.1,
        )

        filepath = save_markdown(ticker, f"{report_date}_numbers.md", markdown)
        logger.info(f"Financial numbers saved to {filepath}")

        return {
            "success": True,
            "result": markdown,
            "filepath": filepath,
            "ticker": ticker,
            "report_date": report_date,
        }