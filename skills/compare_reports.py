"""
Skill: compare-reports
Read [company]/[date]_numbers.md, [company]/[date]_goals.md, and
[company]/[date]_tone.md for the two dates provided and generate
a comparison summary into [company]/[date1]_[date2]_compare.md
"""
import os
from skills.base import BaseSkill
from utils import logger, save_markdown, load_markdown, ensure_company_dir


class CompareReportsSkill(BaseSkill):
    name = "compare-reports"
    description = (
        "Compare numbers, goals, and tone across two report dates "
        "and generate [company]/[date1]_[date2]_compare.md"
    )

    def execute(self, ticker: str, report_dates: list, **kwargs) -> dict:
        if len(report_dates) < 2:
            return {
                "success": False,
                "error": "Need at least 2 report dates for comparison",
                "ticker": ticker,
            }

        date_latest = report_dates[0]
        date_prior = report_dates[1]
        logger.info(f"Comparing reports for {ticker}: {date_prior} vs {date_latest}")

        # Load all the analysis files for both dates
        sections = {}
        file_types = ["numbers", "goals", "tone"]

        for date in [date_prior, date_latest]:
            sections[date] = {}
            for ftype in file_types:
                filename = f"{date}_{ftype}.md"
                try:
                    content = load_markdown(ticker, filename)
                    sections[date][ftype] = content
                    logger.info(f"Loaded {filename}: {len(content)} chars")
                except FileNotFoundError:
                    sections[date][ftype] = f"[{ftype} analysis not available for {date}]"
                    logger.warning(f"Missing file: {filename}")

        # Also try to load news summary
        try:
            news = load_markdown(ticker, "news.md")
        except FileNotFoundError:
            news = "[News summary not available]"

        # Try to load price analysis
        try:
            price_analysis = load_markdown(ticker, f"{date_prior}_{date_latest}_price.md")
        except FileNotFoundError:
            price_analysis = "[Price analysis not available]"

        system_prompt = """You are a senior equity research analyst writing a comprehensive 
comparison between two consecutive quarterly/annual reports from the same company.

You have access to four types of analysis for each report period:
1. Financial Numbers (revenue, earnings, ratios, etc.)
2. Strategic Goals (company's stated priorities)
3. Tonal Analysis (how management communicates)
4. Stock Price Analysis (price movements, volatility, sentiment between reports and post-earnings)

Plus recent news about the company's latest earnings.

Your job is to synthesize all of this into a clear, insightful comparison that 
highlights what has changed, what it means, and what to watch going forward.

Generate a markdown document with this structure:

# Comparative Analysis: [Company] — [Earlier Date] vs [Later Date]

## Executive Summary
A 4-5 sentence overview capturing the most important changes between the two 
reporting periods. This should be punchy and insightful.

## Financial Performance Comparison

### Revenue & Growth Trajectory
Compare revenue trends. Is growth accelerating, decelerating, or stable?
What are the key drivers?

### Profitability Trends
Compare margins, operating income, net income. How is the bottom line trending?

### Balance Sheet Health
Compare cash position, debt levels, asset base changes.

### Cash Flow Analysis
Compare operating cash flow, free cash flow, capital allocation decisions.

### Key Anomalies & Red Flags
🔴 List any unusual or concerning patterns in the numbers:
- Sudden margin compression/expansion
- Unexpected one-time items
- Cash flow diverging from earnings
- Unusual balance sheet changes

### Positive Surprises
🟢 List any unexpectedly positive trends:
- Beat expectations
- Improving metrics
- Strengthening position

## Strategic Direction Comparison

### Goals That Persisted
Which strategic goals remained consistent across both periods?
This indicates long-term strategic commitment.

### Goals That Shifted
Which goals were added, dropped, or significantly changed?
This reveals evolving priorities.

### Execution Assessment
Based on the numbers, how well is the company executing against its stated goals?

## Communication & Tone Shifts
Summarize the key tonal changes and what they signal about management confidence.

## Stock Price & Market Reaction
Analyze the stock price movements between the two report periods and the post-earnings
reaction. Key areas to cover:
- How did the stock perform between the two reporting periods?
- What was the immediate post-earnings price reaction?
- Does the price movement align with the financial results and management tone?
- Any divergence between market sentiment (price) and fundamentals (numbers)?
- Volatility trends and what they signal about investor confidence.

## Market Context
How does the company's performance relate to recent news and market reaction?

## Overall Assessment

### Trajectory: [Improving / Stable / Deteriorating]
### Confidence: [High / Medium / Low] based on data quality and consistency
### Key Metric to Watch: [Single most important metric for next quarter]

### Top 3 Takeaways
1. ...
2. ...
3. ...

### Top 3 Questions for Next Quarter
1. ...
2. ...
3. ...

IMPORTANT:
- Synthesize across all data sources - don't just summarize each individually
- Highlight contradictions (e.g., optimistic tone but declining numbers)
- Be specific with numbers and quotes
- Maintain analytical objectivity
- Note data limitations clearly"""

        user_prompt = f"""Company: {ticker}
Earlier Period: {date_prior}
Latest Period: {date_latest}

=== FINANCIAL NUMBERS ({date_prior}) ===
{sections[date_prior]['numbers'][:6000]}

=== FINANCIAL NUMBERS ({date_latest}) ===
{sections[date_latest]['numbers'][:6000]}

=== STRATEGIC GOALS ({date_prior}) ===
{sections[date_prior]['goals'][:4000]}

=== STRATEGIC GOALS ({date_latest}) ===
{sections[date_latest]['goals'][:4000]}

=== TONAL ANALYSIS ({date_latest}) ===
{sections[date_latest]['tone'][:5000]}

=== STOCK PRICE ANALYSIS ===
{price_analysis[:5000]}

=== RECENT NEWS ===
{news[:4000]}

Synthesize all of this analysis into a comprehensive comparative report.
Focus on what changed, what it means, and what to watch going forward.
Highlight any anomalies between the financial reality, the tone of communication,
and the stock price movements. Note whether the market reaction (price) aligns
with the fundamentals (numbers) and management messaging (tone)."""

        markdown = self.claude.call_for_markdown(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=6000,
            temperature=0.3,
        )

        filename = f"{date_prior}_{date_latest}_compare.md"
        filepath = save_markdown(ticker, filename, markdown)
        logger.info(f"Comparison report saved to {filepath}")

        return {
            "success": True,
            "result": markdown,
            "filepath": filepath,
            "ticker": ticker,
            "report_dates": report_dates,
            "comparison_file": filename,
        }