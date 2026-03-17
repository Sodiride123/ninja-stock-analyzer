"""
Skill: extract-goals
Read the first 10 pages from [company]/[date]_report.txt and extract the most
important 5 goals listed by the company. Generate [company]/[date]_goals.md

Uses [page X] markers in the text file for fast navigation.
Ripgrep (rg) is available for fast text searching.
"""
import os
import re
from skills.base import BaseSkill
from utils import logger, save_markdown, ensure_company_dir


class ExtractGoalsSkill(BaseSkill):
    name = "extract-goals"
    description = (
        "Read the first 10 pages of the report and extract the "
        "5 most important goals into [company]/[date]_goals.md"
    )

    def _load_first_n_pages(self, company_dir: str, report_date: str, n: int = 10) -> str:
        """Load first N pages from pre-extracted text using [page X] markers."""
        txt_path = os.path.join(company_dir, f"{report_date}_report.txt")
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                full_text = f.read()

            # Find the [page N+1] marker and truncate there
            marker = f"[page {n + 1}]"
            pos = full_text.find(marker)
            if pos != -1:
                return full_text[:pos]
            return full_text  # fewer than N pages, return all

        # Fallback: extract from PDF on the fly
        logger.warning(f"No pre-extracted text, falling back to PDF for {report_date}")
        from utils import extract_pdf_text
        pdf_path = os.path.join(company_dir, f"{report_date}.pdf")
        if os.path.exists(pdf_path):
            return extract_pdf_text(pdf_path, first_n_pages=n)
        return ""

    def execute(self, ticker: str, report_date: str, **kwargs) -> dict:
        logger.info(f"Extracting goals for {ticker} report {report_date}")

        company_dir = ensure_company_dir(ticker)

        # Load first 10 pages from pre-extracted text
        text = self._load_first_n_pages(company_dir, report_date, n=10)
        if not text:
            return {
                "success": False,
                "error": f"No report text found for {report_date}",
                "ticker": ticker,
                "report_date": report_date,
            }

        logger.info(f"Loaded {len(text)} chars from first 10 pages")

        system_prompt = """You are a senior corporate strategy analyst. Your task is to 
read the opening sections of a quarterly/annual SEC filing and identify the company's 
top 5 strategic goals and priorities.

The text contains [page X] markers showing page boundaries — use these to reference
where you found each goal.

You also have access to `rg` (ripgrep) for fast text searching if needed.

In SEC filings, companies typically discuss their strategic direction in:
- The cover letter / overview section
- Management Discussion & Analysis (MD&A)
- Business overview sections
- Risk factors (which reveal priorities by what they worry about)
- Forward-looking statements

Generate a markdown document with this structure:

# Strategic Goals: [Company] - [Period]

## Overview
A 2-3 sentence summary of the company's overall strategic direction as 
communicated in this filing.

## Top 5 Strategic Goals

### 1. [Goal Title]
**Priority Level:** High / Medium
**Category:** Growth / Profitability / Innovation / Market Expansion / Cost Optimization / ESG / Other
**Description:** A detailed paragraph explaining this goal, what the company 
said about it, and any specific targets or timelines mentioned.
**Evidence from Filing:** Direct quotes or close paraphrases from the text, with [page X] references.

### 2. [Goal Title]
(same structure)

### 3. [Goal Title]
(same structure)

### 4. [Goal Title]
(same structure)

### 5. [Goal Title]
(same structure)

## Forward-Looking Statements
Summarize any forward-looking guidance or predictions the company makes 
about their future performance.

## Risk Factors Highlighting Priorities
List 2-3 risk factors that reveal what the company considers most critical 
to protect or achieve.

IMPORTANT:
- Only extract goals that are explicitly stated or strongly implied in the text
- Use direct evidence from the filing with [page X] references
- Do NOT invent goals that aren't supported by the text
- If fewer than 5 clear goals are found, list only what you find and note the limitation"""

        user_prompt = f"""Company: {ticker}
Report date: {report_date}

Here is the text from the first 10 pages of the SEC filing:

{text[:25000]}

Analyze this text and extract the company's top 5 strategic goals and priorities.
Ground every goal in specific evidence from the text, referencing [page X] markers."""

        markdown = self.claude.call_for_markdown(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4096,
            temperature=0.2,
        )

        filepath = save_markdown(ticker, f"{report_date}_goals.md", markdown)
        logger.info(f"Goals saved to {filepath}")

        return {
            "success": True,
            "result": markdown,
            "filepath": filepath,
            "ticker": ticker,
            "report_date": report_date,
        }