#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║       CLAUDE WRAPPER — Quarterly Earnings Research Assistant                ║
║                                                                            ║
║  Based on the Generic Claude Wrapper Template — CLI-Powered Assistant       ║
║                                                                            ║
║  Uses `claude --print` CLI to send prompts and receive responses.           ║
║  Each skill builds a prompt, calls call_claude(), and parses the result.    ║
╚══════════════════════════════════════════════════════════════════════════════╝

Usage:
    from claude_wrapper import call_claude, call_claude_for_json, call_claude_for_markdown

    result = call_claude(
        message="Analyze this earnings report...",
        conversation_history=[],
    )

    if result["success"]:
        print(result["response"])
    else:
        print("Error:", result["error"])
"""

import subprocess
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Track active Claude CLI subprocesses so they can be killed on stop
_active_processes = set()


def kill_active_processes():
    """Kill all running Claude CLI subprocesses."""
    for proc in list(_active_processes):
        try:
            proc.kill()
        except Exception:
            pass
    _active_processes.clear()


# ------------------------------------------------------------------------------
# RUN LOG — appends timestamped entries to reports/<TICKER>/run.log
# ------------------------------------------------------------------------------

_current_ticker = None


def set_log_ticker(ticker: str):
    """Set the active ticker so run-log entries go to the right company folder."""
    global _current_ticker
    _current_ticker = ticker.upper() if ticker else None


def log_to_runlog(message: str, ticker: str = None):
    """Append a timestamped line to reports/<TICKER>/run.log."""
    tk = (ticker or _current_ticker or "").upper()
    if not tk:
        return
    from config import REPORTS_DIR
    company_dir = os.path.join(REPORTS_DIR, tk)
    os.makedirs(company_dir, exist_ok=True)
    log_path = os.path.join(company_dir, "run.log")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception as e:
        logger.warning(f"Failed to write run.log: {e}")


# ==============================================================================
# ▼▼▼  CONFIGURE THIS BLOCK  ▼▼▼
# ==============================================================================

WRAPPER_CONFIG = {
    # No MCP client needed — this assistant uses Claude's own reasoning
    "client_module": None,
    "client_class": None,

    # Assistant identity
    "assistant_name": "Quarterly Earnings Research Assistant",
    "domain_desc": "analyzing public company quarterly earnings reports, SEC filings, financial data extraction, tonal analysis, and comparative reporting",

    # Personality bullet points (shown to Claude as its persona)
    "persona": [
        "Expert financial analyst with deep knowledge of SEC filings and earnings reports",
        "Data-driven, precise, and insightful — never fabricates numbers",
        "Proactively highlights anomalies, trends, and red flags in financial data",
        "Uses clear formatting with headers, tables, and bullet points for readability",
        "Maintains analytical objectivity — presents observations, not investment advice",
        "Grounds every claim in evidence from the source documents",
    ],

    # Tools description — these are the skills available in the pipeline
    "tools_desc": [
        "select-company    — Find companies reporting earnings today, pick one at random",
        "research-company  — Search for top 5 news stories, summarize into news.md",
        "get-reports       — Download last 2 SEC filings (10-Q/10-K) as PDFs",
        "get-numbers       — Extract financial numbers from report PDFs into _numbers.md",
        "extract-goals     — Extract top 5 strategic goals from first 10 pages into _goals.md",
        "analyze-tone      — Compare messaging tone across two reports into _tone.md",
        "compare-reports   — Cross-compare numbers, goals, tone into _compare.md",
        "generate-report   — Produce a 5-page styled PDF report from all analyses",
    ],

    # Domain format hint
    "domain_format_hint": (
        "Company tickers: uppercase, e.g. AAPL, MSFT, GOOGL\n"
        "Report dates: YYYY-MM-DD format, e.g. 2024-10-31"
    ),

    # Unique tag used in file markers
    "result_tag": "EARNINGS",

    # Where Claude saves its result JSON
    "result_file": "/tmp/earnings_result.json",

    # Temp files for Claude CLI stdout/stderr
    "output_file": "/tmp/claude_earnings_output.txt",
    "error_file":  "/tmp/claude_earnings_error.txt",

    # Working directory passed to Claude CLI subprocess
    "working_dir": "/workspace/earnings_app",

    # Subprocess timeout in seconds
    "timeout": 600,
}

# ==============================================================================
# ▲▲▲  END OF CONFIG  ▲▲▲
# ==============================================================================


# ------------------------------------------------------------------------------
# SYSTEM PROMPT BUILDER
# ------------------------------------------------------------------------------

def build_system_prompt(config: dict = None) -> str:
    """Build the Claude system prompt from WRAPPER_CONFIG."""
    cfg = config or WRAPPER_CONFIG

    persona_lines = "\n".join(f"- {p}" for p in cfg["persona"])
    tools_lines   = "\n".join(f"- {t}" for t in cfg["tools_desc"])
    result_tag    = cfg["result_tag"]
    result_file   = cfg["result_file"]
    working_dir   = cfg["working_dir"]

    return f"""\
You are a {cfg['assistant_name']}.
You help users with {cfg['domain_desc']}.

Your personality:
{persona_lines}

CRITICAL RULES:
1. Only report numbers and facts you can verify from the provided data
2. NEVER fabricate, estimate, or hallucinate financial figures
3. If data is missing or unclear, explicitly state that
4. Use markdown formatting for all output
5. Support every observation with evidence from the source text
6. When analyzing financial reports, focus on: revenue, earnings, margins, cash flow, guidance

AVAILABLE PIPELINE SKILLS:
{tools_lines}

FORMAT RULES:
{cfg['domain_format_hint']}

When you produce structured results, save them as JSON:
result = {{"data": your_data, "query_type": "skill_name"}}
with open('{result_file}', 'w') as f:
    json.dump(result, f)
print("{result_tag}_FILE_SAVED:{result_file}")

Working directory: {working_dir}
"""


# ------------------------------------------------------------------------------
# MAIN WRAPPER FUNCTION
# ------------------------------------------------------------------------------

def call_claude(
    message: str,
    conversation_history: list = None,
    system_prompt: str = None,
    config: dict = None,
) -> dict:
    """
    Call the Claude Code CLI with the configured system prompt.

    Args:
        message:              The user's question or instruction
        conversation_history: Optional list of {"role": "user"/"assistant", "content": "..."}
                              Last 5 messages are used for context.
        system_prompt:        Override the auto-generated system prompt
        config:               Override WRAPPER_CONFIG

    Returns:
        {"success": True/False, "response": "...", "error": None or "..."}
    """
    cfg = config or WRAPPER_CONFIG

    try:
        # Build conversation context (last 5 messages)
        if conversation_history:
            context = "\n\n".join([
                f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                for m in conversation_history[-5:]
            ])
            full_prompt = f"{context}\n\nUser: {message}"
        else:
            full_prompt = message

        active_prompt = system_prompt or build_system_prompt(cfg)

        # Write prompt to temp file (avoids shell escaping issues with Claude CLI)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(full_prompt)
            prompt_path = f.name

        output_path = cfg["output_file"]
        error_path  = cfg["error_file"]

        # Build shell command
        cmd_parts = ["claude", "--print", "--system-prompt", active_prompt]
        cmd_str   = " ".join(f'"{c}"' if " " in c else c for c in cmd_parts)
        shell_cmd = f"{cmd_str} < {prompt_path} > {output_path} 2> {error_path}"

        # Log the call to run.log
        prompt_preview = message[:120].replace("\n", " ")
        log_to_runlog(f"CLAUDE CALL: {prompt_preview}...")

        logger.info(f"[{cfg['assistant_name']}] Running Claude CLI...")
        t0 = time.time()

        proc = subprocess.Popen(
            shell_cmd,
            shell=True,
            cwd=cfg["working_dir"],
        )
        # Register so external code can kill this process on stop
        _active_processes.add(proc)
        try:
            proc.wait(timeout=cfg["timeout"])
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            _active_processes.discard(proc)
            raise
        finally:
            _active_processes.discard(proc)

        ret = proc
        ret.returncode = proc.returncode

        elapsed = time.time() - t0

        # Read outputs
        stdout = _read_file(output_path)
        stderr = _read_file(error_path)

        # Cleanup temp prompt file
        try:
            os.unlink(prompt_path)
        except Exception:
            pass

        if ret.returncode == 0:
            resp_preview = stdout.strip()[:150].replace("\n", " ")
            log_to_runlog(f"CLAUDE OK ({elapsed:.1f}s, {len(stdout)} chars): {resp_preview}...")
            return {"success": True, "response": stdout.strip(), "error": None}
        else:
            logger.error(f"Claude CLI error: {stderr}")
            log_to_runlog(f"CLAUDE FAIL ({elapsed:.1f}s): {stderr[:200]}")
            return {"success": False, "response": None, "error": stderr}

    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timeout")
        log_to_runlog(f"CLAUDE TIMEOUT after {cfg['timeout']}s")
        return {"success": False, "response": None, "error": "Request timed out"}
    except Exception as e:
        logger.error(f"Claude CLI exception: {e}")
        log_to_runlog(f"CLAUDE EXCEPTION: {str(e)[:200]}")
        return {"success": False, "response": None, "error": str(e)}


# ------------------------------------------------------------------------------
# CONVENIENCE WRAPPERS FOR SKILLS
# ------------------------------------------------------------------------------

def call_claude_for_json(
    system_prompt: str,
    user_prompt: str,
    config: dict = None,
) -> dict:
    """
    Call Claude and parse the response as JSON.

    Extracts JSON from the response, handling cases where
    Claude wraps JSON in markdown code blocks.

    Returns:
        Parsed JSON as a Python dict, or raises ValueError.
    """
    result = call_claude(
        message=user_prompt,
        system_prompt=system_prompt,
        config=config,
    )

    if not result["success"]:
        raise RuntimeError(f"Claude call failed: {result['error']}")

    text = result["response"]

    # Try to extract JSON from markdown code blocks first
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object or array in the text
        for pattern in [r"\{.*\}", r"\[.*\]"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    continue

        logger.error(f"Failed to parse JSON from Claude response: {text[:500]}")
        raise ValueError("Could not parse JSON from Claude response")


def call_claude_for_markdown(
    system_prompt: str,
    user_prompt: str,
    config: dict = None,
) -> str:
    """
    Call Claude expecting a markdown-formatted response.

    Strips any outer markdown code fences if present.

    Returns:
        Clean markdown text.
    """
    result = call_claude(
        message=user_prompt,
        system_prompt=system_prompt,
        config=config,
    )

    if not result["success"]:
        raise RuntimeError(f"Claude call failed: {result['error']}")

    text = result["response"]

    # Strip outer markdown fences if Claude wraps the whole thing
    text = re.sub(r"^```(?:markdown)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)

    return text.strip()


# ------------------------------------------------------------------------------
# RESULT READER
# ------------------------------------------------------------------------------

def read_result(file_path: str = None, max_age_seconds: int = 300) -> dict:
    """
    Read the result JSON saved by Claude's Python script.
    Returns None if the file doesn't exist or is older than max_age_seconds.
    """
    path = file_path or WRAPPER_CONFIG["result_file"]

    if not os.path.exists(path):
        return None

    try:
        if time.time() - os.path.getmtime(path) > max_age_seconds:
            logger.warning(f"Result file is stale (>{max_age_seconds}s): {path}")
            return None
        with open(path) as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to read result file {path}: {e}")
        return None


def extract_result(raw_response: str, config: dict = None) -> dict:
    """
    Extract structured result from Claude's raw response text.

    Priority order:
      1. File marker tag in response  e.g. EARNINGS_FILE_SAVED:/tmp/result.json
      2. Default result file path from config
      3. Inline ```json ... ``` block in the response text
    """
    cfg = config or WRAPPER_CONFIG
    result_tag  = cfg["result_tag"]
    result_file = cfg["result_file"]

    # Priority 1: file marker in response
    match = re.search(rf"{result_tag}_FILE_SAVED:(/[^\s\n]+\.json)", raw_response)
    if match:
        path = match.group(1)
        logger.info(f"Found file marker: {path}")
        result = read_result(path)
        if result:
            return result

    # Priority 2: default file location
    result = read_result(result_file)
    if result:
        return result

    # Priority 3: inline JSON block
    match = re.search(r"```json\s*(\{.*?\})\s*```", raw_response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None


# ------------------------------------------------------------------------------
# LEGACY COMPATIBILITY — ClaudeWrapper class
# ------------------------------------------------------------------------------

class ClaudeWrapper:
    """
    Class-based wrapper for backward compatibility with skills.
    Delegates to the CLI-based call_claude() functions.
    """

    def __init__(self, api_key: str = None, model: str = None):
        """Initialize. api_key and model are ignored (CLI handles auth)."""
        logger.info("ClaudeWrapper initialized (CLI-based)")

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = None,
        temperature: float = 0.3,
    ) -> str:
        """Send a prompt to Claude CLI and return the text response."""
        result = call_claude(
            message=user_prompt,
            system_prompt=system_prompt,
        )
        if result["success"]:
            return result["response"]
        else:
            raise RuntimeError(f"Claude CLI error: {result['error']}")

    def call_with_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = None,
        temperature: float = 0.2,
    ) -> dict:
        """Call Claude and parse the response as JSON."""
        return call_claude_for_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def call_for_markdown(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = None,
        temperature: float = 0.3,
    ) -> str:
        """Call Claude expecting a markdown-formatted response."""
        return call_claude_for_markdown(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )


# Singleton for convenience
_default_wrapper = None


def get_claude() -> ClaudeWrapper:
    """Get or create the default ClaudeWrapper singleton."""
    global _default_wrapper
    if _default_wrapper is None:
        _default_wrapper = ClaudeWrapper()
    return _default_wrapper


# ------------------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------------------

def _read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


# ------------------------------------------------------------------------------
# QUICK TEST — run directly: python claude_wrapper.py
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    name = WRAPPER_CONFIG["assistant_name"]
    print(f"=== {name} Test ===\n")

    test_message = "What are the key things to look for in a quarterly earnings report?"
    print(f"Sending: '{test_message}'\n")

    result = call_claude(message=test_message)

    if result["success"]:
        print("Claude response:\n")
        print(result["response"])
        print("\n--- Structured data ---")
        data = extract_result(result["response"])
        if data:
            print(json.dumps(data, indent=2)[:1000])
        else:
            print("No structured data extracted.")
    else:
        print(f"Error: {result['error']}")