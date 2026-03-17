"""
Skill: select-company
Find companies that announced quarterly results today and pick one at random.

Uses web search to find today's earnings announcements, then asks Claude
to parse the results and select a company at random.
"""
import json
import random
import subprocess
import re
from datetime import datetime
from skills.base import BaseSkill
from utils import logger, today_str, ensure_company_dir, save_json


class SelectCompanySkill(BaseSkill):
    name = "select-company"
    description = "Find companies that listed their results today, pick one at random"

    def _search_web(self, query: str) -> str:
        """Run a web search using curl and return results text."""
        try:
            # Use DuckDuckGo HTML search as a simple scraping target
            url = f"https://duckduckgo.com/html/?q={query.replace(' ', '+')}"
            result = subprocess.run(
                [
                    "curl", "-s", "-L",
                    "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    url,
                ],
                capture_output=True, text=True, timeout=30,
            )
            return result.stdout[:10000]
        except Exception as e:
            logger.warning(f"Web search failed: {e}")
            return ""

    def _search_earnings_calendar(self) -> str:
        """Fetch earnings calendar data from Yahoo Finance."""
        today = datetime.now()
        date_str = today.strftime("%Y-%m-%d")
        url = f"https://finance.yahoo.com/calendar/earnings?day={date_str}"
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "-L",
                    "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "-H", "Accept: text/html",
                    url,
                ],
                capture_output=True, text=True, timeout=30,
            )
            return result.stdout[:30000]
        except Exception as e:
            logger.warning(f"Yahoo Finance calendar fetch failed: {e}")
            return ""

    def _search_nasdaq_calendar(self) -> str:
        """Fetch earnings calendar from Nasdaq."""
        today = datetime.now()
        date_str = today.strftime("%Y-%m-%d")
        url = f"https://api.nasdaq.com/api/calendar/earnings?date={date_str}"
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "-L",
                    "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "-H", "Accept: application/json",
                    url,
                ],
                capture_output=True, text=True, timeout=30,
            )
            return result.stdout[:30000]
        except Exception as e:
            logger.warning(f"Nasdaq calendar fetch failed: {e}")
            return ""

    def execute(self, **kwargs) -> dict:
        today = today_str()
        logger.info(f"Searching for companies reporting earnings on {today}")

        # Gather data from multiple sources
        search_results = self._search_web(
            f"companies reporting quarterly earnings results today {today}"
        )
        yahoo_data = self._search_earnings_calendar()
        nasdaq_data = self._search_nasdaq_calendar()

        # Ask Claude to parse and select
        system_prompt = """You are a financial research assistant. Your job is to identify
public companies that have announced their quarterly earnings results today.

You will be given raw web search results and earnings calendar data.
Parse this information and return a JSON response with the following structure:

{
  "date": "YYYY-MM-DD",
  "companies_reporting": [
    {
      "name": "Company Full Name",
      "ticker": "TICKER",
      "exchange": "NYSE/NASDAQ/etc",
      "report_type": "Q1/Q2/Q3/Q4 YYYY",
      "time": "BMO/AMC/During" 
    }
  ],
  "selected_company": {
    "name": "Company Full Name",
    "ticker": "TICKER",
    "exchange": "NYSE/NASDAQ/etc",
    "report_type": "Q1/Q2/Q3/Q4 YYYY",
    "reason": "Why this is an interesting pick"
  }
}

Pick a well-known, large-cap company at random from those reporting today.
If you cannot find specific companies reporting today, use your knowledge of 
the current earnings season to identify companies likely reporting around this 
date and clearly note this in the reason field.

IMPORTANT: You must respond ONLY with valid JSON."""

        user_prompt = f"""Today's date is: {today}

Here are the search results for today's earnings announcements:

--- Web Search Results ---
{search_results[:5000]}

--- Yahoo Finance Calendar ---
{yahoo_data[:8000]}

--- Nasdaq Earnings Calendar ---
{nasdaq_data[:8000]}

Based on these sources, identify companies reporting quarterly results today 
and select one at random. Prefer well-known large-cap companies.
Return your response as valid JSON."""

        result = self.claude.call_with_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
        )

        # Save metadata
        selected = result.get("selected_company", {})
        ticker = selected.get("ticker") or selected.get("symbol") or "UNKNOWN"
        company_dir = ensure_company_dir(ticker)
        save_json(ticker, "company_info.json", result)

        logger.info(
            f"Selected company: {selected.get('name')} ({ticker}) - "
            f"{selected.get('report_type')}"
        )

        return {
            "success": True,
            "result": selected,
            "all_companies": result.get("companies_reporting", []),
            "ticker": ticker,
            "company_name": selected.get("name", "Unknown"),
        }