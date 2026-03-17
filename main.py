"""
Main Pipeline Orchestrator for the Quarterly Earnings Research Application.

This script chains all skills together in sequence:
1. select-company       → Pick a company that reported earnings today
2. research-company     → Gather and summarize recent news
3. get-reports          → Download last 2 SEC filings
4. get-numbers          → Extract financial data from each report
5. extract-goals        → Extract strategic goals from each report
6. analyze-tone         → Compare messaging tone across reports
7. analyze-price        → Analyze stock price movements
8. get-logo             → Fetch company logo
9. compare-reports      → Generate comparative analysis
10. generate-report     → Produce final 6-page PDF report
11. ten-point-analysis  → Extract 5 yay + 5 nay bullet points
12. animate             → Create 15s OHLC animation video

Usage:
    python main.py                   # Full auto: pick company + run all
    python main.py --ticker AAPL     # Run for specific company
    python main.py --ticker AAPL --skip-download   # Skip report download
"""
import os
import sys
import json
import argparse
import time
from datetime import datetime

# Ensure we can import from the app directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import REPORTS_DIR
from utils import logger, ensure_company_dir, save_json, load_json, get_report_dates
from claude_wrapper import get_claude
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


class EarningsPipeline:
    """Orchestrates the full earnings research pipeline."""

    def __init__(self, ticker: str = None, company_name: str = None):
        self.ticker = ticker
        self.company_name = company_name
        self.claude = get_claude()
        self.report_dates = []
        self.results = {}
        self.start_time = None

        # Initialize all skills
        self.skills = {
            "select_company": SelectCompanySkill(self.claude),
            "research_company": ResearchCompanySkill(self.claude),
            "get_reports": GetReportsSkill(self.claude),
            "get_numbers": GetNumbersSkill(self.claude),
            "extract_goals": ExtractGoalsSkill(self.claude),
            "analyze_tone": AnalyzeToneSkill(self.claude),
            "analyze_price": AnalyzePriceSkill(self.claude),
            "get_logo": GetLogoSkill(self.claude),
            "compare_reports": CompareReportsSkill(self.claude),
            "generate_report": GenerateReportSkill(self.claude),
            "ten_point_analysis": TenPointAnalysisSkill(self.claude),
            "animate": AnimateSkill(self.claude),
        }

    def _log_step(self, step_num: int, total: int, name: str):
        """Log a pipeline step header."""
        logger.info(f"\n{'#'*70}")
        logger.info(f"# STEP {step_num}/{total}: {name}")
        logger.info(f"{'#'*70}\n")

    def _save_pipeline_state(self):
        """Save current pipeline state to JSON for recovery/debugging."""
        state = {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "report_dates": self.report_dates,
            "results_summary": {
                k: {
                    "success": v.get("success"),
                    "elapsed": v.get("elapsed_seconds"),
                }
                for k, v in self.results.items()
            },
            "timestamp": datetime.now().isoformat(),
        }
        if self.ticker:
            save_json(self.ticker, "pipeline_state.json", state)

    def run(self, skip_download: bool = False):
        """Execute the full pipeline."""
        self.start_time = time.time()
        total_steps = 12

        logger.info("=" * 70)
        logger.info("QUARTERLY EARNINGS RESEARCH PIPELINE")
        logger.info(f"Started: {datetime.now().isoformat()}")
        logger.info("=" * 70)

        # ─── STEP 1: Select Company ───────────────────────────
        if not self.ticker:
            self._log_step(1, total_steps, "SELECT COMPANY")
            result = self.skills["select_company"].run()
            self.results["select_company"] = result

            if not result.get("success"):
                logger.error("Failed to select a company. Aborting.")
                return self.results

            self.ticker = result["ticker"]
            self.company_name = result["company_name"]
        else:
            logger.info(f"Using provided ticker: {self.ticker}")
            if not self.company_name:
                self.company_name = self.ticker
            self.results["select_company"] = {
                "success": True,
                "ticker": self.ticker,
                "company_name": self.company_name,
                "elapsed_seconds": 0,
            }

        logger.info(f"\n>>> Working on: {self.company_name} ({self.ticker})\n")

        # ─── STEP 2: Research Company News ────────────────────
        self._log_step(2, total_steps, "RESEARCH COMPANY NEWS")
        result = self.skills["research_company"].run(
            ticker=self.ticker,
            company_name=self.company_name,
        )
        self.results["research_company"] = result
        self._save_pipeline_state()

        # ─── STEP 3: Download Reports ────────────────────────
        if not skip_download:
            self._log_step(3, total_steps, "DOWNLOAD SEC REPORTS")
            result = self.skills["get_reports"].run(
                ticker=self.ticker,
                company_name=self.company_name,
            )
            self.results["get_reports"] = result

            if result.get("success"):
                self.report_dates = result.get("report_dates", [])
            else:
                logger.error("Failed to download reports. Pipeline may be incomplete.")
        else:
            logger.info("Skipping report download (--skip-download flag)")
            self.report_dates = get_report_dates(self.ticker)

        self._save_pipeline_state()

        if len(self.report_dates) < 2:
            logger.error(
                f"Need at least 2 reports, found {len(self.report_dates)}. "
                f"Pipeline cannot complete comparative analysis."
            )
            if len(self.report_dates) == 0:
                logger.error("No reports found at all. Aborting.")
                return self.results

        # ─── STEP 4: Extract Financial Numbers ───────────────
        self._log_step(4, total_steps, "EXTRACT FINANCIAL NUMBERS")
        for date in self.report_dates[:2]:
            logger.info(f"Processing numbers for {date}...")
            result = self.skills["get_numbers"].run(
                ticker=self.ticker,
                report_date=date,
            )
            self.results[f"get_numbers_{date}"] = result
        self._save_pipeline_state()

        # ─── STEP 5: Extract Strategic Goals ─────────────────
        self._log_step(5, total_steps, "EXTRACT STRATEGIC GOALS")
        for date in self.report_dates[:2]:
            logger.info(f"Processing goals for {date}...")
            result = self.skills["extract_goals"].run(
                ticker=self.ticker,
                report_date=date,
            )
            self.results[f"extract_goals_{date}"] = result
        self._save_pipeline_state()

        # ─── STEP 6: Analyze Tone ────────────────────────────
        if len(self.report_dates) >= 2:
            self._log_step(6, total_steps, "ANALYZE COMMUNICATION TONE")
            result = self.skills["analyze_tone"].run(
                ticker=self.ticker,
                report_dates=self.report_dates[:2],
            )
            self.results["analyze_tone"] = result
        else:
            logger.warning("Skipping tone analysis (need 2 reports)")
        self._save_pipeline_state()

        # ─── STEP 7: Analyze Price ───────────────────────────
        if len(self.report_dates) >= 2:
            self._log_step(7, total_steps, "ANALYZE STOCK PRICE")
            result = self.skills["analyze_price"].run(
                ticker=self.ticker,
                report_dates=self.report_dates[:2],
            )
            self.results["analyze_price"] = result
        else:
            logger.warning("Skipping price analysis (need 2 reports)")
        self._save_pipeline_state()

        # ─── STEP 8: Compare Reports ─────────────────────────
        if len(self.report_dates) >= 2:
            self._log_step(8, total_steps, "COMPARE REPORTS")
            result = self.skills["compare_reports"].run(
                ticker=self.ticker,
                report_dates=self.report_dates[:2],
            )
            self.results["compare_reports"] = result
        else:
            logger.warning("Skipping comparison (need 2 reports)")
        self._save_pipeline_state()

        # ─── STEP 9: Generate PDF Report ─────────────────────
        self._log_step(9, total_steps, "GENERATE PDF REPORT")
        result = self.skills["generate_report"].run(
            ticker=self.ticker,
            company_name=self.company_name,
            report_dates=self.report_dates[:2] if len(self.report_dates) >= 2
            else self.report_dates,
        )
        self.results["generate_report"] = result
        self._save_pipeline_state()

        # ─── STEP 10: Get Logo ───────────────────────────
        self._log_step(10, total_steps, "GET COMPANY LOGO")
        result = self.skills["get_logo"].run(
            ticker=self.ticker,
            company_name=self.company_name,
        )
        self.results["get_logo"] = result
        self._save_pipeline_state()

        # ─── STEP 11: Ten-Point Analysis ────────────────
        self._log_step(11, total_steps, "TEN-POINT ANALYSIS")
        result = self.skills["ten_point_analysis"].run(
            ticker=self.ticker,
            company_name=self.company_name,
            report_dates=self.report_dates[:2] if len(self.report_dates) >= 2
            else self.report_dates,
        )
        self.results["ten_point_analysis"] = result
        self._save_pipeline_state()

        # ─── STEP 12: Animate ───────────────────────────
        self._log_step(12, total_steps, "CREATE ANIMATION VIDEO")
        result = self.skills["animate"].run(
            ticker=self.ticker,
            company_name=self.company_name,
        )
        self.results["animate"] = result
        self._save_pipeline_state()

        # ─── DONE ────────────────────────────────────────────
        elapsed = time.time() - self.start_time
        logger.info("\n" + "=" * 70)
        logger.info("PIPELINE COMPLETE")
        logger.info(f"Company: {self.company_name} ({self.ticker})")
        logger.info(f"Total time: {elapsed:.1f}s")
        logger.info(f"Reports analyzed: {self.report_dates}")

        # Summary of results
        for step, res in self.results.items():
            status = "✓" if res.get("success") else "✗"
            t = res.get("elapsed_seconds", 0)
            logger.info(f"  {status} {step}: {t}s")

        if result.get("pdf_path"):
            logger.info(f"\n📄 PDF Report: {result['pdf_path']}")
        if result.get("html_path"):
            logger.info(f"🌐 HTML Report: {result['html_path']}")
        logger.info("=" * 70)

        return self.results


def main():
    parser = argparse.ArgumentParser(
        description="Quarterly Earnings Research Pipeline"
    )
    parser.add_argument(
        "--ticker", "-t",
        type=str,
        default=None,
        help="Company ticker symbol (e.g., AAPL). If not set, auto-selects.",
    )
    parser.add_argument(
        "--company-name", "-n",
        type=str,
        default=None,
        help="Company full name (optional, used for display).",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading reports (use already downloaded files).",
    )

    args = parser.parse_args()

    pipeline = EarningsPipeline(
        ticker=args.ticker,
        company_name=args.company_name,
    )
    results = pipeline.run(skip_download=args.skip_download)

    # Save final results
    if pipeline.ticker:
        save_json(pipeline.ticker, "final_results.json", {
            "ticker": pipeline.ticker,
            "company_name": pipeline.company_name,
            "report_dates": pipeline.report_dates,
            "steps": {
                k: {"success": v.get("success"), "elapsed": v.get("elapsed_seconds")}
                for k, v in results.items()
            },
            "completed": datetime.now().isoformat(),
        })


if __name__ == "__main__":
    main()