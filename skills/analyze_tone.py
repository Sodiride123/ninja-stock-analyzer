"""
Skill: analyze-tone
Read the first 10 pages of the financial reports for the latest two reports
and compare the messages using tonal analysis. Generate [company]/[date]_tone.md

Uses [page X] markers in the pre-extracted text files for fast navigation.
Ripgrep (rg) is available for fast text searching.
"""
import os
import re
from skills.base import BaseSkill
from utils import logger, save_markdown, ensure_company_dir


class AnalyzeToneSkill(BaseSkill):
    name = "analyze-tone"
    description = (
        "Read the first 10 pages of the two latest reports and compare "
        "their messaging tone. Generate [company]/[date]_tone.md"
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

    def execute(self, ticker: str, report_dates: list, **kwargs) -> dict:
        if len(report_dates) < 2:
            return {
                "success": False,
                "error": "Need at least 2 report dates for tonal comparison",
                "ticker": ticker,
            }

        date_latest = report_dates[0]
        date_prior = report_dates[1]
        logger.info(
            f"Analyzing tone shift for {ticker}: {date_prior} → {date_latest}"
        )

        company_dir = ensure_company_dir(ticker)

        # Load first 10 pages from pre-extracted text for both reports
        texts = {}
        for date in [date_latest, date_prior]:
            text = self._load_first_n_pages(company_dir, date, n=10)
            if not text:
                return {
                    "success": False,
                    "error": f"No report text found for {date}",
                    "ticker": ticker,
                }
            texts[date] = text
            logger.info(f"Loaded {len(texts[date])} chars from {date} (first 10 pages)")

        system_prompt = """You are an expert in corporate communications analysis and 
linguistic sentiment analysis. Your task is to perform a detailed tonal analysis 
comparing two consecutive quarterly/annual SEC filings from the same company.

The text contains [page X] markers showing page boundaries — use these to reference
where you found specific language patterns.

You also have access to `rg` (ripgrep) for fast text searching if needed.

You should analyze:
1. **Overall Sentiment**: Is the tone optimistic, cautious, defensive, confident, etc.?
2. **Confidence Level**: How assertive vs. hedging is the language?
3. **Forward-Looking Language**: How do future predictions differ?
4. **Risk Emphasis**: Has the emphasis on risks shifted?
5. **Word Choice Patterns**: Specific language changes (e.g., "growth" vs. "challenges")
6. **Management Confidence Signals**: Subtle cues about management's confidence

Generate a markdown document with this structure:

# Tonal Analysis: [Company] - [Earlier Date] vs [Later Date]

## Executive Summary
A concise 3-4 sentence summary of the key tonal shifts between the two reports.

## Sentiment Scorecard
| Dimension | Earlier Report | Latest Report | Shift |
|-----------|---------------|---------------|-------|
| Overall Sentiment | X/10 | X/10 | ↑/↓/→ |
| Confidence Level | X/10 | X/10 | ↑/↓/→ |
| Optimism About Future | X/10 | X/10 | ↑/↓/→ |
| Transparency | X/10 | X/10 | ↑/↓/→ |
| Urgency | X/10 | X/10 | ↑/↓/→ |

## Detailed Tonal Comparison

### Language of Growth vs. Caution
Compare how the company discusses growth opportunities across the two filings.
Include specific phrases and word choices with [page X] references.

### Risk Communication Shifts
How has the discussion of risks evolved? Are new risks introduced?
Are previous risks downplayed or escalated?

### Forward-Looking Statement Comparison
Compare the guidance and forward-looking language between the two periods.

### Management Confidence Indicators
Analyze subtle language cues that indicate management's confidence:
- Use of strong vs. weak verbs
- Frequency of hedging language ("may", "might", "could" vs. "will", "expect")
- Specificity of commitments and targets

## Key Tone Shifts
List the 3-5 most significant tonal changes between the reports, with 
direct quotes showing the shift.

| # | Shift Description | Earlier Quote | Latest Quote |
|---|------------------|---------------|--------------|
| 1 | ... | "..." | "..." |

## Implications for Investors
What do these tonal shifts suggest about the company's trajectory 
and management's outlook? What should investors pay attention to?

## Red Flags / Green Flags
### 🟢 Positive Signals
### 🔴 Concerning Signals

IMPORTANT:
- Use direct quotes from the filings to support every observation, with [page X] refs
- Be specific about page/section references when possible
- Maintain objectivity - present observations, not investment advice
- If the text is too limited for full analysis, note limitations clearly"""

        user_prompt = f"""Company: {ticker}

=== EARLIER REPORT ({date_prior}) - First 10 pages ===

{texts[date_prior][:15000]}

=== LATEST REPORT ({date_latest}) - First 10 pages ===

{texts[date_latest][:15000]}

Perform a detailed tonal analysis comparing these two filings.
Focus on how the company's messaging, confidence, and outlook have shifted.
Support every observation with direct quotes from the text, referencing [page X] markers."""

        markdown = self.claude.call_for_markdown(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=5000,
            temperature=0.3,
        )

        # Save with the latest date
        filepath = save_markdown(ticker, f"{date_latest}_tone.md", markdown)
        logger.info(f"Tonal analysis saved to {filepath}")

        return {
            "success": True,
            "result": markdown,
            "filepath": filepath,
            "ticker": ticker,
            "report_dates": report_dates,
        }