"""
Skill: research-company
Search for top 5 news stories about the company to understand their
communication to the market. Summarize the news into [company]/news.md
"""
import subprocess
import re
import xml.etree.ElementTree as ET
from skills.base import BaseSkill
from utils import logger, save_markdown, ensure_company_dir


class ResearchCompanySkill(BaseSkill):
    name = "research-company"
    description = (
        "Search for top 5 news stories about the company and "
        "summarize into [company]/news.md"
    )

    def _fetch_mcp_news(self, ticker: str) -> str:
        """Fetch news via the RapidAPI Real-Time Finance Data stock_news endpoint."""
        try:
            from finance_mcp_client import MCPClient
            client = MCPClient()
            result = client.stock_news(symbol=ticker)
            articles = result.get("data", {}).get("news", [])
            if not articles:
                return ""

            lines = []
            for a in articles[:8]:
                title = a.get("article_title", "")
                source = a.get("source", "")
                url = a.get("article_url", "")
                snippet = a.get("article_text", "")[:200]
                pub = a.get("post_time_utc", "")
                lines.append(
                    f"Title: {title}\n"
                    f"Source: {source}\n"
                    f"Date: {pub}\n"
                    f"URL: {url}\n"
                    f"Snippet: {snippet}\n"
                )
            text = "\n---\n".join(lines)
            logger.info(f"MCP stock_news returned {len(articles)} articles for {ticker}")
            return text
        except Exception as e:
            logger.warning(f"MCP stock_news failed for {ticker}: {e}")
            return ""

    def _fetch_google_news_rss(self, query: str) -> str:
        """Fetch news from Google News RSS feed (no CAPTCHA)."""
        try:
            url = (
                f"https://news.google.com/rss/search?"
                f"q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
            )
            result = subprocess.run(
                [
                    "curl", "-s", "-L",
                    "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "--max-time", "15",
                    url,
                ],
                capture_output=True, text=True, timeout=20,
            )
            if not result.stdout.strip():
                return ""

            # Parse RSS XML
            root = ET.fromstring(result.stdout)
            items = root.findall(".//item")
            lines = []
            for item in items[:5]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                source_el = item.find("source")
                source = source_el.text if source_el is not None else ""
                lines.append(
                    f"Title: {title}\n"
                    f"Source: {source}\n"
                    f"Date: {pub_date}\n"
                    f"URL: {link}\n"
                )
            text = "\n---\n".join(lines)
            logger.info(f"Google News RSS returned {len(items)} items for query: {query}")
            return text
        except Exception as e:
            logger.warning(f"Google News RSS failed for {query}: {e}")
            return ""

    def execute(self, ticker: str, company_name: str, **kwargs) -> dict:
        logger.info(f"Researching news for {company_name} ({ticker})")

        # Primary source: MCP stock_news API (real-time, structured)
        mcp_news = self._fetch_mcp_news(ticker)

        # Secondary source: Google News RSS (broader coverage)
        rss_news = self._fetch_google_news_rss(
            f"{company_name} {ticker} quarterly earnings"
        )

        combined_search = ""
        if mcp_news:
            combined_search += f"--- Real-Time Finance News for {ticker} ---\n{mcp_news}\n\n"
        if rss_news:
            combined_search += f"--- Google News Results ---\n{rss_news}\n\n"

        if not combined_search.strip():
            logger.warning(f"No news sources returned data for {ticker}")
            combined_search = "(No news data could be retrieved from any source.)"

        # Ask Claude to analyze and summarize
        system_prompt = """You are a senior financial journalist and market analyst.
Your task is to analyze news articles about a company's recent quarterly earnings
announcement and produce a comprehensive news summary.

Generate a well-structured markdown document with the following sections:

# [Company Name] ([TICKER]) - Earnings News Summary

## Key Headlines
List the top 5 most important news stories with brief descriptions.

## Earnings Results Overview
Summarize the key financial metrics announced (revenue, EPS, guidance, etc.)

## Market Reaction
Describe how the stock market and investors reacted to the earnings.

## Analyst Opinions
Summarize key analyst upgrades/downgrades and opinions.

## Key Takeaways
Bullet points of the most critical information for investors.

## Sources
List the sources referenced with their URLs.

Write in a professional, analytical tone. Focus on factual information.
If specific data points are unclear from the provided articles, note what is
known vs. what requires further verification.
Do NOT fabricate specific numbers - only report what you can find in the sources."""

        user_prompt = f"""Company: {company_name}
Ticker: {ticker}

Here are recent news articles about this company:

{combined_search[:6000]}

Analyze these articles and produce a comprehensive news summary in markdown format.
Focus on the top 5 most important stories and the market's reaction to the earnings."""

        markdown = self.claude.call_for_markdown(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4096,
            temperature=0.3,
        )

        # Save the result
        filepath = save_markdown(ticker, "news.md", markdown)
        logger.info(f"News summary saved to {filepath}")

        return {
            "success": True,
            "result": markdown,
            "filepath": filepath,
            "ticker": ticker,
        }
