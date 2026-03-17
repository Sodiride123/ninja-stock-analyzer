#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         RealTimeFinanceData MCP Client — RapidAPI Direct                    ║
║                                                                              ║
║  Configured for the Real-Time Finance Data API by OpenWeb Ninja              ║
║  Endpoints discovered from /v1/mcp/server + /v1/mcp/tools + API docs        ║
║                                                                              ║
║  Usage:                                                                      ║
║      from finance_mcp_client import MCPClient                                ║
║      client = MCPClient()                                                    ║
║      result = client.stock_quote(symbol="AAPL:NASDAQ")                       ║
║      result = client.company_income_statement(symbol="AAPL:NASDAQ")          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import requests
import json
import os
from typing import Any, Dict, List, Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


# ==============================================================================
# ▼▼▼  CONFIGURE THIS BLOCK FOR YOUR MCP  ▼▼▼
# ==============================================================================

MCP_CONFIG = {
    # Human-readable name for this MCP (used in logs and __repr__)
    "name": "RealTimeFinanceData",

    # From static_headers in /v1/mcp/server response
    # Using the same RapidAPI key shared across NinjaTech MCP servers
    "rapidapi_key":  os.environ.get("RAPIDAPI_KEY", "31ef0dc6b9mshc70a433069615a1p1395d8jsn088e5e2ff6e6"),
    "rapidapi_host": "real-time-finance-data.p.rapidapi.com",

    # Map of: method_name -> (http_method, path, required_params, optional_params)
    # Endpoint paths discovered from OpenWeb Ninja API docs at:
    #   https://www.openwebninja.com/api/real-time-finance-data/docs
    # Curl examples confirm paths like:
    #   GET /search?query=...
    #   GET /stock-quote?symbol=...
    #   etc.
    "tools": {
        "search":                       ("GET", "/search",                       ["query"],       ["language"]),
        "market_trends":                ("GET", "/market-trends",                ["trend_type"],  ["country", "language"]),
        "stock_quote":                  ("GET", "/stock-quote",                  ["symbol"],      ["language"]),
        "stock_time_series":            ("GET", "/stock-time-series",            ["symbol"],      ["period", "language"]),
        "stock_news":                   ("GET", "/stock-news",                   ["symbol"],      ["language"]),
        "stock_overview":               ("GET", "/stock-overview",               ["symbol"],      ["language"]),
        "company_income_statement":     ("GET", "/company-income-statement",     ["symbol"],      ["period", "language"]),
        "company_balance_sheet":        ("GET", "/company-balance-sheet",        ["symbol"],      ["period", "language"]),
        "company_cash_flow":            ("GET", "/company-cash-flow",            ["symbol"],      ["period", "language"]),
        "currency_exchange_rate":       ("GET", "/currency-exchange-rate",       ["from_symbol", "to_symbol"], ["language"]),
        "currency_time_series":         ("GET", "/currency-time-series",         ["from_symbol", "to_symbol"], ["period", "language"]),
        "currency_news":                ("GET", "/currency-news",               ["from_symbol", "to_symbol"], ["language"]),
        "stock_quote_yahoo_finance":    ("GET", "/stock-quote-yahoo-finance",    ["symbol"],      []),
        "stock_time_series_yahoo_finance": ("GET", "/stock-time-series-yahoo-finance", ["symbol"], ["period"]),
    },

    # Tools that exist in the MCP server list but are NOT available on RapidAPI.
    "unavailable_tools": {},
}

# ==============================================================================
# ▲▲▲  END OF CONFIG  ▲▲▲
# ==============================================================================


# ------------------------------------------------------------------------------
# INTERNAL HTTP HELPER
# ------------------------------------------------------------------------------

def _request(
    method: str,
    path: str,
    params: Dict[str, Any] = None,
    body: Dict[str, Any] = None,
    api_key: str = None,
    host: str = None,
) -> Any:
    """Execute a GET or POST request against the RapidAPI host."""
    key  = api_key or os.environ.get("RAPIDAPI_KEY")  or MCP_CONFIG["rapidapi_key"]
    h    = host    or os.environ.get("RAPIDAPI_HOST") or MCP_CONFIG["rapidapi_host"]
    base = f"https://{h}"

    headers = {
        "x-rapidapi-key":  key,
        "x-rapidapi-host": h,
        "Content-Type":    "application/json",
    }

    if method.upper() == "POST":
        r = requests.post(f"{base}{path}", headers=headers, json=body or {}, timeout=30)
    else:
        r = requests.get(f"{base}{path}", headers=headers, params=params or {}, timeout=30)

    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code} from {h}{path}: {r.text[:500]}")

    return r.json()


# ------------------------------------------------------------------------------
# GENERIC MCP CLIENT
# ------------------------------------------------------------------------------

class MCPClient:
    """
    RealTimeFinanceData RapidAPI MCP client.

    Credentials are resolved in this order:
      1. Constructor arguments  MCPClient(api_key="...", host="...")
      2. RAPIDAPI_KEY / RAPIDAPI_HOST environment variables
      3. Hardcoded values in MCP_CONFIG

    All tools defined in MCP_CONFIG["tools"] are available via:
      - client.call("tool_name", {"param": "value"})   # generic
      - client.<tool_name>(param=value)                 # named (auto-generated)
    """

    def __init__(self, api_key: str = None, host: str = None, config: dict = None):
        self._api_key = api_key
        self._host    = host
        self._config  = config or MCP_CONFIG
        self._name    = self._config["name"]

        # Auto-generate named methods for every tool in config
        for tool_name, tool_def in self._config["tools"].items():
            self._register_tool(tool_name, tool_def)

        # Auto-generate NotImplementedError stubs for unavailable tools
        for tool_name, message in self._config.get("unavailable_tools", {}).items():
            self._register_unavailable(tool_name, message)

    def _register_tool(self, tool_name: str, tool_def: tuple):
        """Dynamically attach a named method for a tool."""
        http_method, path, required_params, optional_params = tool_def

        def _method(**kwargs):
            return self.call(tool_name, kwargs)

        _method.__name__ = tool_name
        _method.__doc__  = (
            f"Call the '{tool_name}' tool.\n\n"
            f"  Endpoint:  {http_method} {path}\n"
            f"  Required:  {required_params}\n"
            f"  Optional:  {optional_params}"
        )
        setattr(self, tool_name, _method)

    def _register_unavailable(self, tool_name: str, message: str):
        """Attach a stub that raises NotImplementedError."""
        def _stub(**kwargs):
            raise NotImplementedError(message)
        _stub.__name__ = tool_name
        setattr(self, tool_name, _stub)

    def call(self, tool_name: str, arguments: Dict[str, Any] = None) -> Any:
        """
        Generic tool call by name.

        Args:
            tool_name:  Key from MCP_CONFIG["tools"]
            arguments:  Dict of parameters to pass

        Returns:
            Parsed JSON response (dict or list)
        """
        # Check unavailable tools first
        unavailable = self._config.get("unavailable_tools", {})
        if tool_name in unavailable:
            raise NotImplementedError(unavailable[tool_name])

        tools = self._config["tools"]
        if tool_name not in tools:
            available = list(tools.keys())
            raise ValueError(f"Unknown tool '{tool_name}'. Available: {available}")

        http_method, path, required_params, optional_params = tools[tool_name]
        arguments = arguments or {}

        # Validate required params
        missing = [p for p in required_params if p not in arguments]
        if missing:
            raise ValueError(f"Tool '{tool_name}' missing required params: {missing}")

        # Build params/body — include required + any provided optional params
        payload = {k: v for k, v in arguments.items() if v is not None}

        if http_method.upper() == "POST":
            return _request("POST", path, body=payload,
                            api_key=self._api_key, host=self._host)
        else:
            return _request("GET", path, params=payload,
                            api_key=self._api_key, host=self._host)

    def list_tools(self) -> List[str]:
        """List all available tool names."""
        available   = list(self._config["tools"].keys())
        unavailable = [f"{k} (unavailable)" for k in self._config.get("unavailable_tools", {})]
        return available + unavailable

    def __repr__(self):
        return f"MCPClient(name={self._name!r}, host={self._config['rapidapi_host']!r})"


# ------------------------------------------------------------------------------
# QUICK TEST — run directly: python finance_mcp_client.py
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    client = MCPClient()
    pp = lambda x: print(json.dumps(x, indent=2)[:600] if isinstance(x, (dict, list)) else str(x)[:600])

    print(f"=== {MCP_CONFIG['name']} MCP Client Test ===")
    print(f"Host: {MCP_CONFIG['rapidapi_host']}")
    print(f"Tools: {client.list_tools()}\n")

    # Test a subset of tools relevant to earnings analysis
    test_calls = [
        ("stock_quote",              {"symbol": "AAPL:NASDAQ"}),
        ("stock_news",               {"symbol": "AAPL:NASDAQ"}),
        ("stock_overview",           {"symbol": "AAPL:NASDAQ"}),
        ("company_income_statement", {"symbol": "AAPL:NASDAQ", "period": "QUARTERLY"}),
    ]

    for tool_name, args in test_calls:
        print(f"--- {tool_name}({', '.join(f'{k}={v!r}' for k,v in args.items())}) ---")
        try:
            result = client.call(tool_name, args)
            pp(result)
        except Exception as e:
            print(f"  Error: {e}")
        print()

    print("=== Test complete ===")