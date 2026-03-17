"""
Configuration for the Quarterly Earnings Research Application.

All settings can be overridden via environment variables.
"""
import os

# Claude API Configuration
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"))
CLAUDE_MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "8192"))

# SEC EDGAR Configuration
SEC_EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index?q="
SEC_EDGAR_FILINGS = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_EDGAR_FULL_TEXT = "https://efts.sec.gov/LATEST/search-index"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "EarningsResearchApp admin@earningsapp.com")

# Financial Data APIs
YAHOO_FINANCE_BASE = "https://query1.finance.yahoo.com/v8/finance"
FINVIZ_BASE = "https://finviz.com/quote.ashx"

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.environ.get("REPORTS_DIR", os.path.join(BASE_DIR, "reports"))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Dashboard Colors (Golden Ratio Principle: 60-30-10 rule)
# 60% - Primary/Background: Deep navy (trust, stability)
# 30% - Secondary/Surfaces: Slate blue-gray (neutral, professional)
# 10% - Accent split:
#   ~7% - Accent Primary: Emerald green (growth, positive)
#   ~3% - Accent Secondary: Signal red (alerts, negative)
COLORS = {
    "primary": "#0A1628",       # Deep navy - 60% (backgrounds, large areas)
    "secondary": "#1E293B",     # Slate blue-gray - 30% (cards, surfaces)
    "accent_positive": "#10B981",  # Emerald green - 7% (positive indicators)
    "accent_negative": "#EF4444",  # Signal red - 3% (negative indicators, alerts)
    "text_primary": "#F1F5F9",  # Light gray for text on dark
    "text_secondary": "#94A3B8", # Muted gray for secondary text
    "border": "#334155",        # Subtle border color
}

# Server Configuration
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8090"))

# Report Settings
MAX_REPORT_PAGES = int(os.environ.get("MAX_REPORT_PAGES", "5"))
PDF_PAGE_SIZE = os.environ.get("PDF_PAGE_SIZE", "A4")