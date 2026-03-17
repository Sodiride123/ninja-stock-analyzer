"""
Web Server for the Quarterly Earnings Research Dashboard.

Provides:
  - Static file serving (HTML/CSS/JS)
  - REST API for pipeline control and data access
  - Background pipeline execution

API Endpoints:
  POST /api/run               Start the pipeline (body: {ticker?})
  GET  /api/status            Current pipeline state + progress
  GET  /api/file/:ticker/:fn  Read a generated analysis file
  GET  /api/report-links/:tk  Get report HTML/PDF download links
"""
import os
import re
import sys
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SERVER_HOST, SERVER_PORT, REPORTS_DIR, STATIC_DIR
from utils import logger, ensure_company_dir
from main import EarningsPipeline
from claude_wrapper import set_log_ticker, log_to_runlog


# ── Ticker Validation ─────────────────────────────────────────────
_ticker_cache = {"data": None, "time": 0}
_ticker_cache_lock = threading.Lock()
TICKER_CACHE_TTL = 3600  # 1 hour


def _load_ticker_data() -> dict:
    """Fetch SEC company_tickers.json and build lookup structures.

    Returns {"by_ticker": {TICKER: {name, cik}}, "by_name": [(name_lower, ticker)]}
    Cached for TICKER_CACHE_TTL seconds.
    """
    import subprocess as _sp

    with _ticker_cache_lock:
        if _ticker_cache["data"] and (time.time() - _ticker_cache["time"]) < TICKER_CACHE_TTL:
            return _ticker_cache["data"]

    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        result = _sp.run(
            ["curl", "-s", "-L",
             "-H", "User-Agent: EarningsResearchApp admin@earningsapp.com",
             "--max-time", "15", url],
            capture_output=True, text=True, timeout=20,
        )
        raw = json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"Failed to fetch SEC tickers: {e}")
        # Return empty structures so validation degrades gracefully
        return {"by_ticker": {}, "by_name": []}

    by_ticker = {}
    by_name = []
    for entry in raw.values():
        ticker = entry.get("ticker", "").upper()
        name = entry.get("title", "")
        cik = entry.get("cik_str", "")
        if ticker:
            by_ticker[ticker] = {"name": name, "cik": cik}
            by_name.append((name.lower(), ticker))

    data = {"by_ticker": by_ticker, "by_name": by_name}
    with _ticker_cache_lock:
        _ticker_cache["data"] = data
        _ticker_cache["time"] = time.time()
    return data


def validate_ticker(user_input: str) -> dict:
    """Validate user input as a stock ticker.

    Returns:
        {
            "valid": bool,
            "ticker": str or None,        # the matched ticker
            "company_name": str or None,
            "suggestions": [{"ticker", "name"}],  # fuzzy matches if invalid
            "message": str,
        }
    """
    cleaned = user_input.strip().upper()
    if not cleaned:
        return {"valid": False, "ticker": None, "company_name": None,
                "suggestions": [], "message": "Please enter a ticker symbol."}

    data = _load_ticker_data()
    by_ticker = data.get("by_ticker", {})
    by_name = data.get("by_name", [])

    # If cache is empty (SEC fetch failed), allow anything through
    if not by_ticker:
        return {"valid": True, "ticker": cleaned, "company_name": cleaned,
                "suggestions": [], "message": "Ticker validation unavailable, proceeding."}

    # Exact ticker match
    if cleaned in by_ticker:
        info = by_ticker[cleaned]
        return {"valid": True, "ticker": cleaned, "company_name": info["name"],
                "suggestions": [], "message": f"{info['name']} ({cleaned})"}

    # Fuzzy matching — search by company name and ticker substring
    suggestions = []
    cleaned_lower = cleaned.lower()

    # 1. Ticker starts with input (e.g., "BID" -> "BIDU")
    for t, info in by_ticker.items():
        if t.startswith(cleaned) and len(suggestions) < 8:
            suggestions.append({"ticker": t, "name": info["name"]})

    # 2. Company name contains input (e.g., "baidu" -> "BIDU")
    for name_lower, t in by_name:
        if cleaned_lower in name_lower and len(suggestions) < 8:
            if not any(s["ticker"] == t for s in suggestions):
                suggestions.append({"ticker": t, "name": by_ticker[t]["name"]})

    # 3. Ticker contains input (e.g., "IDU" matches "BIDU")
    if len(suggestions) < 3:
        for t, info in by_ticker.items():
            if cleaned in t and t != cleaned and len(suggestions) < 8:
                if not any(s["ticker"] == t for s in suggestions):
                    suggestions.append({"ticker": t, "name": info["name"]})

    # Limit to top 5 most relevant
    suggestions = suggestions[:5]

    if suggestions:
        msg = f'"{user_input}" is not a valid ticker. Did you mean one of these?'
    else:
        msg = f'"{user_input}" is not a recognized stock ticker.'

    return {"valid": False, "ticker": None, "company_name": None,
            "suggestions": suggestions, "message": msg}


def _safe_ticker(raw: str) -> str | None:
    """Sanitize a ticker to alphanumeric + hyphen/underscore only.

    Returns None if the input is empty or contains invalid characters
    after stripping whitespace.  This prevents path-traversal attacks
    (e.g. ``../../etc/passwd``) in every API endpoint that takes a ticker.
    """
    cleaned = raw.strip().upper()
    if not cleaned:
        return None
    if not re.match(r'^[A-Z0-9_.-]+$', cleaned):
        return None
    return cleaned


class PipelineManager:
    """Manages background pipeline execution and state."""

    # Map of file patterns → pipeline step that produces them
    STEP_FILE_MAP = {
        "news.md": "research_company",
        "reports_metadata.json": "get_reports",
        "_numbers.md": "get_numbers",
        "_goals.md": "extract_goals",
        "_tone.md": "analyze_tone",
        "_price.md": "analyze_price",
        "ticker_analysis.md": "analyze_price",
        "ohlc.json": "analyze_price",
        "_compare.md": "compare_reports",
        "report.html": "generate_report",
        "report.pdf": "generate_report",
        "logo.jpeg": "get_logo",
        "bullets.json": "ten_point_analysis",
        "overview.mp4": "animate",
        "animate_script.py": "animate",
    }

    def __init__(self):
        self.state = "idle"          # idle | running | done | error
        self.ticker = None
        self.company_name = None
        self.report_dates = []
        self.current_step = None
        self.completed_steps = []
        self.failed_steps = []
        self.files = []
        self.logs = []
        self.log_cursor = 0         # track which logs the client has seen
        self.thread = None
        self.results = {}
        self._lock = threading.Lock()  # thread-safe state updates
        self.completed_at = None
        self._stop_requested = False

    # ------------------------------------------------------------------
    # Filesystem scanning — discover past analyses
    # ------------------------------------------------------------------

    def scan_companies(self) -> list:
        """Scan reports/ directory and return info about all analyzed companies."""
        companies = []
        if not os.path.isdir(REPORTS_DIR):
            return companies

        for ticker in sorted(os.listdir(REPORTS_DIR)):
            company_dir = os.path.join(REPORTS_DIR, ticker)
            if not os.path.isdir(company_dir):
                continue

            files = sorted(os.listdir(company_dir))
            if not files:
                continue

            info = {
                "ticker": ticker,
                "company_name": ticker,
                "report_dates": [],
                "files": files,
                "completed_steps": [],
            }

            # Try to load metadata for richer info
            meta_path = os.path.join(company_dir, "reports_metadata.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    info["company_name"] = meta.get("company_name", ticker)
                    info["report_dates"] = [
                        r["date"] for r in meta.get("reports_downloaded", [])
                    ]
                except (json.JSONDecodeError, KeyError):
                    pass

            # If no dates from metadata, infer from PDF filenames
            if not info["report_dates"]:
                for fn in files:
                    if fn.endswith(".pdf") and fn != "report.pdf":
                        m = re.match(r"(\d{4}-\d{2}-\d{2})\.pdf$", fn)
                        if m:
                            info["report_dates"].append(m.group(1))
                info["report_dates"].sort()

            # Infer completed steps from files present
            completed = set()
            completed.add("select_company")  # always done if folder exists
            for fn in files:
                for pattern, step in self.STEP_FILE_MAP.items():
                    if fn == pattern or fn.endswith(pattern):
                        completed.add(step)
            info["completed_steps"] = sorted(completed)

            # Check for report.html/pdf
            info["has_report_html"] = "report.html" in files
            info["has_report_pdf"] = "report.pdf" in files

            companies.append(info)

        return companies

    def load_company(self, ticker: str) -> dict:
        """Load a previously analyzed company from the filesystem into the manager state."""
        companies = self.scan_companies()
        match = None
        for c in companies:
            if c["ticker"].upper() == ticker.upper():
                match = c
                break

        if not match:
            return {"error": f"No analysis found for {ticker}"}

        # Only load if not currently running
        if self.state == "running":
            return {"error": "Pipeline is currently running"}

        with self._lock:
            self.state = "done"
            self.ticker = match["ticker"]
            self.company_name = match["company_name"]
            self.report_dates = match["report_dates"]
            self.current_step = None
            self.completed_steps = match["completed_steps"]
            self.failed_steps = []
            self.files = match["files"]
            self.logs = [f"Loaded previous analysis for {match['ticker']}"]
            self.log_cursor = 0
            # Read completion timestamp
            self.completed_at = None
            meta_path = os.path.join(REPORTS_DIR, match["ticker"], "pipeline_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    self.completed_at = meta.get("completed_at")
                except Exception:
                    pass

        return {"status": "loaded", "ticker": match["ticker"], "company": match}

    def start(self, ticker=None):
        """Start the pipeline in a background thread."""
        with self._lock:
            if self.state == "running":
                return {"error": "Pipeline already running"}

            self.state = "running"
            self.ticker = ticker
            self.company_name = None
            self.report_dates = []
            self.current_step = None
            self.completed_steps = []
            self.failed_steps = []
            self.files = []
            self.logs = []
            self.log_cursor = 0
            self.results = {}
            self.completed_at = None

        self._stop_requested = False
        self.thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.thread.start()

        return {"status": "started", "ticker": ticker}

    def stop(self):
        """Request the pipeline to stop and kill active Claude processes."""
        if self.state != "running":
            return {"error": "Pipeline is not running"}
        self._stop_requested = True
        self._log("Stop requested — terminating active processes…")
        log_to_runlog("STOP REQUESTED by user")
        # Kill any running Claude CLI subprocesses immediately
        from claude_wrapper import kill_active_processes
        kill_active_processes()
        return {"status": "stopping"}

    def _check_stop(self):
        """Return True if the pipeline should stop. Transitions state to done."""
        if self._stop_requested:
            self._log("Pipeline stopped by user. Completed results are preserved.")
            log_to_runlog("PIPELINE STOPPED by user")
            log_to_runlog(f"Completed steps: {self.completed_steps}")
            self.state = "done"
            self.current_step = None
            self.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                meta_path = os.path.join(ensure_company_dir(self.ticker), "pipeline_meta.json")
                with open(meta_path, "w") as f:
                    json.dump({"completed_at": self.completed_at}, f)
            except Exception:
                pass
            return True
        return False

    def run_single_step(self, ticker, step_name):
        """Run a single pipeline step in a background thread."""
        if self.state == "running":
            return {"error": "Pipeline already running"}

        valid_steps = [
            "select_company", "research_company", "get_reports", "get_numbers",
            "extract_goals", "analyze_tone", "analyze_price", "get_logo",
            "compare_reports", "generate_report", "ten_point_analysis", "animate"
        ]
        if step_name not in valid_steps:
            return {"error": f"Unknown step: {step_name}"}

        if not ticker:
            return {"error": "No ticker specified"}

        # Load existing state so we have report_dates etc.
        company_dir = os.path.join(REPORTS_DIR, ticker)
        if not os.path.isdir(company_dir):
            return {"error": f"No analysis found for {ticker}"}

        self.state = "running"
        self.ticker = ticker
        self.company_name = ticker
        self.current_step = step_name
        # Keep existing completed/failed but remove this step so it can re-run
        if step_name in self.completed_steps:
            self.completed_steps.remove(step_name)
        if step_name in self.failed_steps:
            self.failed_steps.remove(step_name)
        self.logs = [f"Re-running step: {step_name} for {ticker}"]
        self.log_cursor = 0

        # Read report_dates from metadata
        meta_path = os.path.join(company_dir, "reports_metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
                self.report_dates = meta.get("report_dates", [])
            except Exception:
                self.report_dates = []
        else:
            self.report_dates = []

        # Refresh files list
        self.files = sorted(os.listdir(company_dir))

        self.thread = threading.Thread(
            target=self._run_single_step, args=(ticker, step_name), daemon=True
        )
        self.thread.start()

        return {"status": "started", "ticker": ticker, "step": step_name}

    def _run_single_step(self, ticker, step_name):
        """Execute a single pipeline step."""
        try:
            pipeline = EarningsPipeline(ticker=ticker, company_name=ticker)
            pipeline.report_dates = self.report_dates

            set_log_ticker(ticker)
            log_to_runlog(f"RE-RUN STEP: {step_name}")

            self._set_step(step_name, f"Re-running {step_name}…")

            step_runners = {
                "research_company": lambda: pipeline.skills["research_company"].run(
                    ticker=ticker, company_name=ticker,
                ),
                "get_reports": lambda: pipeline.skills["get_reports"].run(
                    ticker=ticker, company_name=ticker,
                ),
                "get_numbers": lambda: self._run_step_numbers(pipeline, ticker),
                "extract_goals": lambda: self._run_step_goals(pipeline, ticker),
                "analyze_tone": lambda: pipeline.skills["analyze_tone"].run(
                    ticker=ticker, report_dates=pipeline.report_dates[:2],
                ),
                "analyze_price": lambda: pipeline.skills["analyze_price"].run(
                    ticker=ticker, report_dates=pipeline.report_dates[:2],
                ),
                "get_logo": lambda: pipeline.skills["get_logo"].run(
                    ticker=ticker, company_name=ticker,
                ),
                "compare_reports": lambda: pipeline.skills["compare_reports"].run(
                    ticker=ticker, report_dates=pipeline.report_dates[:2],
                ),
                "generate_report": lambda: pipeline.skills["generate_report"].run(
                    ticker=ticker, company_name=ticker,
                    report_dates=pipeline.report_dates[:2],
                ),
                "ten_point_analysis": lambda: pipeline.skills["ten_point_analysis"].run(
                    ticker=ticker, company_name=ticker,
                    report_dates=pipeline.report_dates[:2],
                ),
                "animate": lambda: pipeline.skills["animate"].run(
                    ticker=ticker, company_name=ticker,
                ),
            }

            runner = step_runners.get(step_name)
            if not runner:
                self._fail_step(step_name, f"No runner for step: {step_name}")
                self.state = "done"
                return

            result = runner()
            if result.get("success"):
                self._complete_step(step_name)
                # If get_reports returned new dates, update them
                if step_name == "get_reports" and result.get("report_dates"):
                    self.report_dates = result["report_dates"]
            else:
                self._fail_step(step_name, f"{step_name} failed")

            self._refresh_files()
            self.state = "done"
            self.current_step = None
            self.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                meta_path = os.path.join(ensure_company_dir(ticker), "pipeline_meta.json")
                with open(meta_path, "w") as f:
                    json.dump({"completed_at": self.completed_at}, f)
            except Exception:
                pass
            self._log(f"Step {step_name} re-run complete")
            log_to_runlog(f"Step {step_name} re-run complete")

        except Exception as e:
            logger.error(f"Step re-run error: {e}", exc_info=True)
            self._fail_step(step_name, str(e))
            self.state = "error"

    def _run_step_numbers(self, pipeline, ticker):
        """Run number extraction for all report dates."""
        results = []
        for date in pipeline.report_dates[:2]:
            r = pipeline.skills["get_numbers"].run(ticker=ticker, report_date=date)
            results.append(r)
        return {"success": all(r.get("success") for r in results)}

    def _run_step_goals(self, pipeline, ticker):
        """Run goal extraction for all report dates."""
        results = []
        for date in pipeline.report_dates[:2]:
            r = pipeline.skills["extract_goals"].run(ticker=ticker, report_date=date)
            results.append(r)
        return {"success": all(r.get("success") for r in results)}

    def _run_pipeline(self):
        """Execute the pipeline, updating state as we go."""
        try:
            pipeline = EarningsPipeline(
                ticker=self.ticker,
                company_name=self.company_name,
            )

            # Set the ticker for run.log as early as possible
            if self.ticker:
                set_log_ticker(self.ticker)
                log_to_runlog("=" * 60)
                log_to_runlog(f"PIPELINE START for {self.ticker}")
                log_to_runlog("=" * 60)

            step_order = [
                ("select_company", "Select Company"),
                ("research_company", "Research News"),
                ("get_reports", "Download Reports"),
                ("get_numbers", "Extract Numbers"),
                ("extract_goals", "Extract Goals"),
                ("analyze_tone", "Analyze Tone"),
                ("analyze_price", "Analyze Price"),
                ("get_logo", "Get Logo"),
                ("compare_reports", "Compare Reports"),
                ("generate_report", "Generate Report"),
                ("ten_point_analysis", "Ten-Point Analysis"),
                ("animate", "Create Animation"),
            ]

            # ── Step 1: Select Company ──
            if not self.ticker:
                self._set_step("select_company", "Selecting company…")
                result = pipeline.skills["select_company"].run()
                if result.get("success"):
                    pipeline.ticker = result["ticker"]
                    pipeline.company_name = result["company_name"]
                    self.ticker = result["ticker"]
                    self.company_name = result["company_name"]
                    set_log_ticker(self.ticker)
                    log_to_runlog("=" * 60)
                    log_to_runlog(f"PIPELINE START for {self.ticker} ({self.company_name})")
                    log_to_runlog("=" * 60)
                    self._complete_step("select_company")
                    self._log(f"Selected: {self.company_name} ({self.ticker})")
                else:
                    self._fail_step("select_company", "Failed to select company")
                    self.state = "error"
                    return
            else:
                pipeline.ticker = self.ticker
                pipeline.company_name = self.ticker
                self.company_name = self.ticker
                self._complete_step("select_company")
                self._log(f"Using provided ticker: {self.ticker}")

            # ══════════════════════════════════════════════════════════
            # PHASE 1: Steps 2 + 3 in PARALLEL (Research + Download)
            # ══════════════════════════════════════════════════════════
            self._log("▶ Phase 1: Research + Download (parallel)")
            log_to_runlog("▶ Phase 1: Research + Download (parallel)")

            self._set_step("research_company", "Researching news…")
            self._set_step("get_reports", "Downloading SEC filings…")

            def _run_research():
                r = pipeline.skills["research_company"].run(
                    ticker=pipeline.ticker,
                    company_name=pipeline.company_name,
                )
                self.results["research_company"] = r
                if r.get("success"):
                    self._complete_step("research_company")
                    self._refresh_files()
                else:
                    self._fail_step("research_company", "News research failed")
                return r

            def _run_get_reports():
                r = pipeline.skills["get_reports"].run(
                    ticker=pipeline.ticker,
                    company_name=pipeline.company_name,
                )
                self.results["get_reports"] = r
                if r.get("success"):
                    with self._lock:
                        pipeline.report_dates = r.get("report_dates", [])
                        self.report_dates = pipeline.report_dates
                    self._complete_step("get_reports")
                    self._log(f"Downloaded reports: {self.report_dates}")
                    self._refresh_files()
                else:
                    self._fail_step("get_reports", "Report download failed")
                return r

            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_research = pool.submit(_run_research)
                fut_reports = pool.submit(_run_get_reports)

                research_result = fut_research.result()
                reports_result = fut_reports.result()

            # Reports are required to continue
            if not reports_result.get("success"):
                self.state = "error"
                return

            if self._check_stop():
                return

            if len(pipeline.report_dates) < 2:
                self._log("WARNING: fewer than 2 reports found, analysis may be limited")

            # ══════════════════════════════════════════════════════════
            # PHASE 2: Steps 4 + 5 + 6 in PARALLEL (Numbers, Goals, Tone)
            # ══════════════════════════════════════════════════════════
            self._log("▶ Phase 2: Numbers + Goals + Tone + Price (parallel)")
            log_to_runlog("▶ Phase 2: Numbers + Goals + Tone + Price (parallel)")

            self._set_step("get_numbers", "Extracting financial numbers…")

            def _run_numbers(date):
                self._log(f"Extracting numbers for {date}…")
                return pipeline.skills["get_numbers"].run(
                    ticker=pipeline.ticker, report_date=date,
                )

            def _run_goals(date):
                self._log(f"Extracting goals for {date}…")
                return pipeline.skills["extract_goals"].run(
                    ticker=pipeline.ticker, report_date=date,
                )

            def _run_tone():
                self._log("Analyzing tone across reports…")
                return pipeline.skills["analyze_tone"].run(
                    ticker=pipeline.ticker,
                    report_dates=pipeline.report_dates[:2],
                )

            def _run_price():
                self._log("Analyzing stock price movements…")
                return pipeline.skills["analyze_price"].run(
                    ticker=pipeline.ticker,
                    report_dates=pipeline.report_dates[:2],
                )

            def _run_logo():
                self._log("Fetching company logo…")
                return pipeline.skills["get_logo"].run(
                    ticker=pipeline.ticker,
                    company_name=pipeline.company_name,
                )

            # Run all of 4/5/6/7 concurrently
            numbers_ok = True
            goals_ok = True
            tone_ok = True
            price_ok = True
            logo_ok = True

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {}

                # Submit numbers for each date
                for date in pipeline.report_dates[:2]:
                    futures[pool.submit(_run_numbers, date)] = ("numbers", date)

                # Submit goals for each date
                for date in pipeline.report_dates[:2]:
                    futures[pool.submit(_run_goals, date)] = ("goals", date)

                # Submit tone analysis (needs 2 reports)
                if len(pipeline.report_dates) >= 2:
                    futures[pool.submit(_run_tone)] = ("tone", None)

                # Submit price analysis (needs 2 reports)
                if len(pipeline.report_dates) >= 2:
                    futures[pool.submit(_run_price)] = ("price", None)

                # Submit logo fetch (no dependencies)
                futures[pool.submit(_run_logo)] = ("logo", None)

                for fut in as_completed(futures):
                    task_type, date = futures[fut]
                    try:
                        result = fut.result()
                        ok = result.get("success", False)
                        if task_type == "numbers" and not ok:
                            numbers_ok = False
                        elif task_type == "goals" and not ok:
                            goals_ok = False
                        elif task_type == "tone" and not ok:
                            tone_ok = False
                        elif task_type == "price" and not ok:
                            price_ok = False
                        elif task_type == "logo" and not ok:
                            logo_ok = False
                    except Exception as e:
                        self._log(f"ERROR in {task_type} ({date}): {e}")
                        log_to_runlog(f"ERROR in {task_type} ({date}): {e}")
                        if task_type == "numbers":
                            numbers_ok = False
                        elif task_type == "goals":
                            goals_ok = False
                        elif task_type == "tone":
                            tone_ok = False
                        elif task_type == "price":
                            price_ok = False
                        elif task_type == "logo":
                            logo_ok = False

                    self._refresh_files()

            # Mark steps complete/failed
            if numbers_ok:
                self._complete_step("get_numbers")
            else:
                self._fail_step("get_numbers", "Some number extraction failed")

            if goals_ok:
                self._complete_step("extract_goals")
            else:
                self._fail_step("extract_goals", "Some goal extraction failed")

            if len(pipeline.report_dates) >= 2:
                if tone_ok:
                    self._complete_step("analyze_tone")
                else:
                    self._fail_step("analyze_tone", "Tone analysis failed")
            else:
                self._log("Skipping tone analysis (need 2 reports)")
                self._complete_step("analyze_tone")

            if len(pipeline.report_dates) >= 2:
                if price_ok:
                    self._complete_step("analyze_price")
                else:
                    self._fail_step("analyze_price", "Price analysis failed")
            else:
                self._log("Skipping price analysis (need 2 reports)")
                self._complete_step("analyze_price")

            if logo_ok:
                self._complete_step("get_logo")
            else:
                self._fail_step("get_logo", "Logo fetch failed")

            if self._check_stop():
                return

            # ══════════════════════════════════════════════════════════
            # PHASE 3: Step 8 — Compare Reports (sequential, needs 4+5+6+7)
            # ══════════════════════════════════════════════════════════
            if len(pipeline.report_dates) >= 2:
                self._set_step("compare_reports", "Comparing reports…")
                result = pipeline.skills["compare_reports"].run(
                    ticker=pipeline.ticker,
                    report_dates=pipeline.report_dates[:2],
                )
                if result.get("success"):
                    self._complete_step("compare_reports")
                else:
                    self._fail_step("compare_reports", "Comparison failed")
                self._refresh_files()
            else:
                self._log("Skipping comparison (need 2 reports)")
                self._complete_step("compare_reports")

            if self._check_stop():
                return

            # ══════════════════════════════════════════════════════════
            # PHASE 4: Step 9 — Generate Report (sequential, needs everything)
            # ══════════════════════════════════════════════════════════
            self._set_step("generate_report", "Generating final report…")
            result = pipeline.skills["generate_report"].run(
                ticker=pipeline.ticker,
                company_name=pipeline.company_name,
                report_dates=pipeline.report_dates[:2],
            )
            if result.get("success"):
                self._complete_step("generate_report")
                self._log(f"Report generated: {result.get('pdf_path', 'N/A')}")
            else:
                self._fail_step("generate_report", "Report generation failed")
            self._refresh_files()

            if self._check_stop():
                return

            # ══════════════════════════════════════════════════════════
            # PHASE 5: Step 11 — Ten-Point Analysis (needs report + price)
            # ══════════════════════════════════════════════════════════
            self._set_step("ten_point_analysis", "Generating ten-point analysis…")
            result = pipeline.skills["ten_point_analysis"].run(
                ticker=pipeline.ticker,
                company_name=pipeline.company_name,
                report_dates=pipeline.report_dates[:2],
            )
            if result.get("success"):
                self._complete_step("ten_point_analysis")
            else:
                self._fail_step("ten_point_analysis", "Ten-point analysis failed")
            self._refresh_files()

            if self._check_stop():
                return

            # ══════════════════════════════════════════════════════════
            # PHASE 6: Step 12 — Animate (needs bullets + logo + ohlc)
            # ══════════════════════════════════════════════════════════
            self._set_step("animate", "Creating animation video…")
            result = pipeline.skills["animate"].run(
                ticker=pipeline.ticker,
                company_name=pipeline.company_name,
            )
            if result.get("success"):
                self._complete_step("animate")
                self._log(f"Animation created: {result.get('video_path', 'N/A')}")
            else:
                self._fail_step("animate", "Animation creation failed")
            self._refresh_files()

            # ── Done ──
            self.state = "done"
            self.current_step = None
            self.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                meta_path = os.path.join(ensure_company_dir(self.ticker), "pipeline_meta.json")
                with open(meta_path, "w") as f:
                    json.dump({"completed_at": self.completed_at}, f)
            except Exception:
                pass
            self._log("Pipeline completed successfully!")
            log_to_runlog("=" * 60)
            log_to_runlog("PIPELINE COMPLETED SUCCESSFULLY")
            log_to_runlog("=" * 60)

        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            self._log(f"ERROR: {str(e)}")
            log_to_runlog(f"PIPELINE ERROR: {str(e)}")
            self.state = "error"

    def _set_step(self, step, msg=""):
        with self._lock:
            self.current_step = step
        if msg:
            self._log(f"STEP: {msg}")
            log_to_runlog(f"STEP: {msg}")

    def _complete_step(self, step):
        with self._lock:
            if step not in self.completed_steps:
                self.completed_steps.append(step)
        self._log(f"✓ {step} completed")
        log_to_runlog(f"✓ {step} completed")

    def _fail_step(self, step, msg=""):
        with self._lock:
            if step not in self.failed_steps:
                self.failed_steps.append(step)
        self._log(f"✗ {step} failed: {msg}")
        log_to_runlog(f"✗ {step} failed: {msg}")

    def _log(self, msg):
        with self._lock:
            self.logs.append(msg)

    def _refresh_files(self):
        """Update the list of files in the company directory."""
        if not self.ticker:
            return
        company_dir = ensure_company_dir(self.ticker)
        if os.path.exists(company_dir):
            with self._lock:
                self.files = sorted(os.listdir(company_dir))

    def get_status(self):
        """Return current pipeline state for the API."""
        with self._lock:
            # Only send new logs
            new_logs = self.logs[self.log_cursor:]
            self.log_cursor = len(self.logs)

            return {
                "state": self.state,
                "ticker": self.ticker,
                "company_name": self.company_name,
                "report_dates": self.report_dates,
                "current_step": self.current_step,
                "completed_steps": list(self.completed_steps),
                "failed_steps": list(self.failed_steps),
                "files": list(self.files),
                "logs": new_logs,
                "completed_at": self.completed_at,
            }


# Singleton pipeline manager
manager = PipelineManager()


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the earnings dashboard."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # API: pipeline status
        if path == "/api/status":
            self._json_response(manager.get_status())
            return

        # API: validate a ticker symbol
        if path.startswith("/api/validate-ticker/"):
            raw_input = unquote(path[len("/api/validate-ticker/"):].strip("/"))
            result = validate_ticker(raw_input)
            self._json_response(result)
            return

        # API: list all analyzed companies
        if path == "/api/companies":
            companies = manager.scan_companies()
            self._json_response(companies)
            return

        # API: load a previous analysis
        if path.startswith("/api/load/"):
            ticker = _safe_ticker(path[len("/api/load/"):].strip("/"))
            if ticker:
                result = manager.load_company(ticker)
                self._json_response(result)
            else:
                self._json_response({"error": "No ticker specified"}, 400)
            return

        # API: run.log for a ticker
        if path.startswith("/api/runlog/"):
            ticker = path[len("/api/runlog/"):].strip("/")
            if ticker:
                log_path = os.path.join(REPORTS_DIR, ticker, "run.log")
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    self._json_response({"content": content, "ticker": ticker})
                else:
                    self._json_response({"content": "", "ticker": ticker})
            else:
                self._json_response({"error": "No ticker specified"}, 400)
            return

        # API: read file
        if path.startswith("/api/file/"):
            parts = path[len("/api/file/"):].split("/", 1)
            if len(parts) == 2:
                ticker, filename = parts
                # Prevent path traversal: reject '..' in both ticker and filename
                if ".." in ticker or ".." in filename or "/" in filename:
                    self._json_response({"error": "Invalid path"}, 400)
                    return
                filepath = os.path.join(REPORTS_DIR, ticker, filename)
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    self._json_response({"content": content, "filename": filename})
                else:
                    self._json_response({"error": "File not found"}, 404)
            else:
                self._json_response({"error": "Invalid path"}, 400)
            return

        # API: OHLC data for charting
        if path.startswith("/api/ohlc/"):
            ticker = _safe_ticker(path[len("/api/ohlc/"):].strip("/"))
            if ticker:
                ohlc_path = os.path.join(REPORTS_DIR, ticker, "ohlc.json")
                if os.path.exists(ohlc_path):
                    with open(ohlc_path, "r", encoding="utf-8") as f:
                        ohlc = json.load(f)
                    self._json_response(ohlc)
                else:
                    self._json_response({"error": "No OHLC data found"}, 404)
            else:
                self._json_response({"error": "No ticker specified"}, 400)
            return

        # API: logo image
        if path.startswith("/api/logo/"):
            ticker = _safe_ticker(path[len("/api/logo/"):].strip("/"))
            if ticker:
                logo_path = os.path.join(REPORTS_DIR, ticker, "logo.jpeg")
                if os.path.exists(logo_path):
                    self._serve_file(logo_path)
                else:
                    self.send_error(404, "Logo not found")
            else:
                self.send_error(400, "No ticker specified")
            return

        # API: bullets.json
        if path.startswith("/api/bullets/"):
            ticker = _safe_ticker(path[len("/api/bullets/"):].strip("/"))
            if ticker:
                bullets_path = os.path.join(REPORTS_DIR, ticker, "bullets.json")
                if os.path.exists(bullets_path):
                    with open(bullets_path, "r", encoding="utf-8") as f:
                        bullets = json.load(f)
                    self._json_response(bullets)
                else:
                    self._json_response({"error": "No bullets data found"}, 404)
            else:
                self._json_response({"error": "No ticker specified"}, 400)
            return

        # API: video file
        if path.startswith("/api/video/"):
            ticker = _safe_ticker(path[len("/api/video/"):].strip("/"))
            if ticker:
                video_path = os.path.join(REPORTS_DIR, ticker, "overview.mp4")
                if os.path.exists(video_path):
                    self._serve_file(video_path)
                else:
                    self.send_error(404, "Video not found")
            else:
                self.send_error(400, "No ticker specified")
            return

        # API: report links
        if path.startswith("/api/report-links/"):
            ticker = path[len("/api/report-links/"):]
            company_dir = os.path.join(REPORTS_DIR, ticker)
            result = {}
            if os.path.exists(os.path.join(company_dir, "report.html")):
                result["html"] = f"/reports/{ticker}/report.html"
            if os.path.exists(os.path.join(company_dir, "report.pdf")):
                result["pdf"] = f"/reports/{ticker}/report.pdf"
            self._json_response(result)
            return

        # Serve report files (PDFs, etc.)
        if path.startswith("/reports/"):
            rel = path[len("/reports/"):]
            filepath = os.path.join(REPORTS_DIR, rel)
            if os.path.exists(filepath):
                self._serve_file(filepath)
            else:
                self.send_error(404)
            return

        # Static files
        if path.startswith("/static/"):
            filepath = os.path.join(
                os.path.dirname(STATIC_DIR), path.lstrip("/")
            )
            if os.path.exists(filepath):
                self._serve_file(filepath)
            else:
                self.send_error(404)
            return

        # Serve /img/, /css/, /js/ directly from static dir
        if path.startswith("/img/") or path.startswith("/css/") or path.startswith("/js/"):
            filepath = os.path.join(STATIC_DIR, path.lstrip("/"))
            if os.path.exists(filepath):
                self._serve_file(filepath)
            else:
                self.send_error(404)
            return

        # Root → serve index.html (also handle hash routes like /#NVDA)
        if path == "/" or path == "/index.html":
            filepath = os.path.join(STATIC_DIR, "index.html")
            self._serve_file(filepath)
            return

        self.send_error(404)

    def do_HEAD(self):
        """Handle HEAD requests for API endpoints (existence checks)."""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        mime_map = {".mp4": "video/mp4", ".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}

        # HEAD for /api/video/<ticker>
        if path.startswith("/api/video/"):
            ticker = _safe_ticker(path[len("/api/video/"):].strip("/"))
            filepath = os.path.join(REPORTS_DIR, ticker, "overview.mp4")
            if os.path.exists(filepath):
                size = os.path.getsize(filepath)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(size))
                self.end_headers()
            else:
                self.send_error(404)
            return

        # HEAD for /api/logo/<ticker>
        if path.startswith("/api/logo/"):
            ticker = _safe_ticker(path[len("/api/logo/"):].strip("/"))
            filepath = os.path.join(REPORTS_DIR, ticker, "logo.jpeg")
            if os.path.exists(filepath):
                size = os.path.getsize(filepath)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(size))
                self.end_headers()
            else:
                self.send_error(404)
            return

        # HEAD for /api/report/<ticker>
        if path.startswith("/api/report/"):
            ticker = _safe_ticker(path[len("/api/report/"):].strip("/"))
            filepath = os.path.join(REPORTS_DIR, ticker, "report.pdf")
            if os.path.exists(filepath):
                size = os.path.getsize(filepath)
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(size))
                self.end_headers()
            else:
                self.send_error(404)
            return

        # For all other paths, fall back to default HEAD behavior
        super().do_HEAD()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # API: delete a company's analysis
        if path.startswith("/api/company/"):
            ticker = _safe_ticker(path[len("/api/company/"):].strip("/"))
            if not ticker:
                self._json_response({"error": "No ticker specified"}, 400)
                return

            company_dir = os.path.join(REPORTS_DIR, ticker)
            if not os.path.isdir(company_dir):
                self._json_response({"error": f"No analysis found for {ticker}"}, 404)
                return

            # If this company is currently loaded, reset state
            if manager.ticker and manager.ticker.upper() == ticker:
                if manager.state == "running":
                    self._json_response({"error": "Cannot delete while pipeline is running"}, 409)
                    return
                with manager._lock:
                    manager.state = "idle"
                    manager.ticker = None
                    manager.company_name = None
                    manager.report_dates = []
                    manager.current_step = None
                    manager.completed_steps = []
                    manager.failed_steps = []
                    manager.files = []
                    manager.logs = []
                    manager.log_cursor = 0

            # Delete all files and the directory
            try:
                shutil.rmtree(company_dir)
                logger.info(f"Deleted analysis for {ticker}: {company_dir}")
                self._json_response({"status": "deleted", "ticker": ticker})
            except Exception as e:
                logger.error(f"Failed to delete {company_dir}: {e}")
                self._json_response({"error": str(e)}, 500)
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # API: rerun analysis (delete + start)
        if path.startswith("/api/rerun/"):
            ticker = _safe_ticker(path[len("/api/rerun/"):].strip("/"))
            if not ticker:
                self._json_response({"error": "No ticker specified"}, 400)
                return

            if manager.state == "running":
                self._json_response({"error": "Pipeline already running"}, 409)
                return

            # Delete existing assets
            company_dir = os.path.join(REPORTS_DIR, ticker)
            if os.path.isdir(company_dir):
                try:
                    shutil.rmtree(company_dir)
                    logger.info(f"Deleted analysis for rerun: {ticker}")
                except Exception as e:
                    logger.error(f"Failed to delete for rerun {company_dir}: {e}")
                    self._json_response({"error": f"Failed to clean up: {e}"}, 500)
                    return

            # Start pipeline
            result = manager.start(ticker)
            self._json_response(result)
            return

        # API: run single step
        if path.startswith("/api/run-step/"):
            parts = path[len("/api/run-step/"):].strip("/").split("/", 1)
            if len(parts) != 2:
                self._json_response({"error": "Usage: /api/run-step/<ticker>/<step_name>"}, 400)
                return
            ticker = parts[0].upper()
            step_name = parts[1]
            result = manager.run_single_step(ticker, step_name)
            self._json_response(result)
            return

        # API: chat with Claude about the current stock
        if path == "/api/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            body = {}
            if content_length:
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    pass

            message = body.get("message", "").strip()
            ticker = body.get("ticker", "").strip().upper()
            history = body.get("history", [])

            if not message:
                self._json_response({"error": "No message provided"}, 400)
                return

            # Build context about available files for this ticker
            file_context = ""
            file_contents = {}
            if ticker:
                company_dir = os.path.join(REPORTS_DIR, ticker)
                if os.path.isdir(company_dir):
                    all_files = sorted(os.listdir(company_dir))
                    file_context = f"\n\nAvailable analysis files for {ticker}:\n"
                    for fn in all_files:
                        fpath = os.path.join(company_dir, fn)
                        size = os.path.getsize(fpath)
                        file_context += f"  - {fn} ({size:,} bytes)\n"

                    # Read key markdown/text files to provide as context
                    readable_exts = {'.md', '.txt', '.json'}
                    skip_files = {'run.log', 'ohlc.json', 'reports_metadata.json'}
                    for fn in all_files:
                        if fn in skip_files:
                            continue
                        ext = os.path.splitext(fn)[1].lower()
                        if ext in readable_exts:
                            fpath = os.path.join(company_dir, fn)
                            try:
                                with open(fpath, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                # Truncate very large files
                                if len(content) > 15000:
                                    content = content[:15000] + "\n\n... [truncated] ..."
                                file_contents[fn] = content
                            except Exception:
                                pass

            # Build the system prompt
            system_prompt = f"""You are a financial analyst assistant for the Stock Analyst dashboard.
You help users understand and analyze quarterly earnings data for public companies.

RULES:
1. Only reference data that exists in the provided files — never fabricate numbers
2. Be concise but thorough — use markdown formatting
3. If asked about data that isn't available, say so clearly
4. Focus on actionable insights: trends, risks, opportunities
5. When citing numbers, reference the source file
"""
            if ticker:
                system_prompt += f"\nCurrently analyzing: {ticker}"
                system_prompt += file_context

            # Build user prompt with file contents as context
            user_prompt = ""
            if file_contents:
                user_prompt += "Here are the analysis files for context:\n\n"
                for fn, content in file_contents.items():
                    user_prompt += f"=== {fn} ===\n{content}\n\n"
                user_prompt += "---\n\n"
            user_prompt += f"User question: {message}"

            # Call Claude
            from claude_wrapper import call_claude
            try:
                result = call_claude(
                    message=user_prompt,
                    conversation_history=history[-5:] if history else None,
                    system_prompt=system_prompt,
                )
                if result["success"]:
                    self._json_response({"response": result["response"]})
                else:
                    self._json_response({"error": result["error"] or "Claude call failed"}, 500)
            except Exception as e:
                logger.error(f"Chat error: {e}")
                self._json_response({"error": str(e)}, 500)
            return

        # API: start pipeline
        if path == "/api/run":
            content_length = int(self.headers.get("Content-Length", 0))
            body = {}
            if content_length:
                raw = self.rfile.read(content_length)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    pass

            ticker = body.get("ticker", "").strip().upper() or None
            result = manager.start(ticker)
            self._json_response(result)
            return

        if path == "/api/stop":
            result = manager.stop()
            self._json_response(result)
            return

        self.send_error(404)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json_response(self, data, status=200):
        """Send a JSON response."""
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filepath):
        """Serve a static file with correct MIME type."""
        ext = os.path.splitext(filepath)[1].lower()
        mime_map = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".mp4": "video/mp4",
            ".jpeg": "image/jpeg",
        }
        mime = mime_map.get(ext, "application/octet-stream")
        try:
            mode = "rb"
            with open(filepath, mode) as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, format, *args):
        """Suppress default request logging to keep output clean."""
        pass


def run_server(host=None, port=None):
    """Start the dashboard server."""
    host = host or SERVER_HOST
    port = port or SERVER_PORT

    # Ensure directories exist
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)

    server = HTTPServer((host, port), DashboardHandler)
    logger.info(f"Dashboard server running at http://{host}:{port}")
    logger.info(f"Static files: {STATIC_DIR}")
    logger.info(f"Reports dir: {REPORTS_DIR}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down…")
        server.server_close()


if __name__ == "__main__":
    run_server()