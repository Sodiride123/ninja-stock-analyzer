"""
Skill: ten-point-analysis
Extracts 5 positive ("yay") and 5 negative ("nay") bullet points
from the generated report and OHLC analysis.

Output: [company]/bullets.json with structure:
{
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "generated_at": "2025-01-15T10:30:00",
  "yay": [
    {"title": "...", "detail": "...", "metric": "..."},
    ...
  ],
  "nay": [
    {"title": "...", "detail": "...", "metric": "..."},
    ...
  ]
}
"""
import os
import json
from datetime import datetime
from skills.base import BaseSkill
from utils import logger, ensure_company_dir, load_markdown
from claude_wrapper import log_to_runlog


class TenPointAnalysisSkill(BaseSkill):
    name = "ten-point-analysis"
    description = "Extract 5 positive and 5 negative bullet points from analysis"

    def execute(self, ticker: str, company_name: str = None,
                report_dates: list = None, **kwargs) -> dict:
        company_dir = ensure_company_dir(ticker)
        bullets_path = os.path.join(company_dir, "bullets.json")

        log_to_runlog(f"Generating 10-point analysis for {ticker}...")

        # Gather all available analysis content
        content_parts = []

        # 1. Load comparison report (most comprehensive)
        if report_dates and len(report_dates) >= 2:
            # Try both date orderings since we don't know which is prior/latest
            compare_md = None
            for combo in [f"{report_dates[0]}_{report_dates[1]}", f"{report_dates[1]}_{report_dates[0]}"]:
                try:
                    compare_md = load_markdown(ticker, f"{combo}_compare.md")
                    break
                except FileNotFoundError:
                    continue
            if compare_md:
                content_parts.append(f"## COMPARISON REPORT\n{compare_md}")
            else:
                logger.warning(f"Comparison file not found for dates: {report_dates[:2]}")

        # 2. Load price/OHLC analysis
        try:
            price_md = load_markdown(ticker, "ticker_analysis.md")
            content_parts.append(f"## STOCK PRICE ANALYSIS\n{price_md}")
        except FileNotFoundError:
            logger.info("No ticker_analysis.md found")

        # 3. Load individual report analyses (numbers, goals, tone)
        if report_dates:
            for date in report_dates[:2]:
                for suffix, label in [("_numbers.md", "FINANCIAL NUMBERS"),
                                       ("_goals.md", "GOALS & OUTLOOK"),
                                       ("_tone.md", "TONE ANALYSIS"),
                                       ("_report.txt", "RAW REPORT TEXT")]:
                    try:
                        md = load_markdown(ticker, f"{date}{suffix}")
                        content_parts.append(f"## {label} ({date})\n{md}")
                    except FileNotFoundError:
                        pass

        # 4. Load news
        try:
            news_md = load_markdown(ticker, "news.md")
            content_parts.append(f"## RECENT NEWS\n{news_md}")
        except FileNotFoundError:
            pass

        if not content_parts:
            return {
                "success": False,
                "error": "No analysis content found to generate bullet points"
            }

        combined_content = "\n\n---\n\n".join(content_parts)

        # Truncate if too long (Claude context limit)
        max_chars = 80000
        if len(combined_content) > max_chars:
            combined_content = combined_content[:max_chars] + "\n\n[... truncated ...]"

        # Ask Claude to extract the 10 bullet points
        system_prompt = """You are a senior financial analyst creating a concise executive briefing.
Your task is to distill the provided analysis into exactly 5 POSITIVE and 5 NEGATIVE bullet points.

RULES:
1. Each bullet point must have:
   - "title": A short punchy headline (5-8 words max)
   - "detail": One sentence explanation (15-25 words)
   - "metric": A specific number, percentage, or data point that supports this point
2. Positive points ("yay") = things going well, growth, beats, improvements
3. Negative points ("nay") = concerns, misses, declines, risks
4. Be specific - use actual numbers from the analysis
5. Order by importance (most impactful first)
6. Cover different aspects: revenue, margins, guidance, market position, stock price

OUTPUT FORMAT: Return ONLY valid JSON, no markdown fences, no explanation:
{
  "yay": [
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."}
  ],
  "nay": [
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."},
    {"title": "...", "detail": "...", "metric": "..."}
  ]
}"""

        user_prompt = (
            f"Analyze the following research for {ticker}"
            f"{f' ({company_name})' if company_name else ''} "
            f"and extract exactly 5 positive and 5 negative bullet points.\n\n"
            f"{combined_content}"
        )

        response = self.claude.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        if not response:
            return {"success": False, "error": "Claude returned empty response"}

        # Parse the JSON response
        try:
            # Clean up response - remove markdown fences if present
            cleaned = response.strip()
            if cleaned.startswith("```"):
                # Remove first line and last line
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            
            bullets = json.loads(cleaned)
        except json.JSONDecodeError as e:
            # Try to extract JSON from the response
            import re
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    bullets = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    log_to_runlog(f"Failed to parse bullet points JSON: {e}")
                    return {"success": False, "error": f"JSON parse error: {e}"}
            else:
                log_to_runlog(f"No JSON found in response: {e}")
                return {"success": False, "error": f"No JSON in response: {e}"}

        # Validate structure
        if "yay" not in bullets or "nay" not in bullets:
            return {"success": False, "error": "Missing yay/nay keys in response"}

        if len(bullets["yay"]) < 3 or len(bullets["nay"]) < 3:
            logger.warning(f"Got fewer bullet points than expected: "
                          f"{len(bullets['yay'])} yay, {len(bullets['nay'])} nay")

        # Ensure exactly 5 of each (pad if needed)
        while len(bullets["yay"]) < 5:
            bullets["yay"].append({"title": "—", "detail": "—", "metric": "—"})
        while len(bullets["nay"]) < 5:
            bullets["nay"].append({"title": "—", "detail": "—", "metric": "—"})

        # Trim to exactly 5
        bullets["yay"] = bullets["yay"][:5]
        bullets["nay"] = bullets["nay"][:5]

        # Build final output
        output = {
            "ticker": ticker,
            "company_name": company_name or ticker,
            "generated_at": datetime.now().isoformat(),
            "report_dates": report_dates or [],
            "yay": bullets["yay"],
            "nay": bullets["nay"],
        }

        # Save to file
        with open(bullets_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved bullets.json: {bullets_path}")
        log_to_runlog(f"Generated 10-point analysis → bullets.json")

        return {
            "success": True,
            "bullets_path": bullets_path,
            "yay_count": len(output["yay"]),
            "nay_count": len(output["nay"]),
        }