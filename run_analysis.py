#!/usr/bin/env python3
"""
Quarterly Earnings Research — Scheduled Analysis Runner

Usage:
    python run_analysis.py AAPL                # Analyze Apple (cleans existing data first)
    python run_analysis.py AAPL MSFT GOOG      # Analyze multiple tickers
    python run_analysis.py --all               # Re-run all existing analyses
    python run_analysis.py --keep AAPL         # Analyze without deleting existing data
    python run_analysis.py                     # Auto-select: companies that reported ~10 days ago
    python run_analysis.py --list              # List all past analyses

Designed for cron / systemd timer scheduling:
    # Every weekday at 7am, analyze companies that reported 10 days ago
    0 7 * * 1-5  cd /workspace/earnings_app && python run_analysis.py >> /var/log/earnings.log 2>&1

    # Re-run all existing analyses fresh
    0 6 * * 0  cd /workspace/earnings_app && python run_analysis.py --all >> /var/log/earnings.log 2>&1

    # Specific ticker every quarter
    0 18 * * *   cd /workspace/earnings_app && python run_analysis.py AAPL >> /var/log/earnings.log 2>&1

Exit codes:
    0  All analyses completed successfully
    1  Some analyses failed
    2  No companies found / bad arguments
"""
import os
import sys
import json
import shutil
import argparse
import time
import subprocess
import re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import REPORTS_DIR
from utils import logger, ensure_company_dir, save_json, get_report_dates
from claude_wrapper import get_claude, set_log_ticker, log_to_runlog
from skills import (
    SelectCompanySkill,
    ResearchCompanySkill,
    GetReportsSkill,
    GetNumbersSkill,
    ExtractGoalsSkill,
    AnalyzeToneSkill,
    AnalyzePriceSkill,
    GetLogoSkill,
    CompareReportsSkill,
    GenerateReportSkill,
    TenPointAnalysisSkill,
    AnimateSkill,
)


# ── Helpers ──────────────────────────────────────────────────────────

def banner(msg, char="═"):
    width = 70
    logger.info(f"\n{char * width}")
    logger.info(f"  {msg}")
    logger.info(f"{char * width}\n")


def elapsed_str(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def find_companies_reported_n_days_ago(n_days=10):
    """
    Use Claude to find companies that reported quarterly earnings approximately
    n_days ago. Returns a list of (ticker, company_name) tuples.
    """
    claude = get_claude()
    target_date = datetime.now() - timedelta(days=n_days)
    date_range_start = (target_date - timedelta(days=2)).strftime("%Y-%m-%d")
    date_range_end = (target_date + timedelta(days=2)).strftime("%Y-%m-%d")
    target_str = target_date.strftime("%Y-%m-%d")

    # Fetch earnings calendar data
    yahoo_data = ""
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-L",
                "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "-H", "Accept: text/html",
                f"https://finance.yahoo.com/calendar/earnings?day={target_str}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        yahoo_data = result.stdout[:20000]
    except Exception as e:
        logger.warning(f"Yahoo calendar fetch failed: {e}")

    nasdaq_data = ""
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-L",
                "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "-H", "Accept: application/json",
                f"https://api.nasdaq.com/api/calendar/earnings?date={target_str}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        nasdaq_data = result.stdout[:20000]
    except Exception as e:
        logger.warning(f"Nasdaq calendar fetch failed: {e}")

    # Web search
    search_data = ""
    try:
        query = f"companies+that+reported+quarterly+earnings+{target_str}"
        result = subprocess.run(
            [
                "curl", "-s", "-L",
                "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                f"https://duckduckgo.com/html/?q={query}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        search_data = result.stdout[:10000]
    except Exception as e:
        logger.warning(f"Web search failed: {e}")

    system_prompt = """You are a financial research assistant. Identify public companies 
that reported quarterly earnings around the specified date range.

Return a JSON array of objects, each with:
  {"ticker": "AAPL", "name": "Apple Inc."}

Rules:
- Return 3-5 well-known, large-cap companies (S&P 500 preferred)
- Only include companies you are confident actually reported in this date range
- If calendar data is sparse, use your knowledge of the earnings season
- Prefer companies with interesting stories (beats, misses, guidance changes)
- IMPORTANT: Respond ONLY with a valid JSON array, no other text"""

    user_prompt = f"""Find companies that reported quarterly earnings around {target_str}
(range: {date_range_start} to {date_range_end}).

--- Yahoo Finance Calendar ---
{yahoo_data[:8000]}

--- Nasdaq Earnings Calendar ---
{nasdaq_data[:8000]}

--- Web Search ---
{search_data[:5000]}

Return a JSON array of 3-5 companies that reported in this window."""

    try:
        raw = claude.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1000,
            temperature=0.3,
        )
        # Extract JSON array from response
        match = re.search(r'\[[\s\S]*?\]', raw)
        if match:
            companies = json.loads(match.group(0))
            return [(c["ticker"], c.get("name", c["ticker"])) for c in companies]
    except Exception as e:
        logger.error(f"Failed to find companies: {e}")

    return []


def get_all_existing_tickers():
    """Return a list of all tickers that have existing analyses."""
    if not os.path.isdir(REPORTS_DIR):
        return []
    tickers = []
    for name in sorted(os.listdir(REPORTS_DIR)):
        company_dir = os.path.join(REPORTS_DIR, name)
        if os.path.isdir(company_dir) and os.listdir(company_dir):
            tickers.append(name)
    return tickers


# ── Pipeline Runner ──────────────────────────────────────────────────

def run_single_analysis(ticker, company_name=None, clean=True):
    """
    Run the full 12-step pipeline for a single ticker.
    Uses parallel execution for Phase 1 (research + download) and
    Phase 2 (numbers + goals + tone + price + logo).

    Args:
        ticker: Stock ticker symbol
        company_name: Optional company name
        clean: If True (default), delete existing data before re-running

    Returns dict with success status and timing info.
    """
    start = time.time()
    company_name = company_name or ticker
    results = {}
    failed = []

    # Clean existing data if requested (default behavior)
    if clean:
        company_dir = os.path.join(REPORTS_DIR, ticker)
        if os.path.isdir(company_dir):
            shutil.rmtree(company_dir)
            logger.info(f"Cleaned existing analysis for {ticker}")

    banner(f"ANALYZING: {company_name} ({ticker})")
    set_log_ticker(ticker)
    log_to_runlog("=" * 60)
    log_to_runlog(f"SCHEDULED ANALYSIS START for {ticker} ({company_name})")
    log_to_runlog(f"Time: {datetime.now().isoformat()}")
    log_to_runlog(f"Clean: {clean}")
    log_to_runlog("=" * 60)

    claude = get_claude()

    # Initialize skills
    skills = {
        "research_company": ResearchCompanySkill(claude),
        "get_reports": GetReportsSkill(claude),
        "get_numbers": GetNumbersSkill(claude),
        "extract_goals": ExtractGoalsSkill(claude),
        "analyze_tone": AnalyzeToneSkill(claude),
        "analyze_price": AnalyzePriceSkill(claude),
        "get_logo": GetLogoSkill(claude),
        "compare_reports": CompareReportsSkill(claude),
        "generate_report": GenerateReportSkill(claude),
        "ten_point_analysis": TenPointAnalysisSkill(claude),
        "animate": AnimateSkill(claude),
    }

    report_dates = []

    # ═════════════════════════════════════════════════════════════════════
    # PHASE 1: Research + Download Reports (parallel)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("▶ Phase 1: Research + Download (parallel)")
    log_to_runlog("▶ Phase 1: Research + Download (parallel)")

    def _research():
        return skills["research_company"].run(
            ticker=ticker, company_name=company_name
        )

    def _download():
        return skills["get_reports"].run(
            ticker=ticker, company_name=company_name
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_research = pool.submit(_research)
        fut_download = pool.submit(_download)

        research_result = fut_research.result()
        download_result = fut_download.result()

    results["research_company"] = research_result
    results["get_reports"] = download_result

    if research_result.get("success"):
        logger.info("  ✓ Research complete")
        log_to_runlog("✓ research_company completed")
    else:
        logger.warning("  ✗ Research failed")
        log_to_runlog("✗ research_company failed")
        failed.append("research_company")

    if download_result.get("success"):
        report_dates = download_result.get("report_dates", [])
        logger.info(f"  ✓ Reports downloaded: {report_dates}")
        log_to_runlog(f"✓ get_reports completed — dates: {report_dates}")
    else:
        logger.error("  ✗ Report download failed — cannot continue")
        log_to_runlog("✗ get_reports failed — aborting")
        failed.append("get_reports")
        return _finish(ticker, company_name, results, failed, start, clean)

    if len(report_dates) < 2:
        logger.error(f"  Only {len(report_dates)} reports found, need 2. Aborting.")
        log_to_runlog(f"ABORT: only {len(report_dates)} reports found")
        return _finish(ticker, company_name, results, failed, start, clean)

    # ═════════════════════════════════════════════════════════════════════
    # PHASE 2: Numbers + Goals + Tone + Price (parallel)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("▶ Phase 2: Numbers + Goals + Tone + Price + Logo (parallel)")
    log_to_runlog("▶ Phase 2: Numbers + Goals + Tone + Price + Logo (parallel)")

    phase2_ok = {"numbers": True, "goals": True, "tone": True, "price": True, "logo": True}

    def _numbers(date):
        return ("numbers", date, skills["get_numbers"].run(
            ticker=ticker, report_date=date
        ))

    def _goals(date):
        return ("goals", date, skills["extract_goals"].run(
            ticker=ticker, report_date=date
        ))

    def _tone():
        return ("tone", None, skills["analyze_tone"].run(
            ticker=ticker, report_dates=report_dates[:2]
        ))

    def _price():
        return ("price", None, skills["analyze_price"].run(
            ticker=ticker, report_dates=report_dates[:2]
        ))

    def _logo():
        return ("logo", None, skills["get_logo"].run(
            ticker=ticker, company_name=company_name
        ))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for date in report_dates[:2]:
            futures.append(pool.submit(_numbers, date))
            futures.append(pool.submit(_goals, date))
        futures.append(pool.submit(_tone))
        futures.append(pool.submit(_price))
        futures.append(pool.submit(_logo))

        for fut in as_completed(futures):
            try:
                task_type, date, result = fut.result()
                ok = result.get("success", False)
                label = f"{task_type}" + (f" ({date})" if date else "")
                if ok:
                    logger.info(f"  ✓ {label}")
                    log_to_runlog(f"✓ {label} completed")
                else:
                    logger.warning(f"  ✗ {label}")
                    log_to_runlog(f"✗ {label} failed")
                    phase2_ok[task_type] = False
            except Exception as e:
                logger.error(f"  ✗ Phase 2 task error: {e}")
                log_to_runlog(f"✗ Phase 2 error: {e}")

    if not phase2_ok["numbers"]:
        failed.append("get_numbers")
    if not phase2_ok["goals"]:
        failed.append("extract_goals")
    if not phase2_ok["tone"]:
        failed.append("analyze_tone")
    if not phase2_ok["price"]:
        failed.append("analyze_price")
    if not phase2_ok["logo"]:
        failed.append("get_logo")

    # ═════════════════════════════════════════════════════════════════════
    # PHASE 3: Compare Reports (sequential, needs Phase 2)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("▶ Phase 3: Compare Reports")
    log_to_runlog("▶ Phase 3: Compare Reports")

    compare_result = skills["compare_reports"].run(
        ticker=ticker, report_dates=report_dates[:2]
    )
    results["compare_reports"] = compare_result
    if compare_result.get("success"):
        logger.info("  ✓ Comparison complete")
        log_to_runlog("✓ compare_reports completed")
    else:
        logger.warning("  ✗ Comparison failed")
        log_to_runlog("✗ compare_reports failed")
        failed.append("compare_reports")

    # ═════════════════════════════════════════════════════════════════════
    # PHASE 4: Generate Report (sequential, needs everything)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("▶ Phase 4: Generate Report")
    log_to_runlog("▶ Phase 4: Generate Report")

    report_result = skills["generate_report"].run(
        ticker=ticker,
        company_name=company_name,
        report_dates=report_dates[:2],
    )
    results["generate_report"] = report_result
    if report_result.get("success"):
        logger.info(f"  ✓ Report generated: {report_result.get('pdf_path', 'N/A')}")
        log_to_runlog(f"✓ generate_report completed — {report_result.get('pdf_path', 'N/A')}")
    else:
        logger.warning("  ✗ Report generation failed")
        log_to_runlog("✗ generate_report failed")
        failed.append("generate_report")

    # ═════════════════════════════════════════════════════════════════════
    # PHASE 5: Ten-Point Analysis (needs report + price)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("▶ Phase 5: Ten-Point Analysis")
    log_to_runlog("▶ Phase 5: Ten-Point Analysis")

    tp_result = skills["ten_point_analysis"].run(
        ticker=ticker,
        company_name=company_name,
        report_dates=report_dates[:2],
    )
    results["ten_point_analysis"] = tp_result
    if tp_result.get("success"):
        logger.info("  ✓ Ten-point analysis complete")
        log_to_runlog("✓ ten_point_analysis completed")
    else:
        logger.warning("  ✗ Ten-point analysis failed")
        log_to_runlog("✗ ten_point_analysis failed")
        failed.append("ten_point_analysis")

    # ═════════════════════════════════════════════════════════════════════
    # PHASE 6: Animate (needs bullets + logo + ohlc)
    # ═════════════════════════════════════════════════════════════════════
    logger.info("▶ Phase 6: Create Animation")
    log_to_runlog("▶ Phase 6: Create Animation")

    anim_result = skills["animate"].run(
        ticker=ticker,
        company_name=company_name,
    )
    results["animate"] = anim_result
    if anim_result.get("success"):
        logger.info(f"  ✓ Animation created: {anim_result.get('video_path', 'N/A')}")
        log_to_runlog(f"✓ animate completed — {anim_result.get('video_path', 'N/A')}")
    else:
        logger.warning("  ✗ Animation failed")
        log_to_runlog("✗ animate failed")
        failed.append("animate")

    return _finish(ticker, company_name, results, failed, start, clean)


def _finish(ticker, company_name, results, failed, start, clean=True):
    """Wrap up a single analysis run."""
    elapsed = time.time() - start
    success = len(failed) == 0

    log_to_runlog("=" * 60)
    if success:
        log_to_runlog(f"ANALYSIS COMPLETED SUCCESSFULLY in {elapsed_str(elapsed)}")
    else:
        log_to_runlog(f"ANALYSIS COMPLETED WITH FAILURES in {elapsed_str(elapsed)}")
        log_to_runlog(f"Failed steps: {', '.join(failed)}")
    log_to_runlog("=" * 60)

    # Save run metadata
    save_json(ticker, "run_metadata.json", {
        "ticker": ticker,
        "company_name": company_name,
        "run_type": "scheduled",
        "clean": clean,
        "started": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "success": success,
        "failed_steps": failed,
        "report_dates": get_report_dates(ticker),
    })

    status = "✅ SUCCESS" if success else "⚠️  PARTIAL"
    banner(f"{status}: {company_name} ({ticker}) — {elapsed_str(elapsed)}", "─")

    return {
        "ticker": ticker,
        "company_name": company_name,
        "success": success,
        "failed_steps": failed,
        "elapsed": elapsed,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def list_analyses():
    """List all past analyses."""
    if not os.path.isdir(REPORTS_DIR):
        print("No analyses found.")
        return

    print(f"\n{'Ticker':<10} {'Company':<25} {'Dates':<30} {'Report?'}")
    print("─" * 80)

    for ticker in sorted(os.listdir(REPORTS_DIR)):
        company_dir = os.path.join(REPORTS_DIR, ticker)
        if not os.path.isdir(company_dir):
            continue

        files = os.listdir(company_dir)
        if not files:
            continue

        # Get company name from metadata
        name = ticker
        meta_path = os.path.join(company_dir, "reports_metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                name = meta.get("company_name", ticker)
            except Exception:
                pass

        dates = get_report_dates(ticker)
        has_pdf = "report.pdf" in files
        has_html = "report.html" in files
        has_ohlc = "ohlc.json" in files

        report_status = []
        if has_pdf:
            report_status.append("PDF")
        if has_html:
            report_status.append("HTML")
        if has_ohlc:
            report_status.append("OHLC")

        print(f"{ticker:<10} {name[:24]:<25} {', '.join(dates) or 'none':<30} {' '.join(report_status)}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Quarterly Earnings Research — Scheduled Analysis Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_analysis.py AAPL             Analyze Apple (deletes existing data first)
  python run_analysis.py AAPL MSFT        Analyze Apple and Microsoft (fresh start)
  python run_analysis.py --keep AAPL      Analyze Apple without deleting existing data
  python run_analysis.py --all            Re-run all existing analyses (fresh start)
  python run_analysis.py --all --keep     Re-run all existing analyses (keep existing data)
  python run_analysis.py                  Auto-select companies (reported ~10 days ago)
  python run_analysis.py --days 7         Auto-select from 7 days ago
  python run_analysis.py --list           List all past analyses
  python run_analysis.py --max 5          Auto-select up to 5 companies

Cron example (weekdays at 7am):
  0 7 * * 1-5  cd /workspace/earnings_app && python run_analysis.py >> /var/log/earnings.log 2>&1

SuperNinja prompt:
  Using the run_analysis.py script refresh the analysis for stock AAPL
        """,
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Ticker symbol(s) to analyze. If omitted, auto-selects.",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Re-run analysis for ALL existing tickers",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=10,
        help="For auto-select: look for companies that reported N days ago (default: 10)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all past analyses and exit",
    )
    parser.add_argument(
        "--keep", "-k",
        action="store_true",
        help="Keep existing data (don't clean before re-running). Default is to clean.",
    )
    parser.add_argument(
        "--max", "-m",
        type=int,
        default=3,
        help="Max companies to analyze in auto-select mode (default: 3)",
    )

    args = parser.parse_args()

    # Determine clean mode: clean by default, --keep disables it
    clean = not args.keep

    # ── List mode ──
    if args.list:
        list_analyses()
        sys.exit(0)

    # ── Determine tickers to analyze ──
    tickers = []

    if args.all:
        # --all mode: re-run all existing analyses
        existing = get_all_existing_tickers()
        if not existing:
            logger.error("No existing analyses found. Run some analyses first.")
            sys.exit(2)

        tickers = [(t, None) for t in existing]
        mode_label = "ALL EXISTING"
        banner(f"RE-RUNNING ALL {len(tickers)} EXISTING ANALYSES {'(clean)' if clean else '(keep existing)'}")
        for t, _ in tickers:
            logger.info(f"  • {t}")

    elif args.tickers:
        # Explicit tickers provided
        tickers = [(t.upper(), None) for t in args.tickers]

    else:
        # Auto-select mode
        banner(f"AUTO-SELECT: Finding companies that reported ~{args.days} days ago")
        companies = find_companies_reported_n_days_ago(args.days)

        if not companies:
            logger.error("No companies found for auto-selection. Try specifying tickers manually.")
            sys.exit(2)

        logger.info(f"Found {len(companies)} candidates:")
        for t, n in companies:
            logger.info(f"  • {t} — {n}")

        tickers = companies[:args.max]

    if not tickers:
        logger.error("No tickers to analyze.")
        sys.exit(2)

    # ── Run analyses ──
    banner(f"RUNNING {len(tickers)} ANALYSIS(ES) {'(clean)' if clean else '(keep existing)'}")
    run_start = time.time()
    all_results = []

    for ticker, name in tickers:
        result = run_single_analysis(ticker, name, clean=clean)
        all_results.append(result)

    # ── Summary ──
    total_elapsed = time.time() - run_start
    successes = sum(1 for r in all_results if r["success"])
    failures = len(all_results) - successes

    banner("RUN SUMMARY")
    print(f"\n{'Ticker':<10} {'Status':<12} {'Time':<10} {'Failed Steps'}")
    print("─" * 60)
    for r in all_results:
        status = "✅ OK" if r["success"] else "⚠️  PARTIAL"
        t = elapsed_str(r["elapsed"])
        fails = ", ".join(r["failed_steps"]) if r["failed_steps"] else "—"
        print(f"{r['ticker']:<10} {status:<12} {t:<10} {fails}")

    print(f"\n{'─' * 60}")
    print(f"Total: {len(all_results)} analyses | {successes} succeeded | {failures} failed | {elapsed_str(total_elapsed)}")
    print()

    # Save run summary
    summary_path = os.path.join(REPORTS_DIR, "last_run_summary.json")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "mode": "all" if args.all else ("explicit" if args.tickers else f"auto-select ({args.days} days)"),
            "clean": clean,
            "total_elapsed": round(total_elapsed, 1),
            "results": all_results,
        }, f, indent=2, default=str)

    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()