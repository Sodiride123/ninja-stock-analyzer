# 📊 Ninja Stock Analyzer

An autonomous AI-powered quarterly earnings research application that downloads SEC filings, analyzes financial data, and generates comprehensive reports with animated visualizations.

![Dashboard](https://img.shields.io/badge/Dashboard-Live-10B981?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-AI-FF6B35?style=for-the-badge)

## ✨ Features

- **12-Step Analysis Pipeline** — Fully automated from ticker input to final report
- **SEC EDGAR Integration** — Downloads and parses 10-K/10-Q filings automatically
- **AI-Powered Analysis** — Claude extracts financial numbers, goals, tone, and comparisons
- **Real-Time Price Data** — OHLC stock price analysis via RapidAPI
- **PDF Report Generation** — Professional multi-page reports with cover page and logo
- **Animated OHLC Videos** — 15-second MP4 animations of stock price movement
- **Ten-Point Analysis** — 5 bullish + 5 bearish bullet points per company
- **Web Dashboard** — Real-time pipeline monitoring with tabs for each analysis section
- **Stop Anytime** — Cancel analysis mid-run while keeping completed results
- **Persistent Storage** — All analyses saved to disk, survives restarts
- **Multi-Company Support** — Analyze multiple companies, switch between them instantly
- **AI Chat** — Ask questions about any analyzed company using the Chat tab

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Web Dashboard (:8090)                  │
│  Overview │ News │ Numbers │ Goals │ Tone │ Price │ ...  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Python HTTP Server (server.py)               │
│         API endpoints + Static file serving               │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  Analysis Pipeline                        │
│                                                           │
│  Phase 1: Research + Download (parallel)                  │
│    ├── research-company  →  news.md                       │
│    └── get-reports       →  [date].pdf + [date]_report.txt│
│                                                           │
│  Phase 2: Extract + Analyze (parallel)                    │
│    ├── get-numbers    →  [date]_numbers.md                │
│    ├── extract-goals  →  [date]_goals.md                  │
│    ├── analyze-tone   →  [date]_tone.md                   │
│    ├── analyze-price  →  price.md + ohlc.json             │
│    └── get-logo       →  logo.jpeg                        │
│                                                           │
│  Phase 3: Compare                                         │
│    └── compare-reports →  [d1]_[d2]_compare.md            │
│                                                           │
│  Phase 4: Generate Report                                 │
│    └── generate-report →  report.pdf                      │
│                                                           │
│  Phase 5: Ten-Point Analysis                              │
│    └── ten-point-analysis →  bullets.json                 │
│                                                           │
│  Phase 6: Animate                                         │
│    └── animate →  overview.mp4                            │
└─────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Claude Code CLI (pre-installed on supported VMs)
- `/root/.claude/settings.json` with valid `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL`

### 1. Clone the repo

```bash
git clone git@github.com:Sodiride123/ninja-stock-analyzer.git
cd ninja-stock-analyzer
```

### 2. Install dependencies (run once)

```bash
bash setup.sh
```

This installs: `wkhtmltopdf`, `poppler-utils`, `ffmpeg`, `xvfb`, OpenGL libraries, and Python packages.

### 3. Start the app

```bash
bash start.sh
```

The app auto-reads credentials from `/root/.claude/settings.json` — no `.env` file needed.

Open **http://localhost:8090**

---

## 🖥️ Dashboard

### Running an Analysis

- **Type a ticker** (e.g. `AAPL`) and click **▶ Run Analysis**
- **⚡ Auto-Select** — randomly picks a known-good US company and starts analysis
- **■ Stop** — appears during analysis; stops after the current step, keeps all completed results

### Tabs

| Tab | Content |
|-----|---------|
| **Overview** | Company info, filing dates, logo |
| **News** | Recent earnings news summary |
| **Numbers** | Extracted financial metrics (revenue, EPS, margins) |
| **Goals** | Strategic goals and forward guidance |
| **Tone** | Management sentiment analysis |
| **Price** | Interactive OHLC chart with price analysis |
| **Comparison** | Side-by-side period comparison |
| **Report** | Full PDF report (embedded viewer) |
| **Bullets** | 5 bullish + 5 bearish key points |
| **Animation** | 15-second OHLC video with overlays |
| **Chat** | Ask questions about the analyzed company |
| **Logs** | Real-time pipeline execution log |

### Supported Companies

Works with any **US domestic filer** on SEC EDGAR (10-Q/10-K). Examples: `AAPL`, `MSFT`, `GOOGL`, `AMZN`, `META`, `NVDA`, `TSLA`, `JPM`, `COST`, `NFLX`.

> ⚠️ Foreign issuers (e.g. BIDU, BABA, TME) file 20-F/6-K instead of 10-Q/10-K and are not currently supported.

---

## 📁 Project Structure

```
ninja-stock-analyzer/
├── server.py              # HTTP server + pipeline manager + API endpoints
├── claude_wrapper.py      # Claude CLI subprocess integration
├── finance_mcp_client.py  # RapidAPI stock data client
├── config.py              # Configuration & colors
├── utils.py               # Shared utilities
├── main.py                # Standalone pipeline runner
├── run_analysis.py        # CLI batch runner
├── skills/                # 12 analysis skills (one per pipeline step)
├── static/                # Frontend (index.html, app.js, dashboard.css)
├── reports/               # Generated analysis data (gitignored)
├── setup.sh               # One-time dependency installer
├── start.sh               # App startup script
├── requirements.txt       # Python dependencies
└── .env.example           # Environment variable reference
```

---

## 🔑 Environment Variables

Credentials are auto-loaded from `/root/.claude/settings.json` by `start.sh`. You can override any of these in a `.env` file:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | from settings.json | Claude API authentication |
| `ANTHROPIC_BASE_URL` | from settings.json | Claude API endpoint |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model to use |
| `RAPIDAPI_KEY` | built-in | Stock price & news data |
| `SERVER_PORT` | `8090` | Dashboard port |

---

## 🙏 Credits

- **Claude AI** by Anthropic — Powers all analysis
- **SEC EDGAR** — Financial filing data source
- **Real-Time Finance Data API** — Stock price & news data
- **Python Arcade** — Animation rendering engine
