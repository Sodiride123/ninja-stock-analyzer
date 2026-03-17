"""
Skill: analyze-price
Fetch daily OHLC data from RealTimeFinanceData MCP for two windows:
  A) Between the prior report date and the latest report date
  B) 10 trading days after the latest report date
Compute statistics (gain/loss %, volatility, time above/below 30-day mean, etc.)
and derive sentiment about stock price movements.

Generates:
  [company]/[date_prior]_[date_latest]_price.md   (inter-report period)
  [company]/[date_latest]_post_price.md            (post-earnings reaction)
"""
import os
import sys
import math
import statistics
from datetime import datetime, timedelta
from skills.base import BaseSkill
from utils import logger, save_markdown, ensure_company_dir

# Import the MCP client
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from finance_mcp_client import MCPClient
except ImportError:
    MCPClient = None


class AnalyzePriceSkill(BaseSkill):
    name = "analyze-price"
    description = (
        "Fetch daily price data and compute statistics for the period between "
        "two report dates and 10 days post-earnings. Derive price sentiment."
    )

    def __init__(self, claude=None):
        super().__init__(claude)
        self._mcp = MCPClient() if MCPClient else None

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_time_series(self, ticker: str) -> dict:
        """Fetch 1-year daily time series from the MCP API."""
        if not self._mcp:
            raise RuntimeError("finance_mcp_client not available")

        symbol = f"{ticker}:NASDAQ"
        # Try NASDAQ first, fall back to NYSE
        try:
            result = self._mcp.stock_time_series(symbol=symbol, period="1Y")
            if result.get("status") == "OK":
                return result["data"].get("time_series", {})
        except Exception:
            pass

        symbol = f"{ticker}:NYSE"
        try:
            result = self._mcp.stock_time_series(symbol=symbol, period="1Y")
            if result.get("status") == "OK":
                return result["data"].get("time_series", {})
        except Exception:
            pass

        # Last resort: just ticker
        result = self._mcp.stock_time_series(symbol=ticker, period="1Y")
        if result.get("status") == "OK":
            return result["data"].get("time_series", {})
        raise RuntimeError(f"Could not fetch time series for {ticker}")

    def _filter_series(self, ts: dict, start_date: str, end_date: str) -> list:
        """
        Filter time series dict to entries between start_date and end_date (inclusive).
        Returns sorted list of (date_str, price, volume, change, change_pct).
        """
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        filtered = []
        for dt_str, vals in ts.items():
            # Parse "2025-09-05 16:00:00" -> date
            try:
                dt = datetime.strptime(dt_str.split(" ")[0], "%Y-%m-%d")
            except ValueError:
                continue
            if start <= dt <= end:
                filtered.append((
                    dt_str.split(" ")[0],
                    vals.get("price", 0),
                    vals.get("volume", 0),
                    vals.get("change", 0),
                    vals.get("change_percent", 0),
                ))
        filtered.sort(key=lambda x: x[0])
        return filtered

    # ------------------------------------------------------------------
    # Statistics computation
    # ------------------------------------------------------------------

    def _compute_stats(self, data_points: list, label: str) -> dict:
        """
        Compute comprehensive statistics from a list of
        (date, price, volume, change, change_pct) tuples.
        """
        if not data_points or len(data_points) < 2:
            return {"error": f"Insufficient data for {label} (got {len(data_points)} points)"}

        prices = [p[1] for p in data_points]
        volumes = [p[2] for p in data_points]
        daily_changes = [p[4] for p in data_points]

        start_price = prices[0]
        end_price = prices[-1]
        high_price = max(prices)
        low_price = min(prices)
        avg_price = statistics.mean(prices)
        median_price = statistics.median(prices)

        # Gain/loss
        total_change = end_price - start_price
        total_change_pct = (total_change / start_price * 100) if start_price else 0

        # Volatility (std dev of daily % changes)
        if len(daily_changes) > 1:
            volatility = statistics.stdev(daily_changes)
        else:
            volatility = 0

        # Annualized volatility (approx 252 trading days)
        annualized_vol = volatility * math.sqrt(252) if volatility else 0

        # 30-day rolling mean (use all data if < 30 points)
        window = min(30, len(prices))
        rolling_mean = statistics.mean(prices[-window:])

        # Time above/below 30-day mean
        days_above_mean = sum(1 for p in prices if p > rolling_mean)
        days_below_mean = sum(1 for p in prices if p < rolling_mean)
        days_at_mean = len(prices) - days_above_mean - days_below_mean
        pct_above_mean = (days_above_mean / len(prices) * 100) if prices else 0

        # Average volume
        avg_volume = statistics.mean(volumes) if volumes else 0

        # Max drawdown
        peak = prices[0]
        max_drawdown = 0
        max_drawdown_pct = 0
        for p in prices:
            if p > peak:
                peak = p
            drawdown = peak - p
            drawdown_pct = (drawdown / peak * 100) if peak else 0
            if drawdown_pct > max_drawdown_pct:
                max_drawdown = drawdown
                max_drawdown_pct = drawdown_pct

        # Positive vs negative days
        up_days = sum(1 for c in daily_changes if c > 0)
        down_days = sum(1 for c in daily_changes if c < 0)
        flat_days = len(daily_changes) - up_days - down_days

        # Best and worst single day
        best_day_idx = daily_changes.index(max(daily_changes))
        worst_day_idx = daily_changes.index(min(daily_changes))

        return {
            "label": label,
            "start_date": data_points[0][0],
            "end_date": data_points[-1][0],
            "trading_days": len(data_points),
            "start_price": round(start_price, 2),
            "end_price": round(end_price, 2),
            "total_change": round(total_change, 2),
            "total_change_pct": round(total_change_pct, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "high_date": data_points[prices.index(high_price)][0],
            "low_date": data_points[prices.index(low_price)][0],
            "avg_price": round(avg_price, 2),
            "median_price": round(median_price, 2),
            "daily_volatility": round(volatility, 4),
            "annualized_volatility": round(annualized_vol, 2),
            "rolling_mean_30d": round(rolling_mean, 2),
            "days_above_mean": days_above_mean,
            "days_below_mean": days_below_mean,
            "pct_above_mean": round(pct_above_mean, 1),
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "up_days": up_days,
            "down_days": down_days,
            "flat_days": flat_days,
            "win_rate": round(up_days / max(up_days + down_days, 1) * 100, 1),
            "best_day": {
                "date": data_points[best_day_idx][0],
                "change_pct": round(daily_changes[best_day_idx], 4),
            },
            "worst_day": {
                "date": data_points[worst_day_idx][0],
                "change_pct": round(daily_changes[worst_day_idx], 4),
            },
            "avg_daily_volume": int(avg_volume),
        }

    # ------------------------------------------------------------------
    # Sentiment derivation
    # ------------------------------------------------------------------

    def _derive_sentiment(self, stats: dict) -> dict:
        """Derive sentiment signals from computed statistics."""
        signals = []
        overall = "neutral"
        score = 0  # -5 to +5

        if "error" in stats:
            return {"overall": "unknown", "score": 0, "signals": [stats["error"]]}

        # Price trend
        chg = stats["total_change_pct"]
        if chg > 10:
            signals.append(f"🟢 Strong rally: +{chg}% over the period")
            score += 2
        elif chg > 3:
            signals.append(f"🟢 Positive trend: +{chg}% gain")
            score += 1
        elif chg < -10:
            signals.append(f"🔴 Sharp decline: {chg}% over the period")
            score -= 2
        elif chg < -3:
            signals.append(f"🔴 Negative trend: {chg}% loss")
            score -= 1
        else:
            signals.append(f"⚪ Relatively flat: {chg}% change")

        # Volatility
        vol = stats["annualized_volatility"]
        if vol > 40:
            signals.append(f"🔴 High volatility ({vol}% annualized) — significant uncertainty")
            score -= 1
        elif vol > 25:
            signals.append(f"🟡 Moderate volatility ({vol}% annualized)")
        else:
            signals.append(f"🟢 Low volatility ({vol}% annualized) — stable trading")
            score += 1

        # Mean reversion tendency
        pct_above = stats["pct_above_mean"]
        if pct_above > 65:
            signals.append(f"🟢 Spent {pct_above}% of time above 30-day mean — bullish bias")
            score += 1
        elif pct_above < 35:
            signals.append(f"🔴 Spent only {pct_above}% of time above 30-day mean — bearish bias")
            score -= 1

        # Drawdown severity
        dd = stats["max_drawdown_pct"]
        if dd > 15:
            signals.append(f"🔴 Severe max drawdown: -{dd}% from peak")
            score -= 1
        elif dd > 8:
            signals.append(f"🟡 Notable drawdown: -{dd}% from peak")

        # Win rate
        wr = stats["win_rate"]
        if wr > 55:
            signals.append(f"🟢 Positive win rate: {wr}% of days were up")
        elif wr < 45:
            signals.append(f"🔴 Weak win rate: only {wr}% of days were up")

        # Overall sentiment
        if score >= 3:
            overall = "strongly bullish"
        elif score >= 1:
            overall = "bullish"
        elif score <= -3:
            overall = "strongly bearish"
        elif score <= -1:
            overall = "bearish"
        else:
            overall = "neutral"

        return {"overall": overall, "score": score, "signals": signals}

    # ------------------------------------------------------------------
    # Markdown generation
    # ------------------------------------------------------------------

    def _stats_to_markdown(self, stats: dict, sentiment: dict) -> str:
        """Convert stats + sentiment into a markdown section."""
        if "error" in stats:
            return f"### {stats.get('label', 'Analysis')}\n\n⚠️ {stats['error']}\n"

        s = stats
        lines = [
            f"### {s['label']}",
            f"**Period:** {s['start_date']} → {s['end_date']} ({s['trading_days']} trading days)",
            "",
            "#### Price Summary",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Start Price | ${s['start_price']:,.2f} |",
            f"| End Price | ${s['end_price']:,.2f} |",
            f"| **Total Change** | **{'+' if s['total_change'] >= 0 else ''}{s['total_change_pct']}%** (${'+' if s['total_change'] >= 0 else ''}{s['total_change']:,.2f}) |",
            f"| Period High | ${s['high']:,.2f} ({s['high_date']}) |",
            f"| Period Low | ${s['low']:,.2f} ({s['low_date']}) |",
            f"| Average Price | ${s['avg_price']:,.2f} |",
            f"| Median Price | ${s['median_price']:,.2f} |",
            "",
            "#### Volatility & Risk",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Daily Volatility | {s['daily_volatility']}% |",
            f"| Annualized Volatility | {s['annualized_volatility']}% |",
            f"| Max Drawdown | -{s['max_drawdown_pct']}% (${s['max_drawdown']:,.2f}) |",
            f"| 30-Day Rolling Mean | ${s['rolling_mean_30d']:,.2f} |",
            f"| Days Above Mean | {s['days_above_mean']} ({s['pct_above_mean']}%) |",
            f"| Days Below Mean | {s['days_below_mean']} ({round(100 - s['pct_above_mean'], 1)}%) |",
            "",
            "#### Trading Activity",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Up Days | {s['up_days']} |",
            f"| Down Days | {s['down_days']} |",
            f"| Win Rate | {s['win_rate']}% |",
            f"| Best Day | {s['best_day']['date']} (+{s['best_day']['change_pct']}%) |",
            f"| Worst Day | {s['worst_day']['date']} ({s['worst_day']['change_pct']}%) |",
            f"| Avg Daily Volume | {s['avg_daily_volume']:,} |",
            "",
            "#### Sentiment Assessment",
            f"**Overall: {sentiment['overall'].upper()}** (score: {sentiment['score']}/5)",
            "",
        ]
        for sig in sentiment["signals"]:
            lines.append(f"- {sig}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    def execute(self, ticker: str, report_dates: list, **kwargs) -> dict:
        """
        Run price analysis for two windows:
          A) Between prior report date and latest report date
          B) Latest report date to +10 trading days (approx 14 calendar days)
        """
        if len(report_dates) < 2:
            return {
                "success": False,
                "error": "Need at least 2 report dates for price analysis",
                "ticker": ticker,
            }

        date_latest = report_dates[0]   # e.g. "2026-01-30"
        date_prior = report_dates[1]    # e.g. "2025-10-31"

        logger.info(f"Fetching price data for {ticker}...")

        # Fetch the full time series
        try:
            ts = self._fetch_time_series(ticker)
        except Exception as e:
            logger.error(f"Failed to fetch time series: {e}")
            return {
                "success": False,
                "error": f"Failed to fetch price data: {e}",
                "ticker": ticker,
            }

        logger.info(f"Got {len(ts)} data points from MCP API")

        # --- Window A: Between prior and latest report ---
        data_a = self._filter_series(ts, date_prior, date_latest)
        stats_a = self._compute_stats(data_a, f"Inter-Report Period ({date_prior} → {date_latest})")
        sentiment_a = self._derive_sentiment(stats_a)

        # --- Window B: 10 trading days after latest report (~14 calendar days) ---
        latest_dt = datetime.strptime(date_latest, "%Y-%m-%d")
        post_end = latest_dt + timedelta(days=16)  # ~10 trading days
        post_end_str = post_end.strftime("%Y-%m-%d")

        data_b = self._filter_series(ts, date_latest, post_end_str)
        stats_b = self._compute_stats(data_b, f"Post-Earnings Reaction ({date_latest} → +10 trading days)")
        sentiment_b = self._derive_sentiment(stats_b)

        # --- Build the full markdown ---
        md_lines = [
            f"# Stock Price Analysis: {ticker}",
            "",
            f"*Data sourced from RealTimeFinanceData API*",
            "",
            "---",
            "",
            self._stats_to_markdown(stats_a, sentiment_a),
            "---",
            "",
            self._stats_to_markdown(stats_b, sentiment_b),
            "---",
            "",
            "## Combined Price Sentiment",
            "",
        ]

        # Derive combined narrative
        combined_score = (sentiment_a.get("score", 0) + sentiment_b.get("score", 0))
        if combined_score >= 3:
            combined = "The stock showed strong positive momentum both between reports and after earnings."
        elif combined_score >= 1:
            combined = "The stock trended positively overall, with moderate investor confidence."
        elif combined_score <= -3:
            combined = "The stock faced significant selling pressure across both periods, signaling investor concern."
        elif combined_score <= -1:
            combined = "The stock showed weakness, suggesting the market is cautious about the company's trajectory."
        else:
            combined = "The stock traded in a mixed pattern, with no clear directional conviction from investors."

        # Check for divergence between periods
        score_a = sentiment_a.get("score", 0)
        score_b = sentiment_b.get("score", 0)
        if score_a >= 1 and score_b <= -1:
            combined += " Notably, the post-earnings reaction was negative despite a positive inter-report trend — the market may have been disappointed by the results."
        elif score_a <= -1 and score_b >= 1:
            combined += " Interestingly, the post-earnings reaction was positive despite prior weakness — the earnings may have exceeded lowered expectations."

        md_lines.append(combined)
        md_lines.append("")

        # Add key takeaways for the compare/report skills to consume
        md_lines.extend([
            "",
            "## Key Price Takeaways",
            "",
        ])

        if "error" not in stats_a:
            md_lines.append(f"- **Inter-report return:** {'+' if stats_a['total_change_pct'] >= 0 else ''}{stats_a['total_change_pct']}% over {stats_a['trading_days']} trading days")
            md_lines.append(f"- **Inter-report volatility:** {stats_a['annualized_volatility']}% annualized")
        if "error" not in stats_b:
            md_lines.append(f"- **Post-earnings return:** {'+' if stats_b['total_change_pct'] >= 0 else ''}{stats_b['total_change_pct']}% in first {stats_b['trading_days']} trading days")
            md_lines.append(f"- **Post-earnings volatility:** {stats_b['annualized_volatility']}% annualized")
        md_lines.append(f"- **Combined sentiment:** {sentiment_a.get('overall', 'N/A')} (inter-report) / {sentiment_b.get('overall', 'N/A')} (post-earnings)")
        md_lines.append("")

        full_markdown = "\n".join(md_lines)

        # Save the markdown files
        # Main combined file (legacy name)
        filename_combined = f"{date_prior}_{date_latest}_price.md"
        filepath_combined = save_markdown(ticker, filename_combined, full_markdown)
        logger.info(f"Price analysis saved to {filepath_combined}")

        # Also save as canonical ticker_analysis.md for easy UI access
        filepath_analysis = save_markdown(ticker, "ticker_analysis.md", full_markdown)
        logger.info(f"Ticker analysis saved to {filepath_analysis}")

        # Also save post-earnings separately for easy access
        post_md = "\n".join([
            f"# Post-Earnings Price Reaction: {ticker}",
            "",
            self._stats_to_markdown(stats_b, sentiment_b),
        ])
        filename_post = f"{date_latest}_post_price.md"
        filepath_post = save_markdown(ticker, filename_post, post_md)
        logger.info(f"Post-earnings price saved to {filepath_post}")

        # Save raw OHLC data as ohlc.json for charting
        import json as _json
        ohlc_data = {
            "ticker": ticker,
            "fetched_at": datetime.now().isoformat(),
            "inter_report": {
                "start_date": date_prior,
                "end_date": date_latest,
                "data": [
                    {
                        "date": d[0],
                        "price": d[1],
                        "volume": d[2],
                        "change": d[3],
                        "change_pct": d[4],
                    }
                    for d in data_a
                ],
                "stats": stats_a,
                "sentiment": sentiment_a,
            },
            "post_earnings": {
                "start_date": date_latest,
                "end_date": post_end_str,
                "data": [
                    {
                        "date": d[0],
                        "price": d[1],
                        "volume": d[2],
                        "change": d[3],
                        "change_pct": d[4],
                    }
                    for d in data_b
                ],
                "stats": stats_b,
                "sentiment": sentiment_b,
            },
        }
        company_dir = ensure_company_dir(ticker)
        ohlc_path = os.path.join(company_dir, "ohlc.json")
        with open(ohlc_path, "w", encoding="utf-8") as f:
            _json.dump(ohlc_data, f, indent=2, default=str)
        logger.info(f"OHLC data saved to {ohlc_path}")

        return {
            "success": True,
            "result": full_markdown,
            "ticker": ticker,
            "filepath": filepath_combined,
            "filepath_analysis": filepath_analysis,
            "filepath_post": filepath_post,
            "ohlc_path": ohlc_path,
            "stats_inter_report": stats_a,
            "stats_post_earnings": stats_b,
            "sentiment_inter_report": sentiment_a,
            "sentiment_post_earnings": sentiment_b,
        }