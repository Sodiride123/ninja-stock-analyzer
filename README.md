# 📊 Earnings Research AI

An autonomous AI-powered quarterly earnings research application that downloads SEC filings, analyzes financial data, and generates comprehensive reports with animated visualizations.

![Dashboard](https://img.shields.io/badge/Dashboard-Live-10B981?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-AI-FF6B35?style=for-the-badge)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)

## ✨ Features

- **12-Step Analysis Pipeline** — Fully automated from ticker input to final report
- **SEC EDGAR Integration** — Downloads and parses 10-K/10-Q filings automatically
- **AI-Powered Analysis** — Claude extracts financial numbers, goals, tone, and comparisons
- **Real-Time Price Data** — OHLC stock price analysis via RapidAPI
- **PDF Report Generation** — Professional multi-page reports with cover page and logo
- **Animated OHLC Videos** — 15-second MP4 animations of stock price movement
- **Ten-Point Analysis** — 5 bullish + 5 bearish bullet points per company
- **Web Dashboard** — Real-time pipeline monitoring with 11 content tabs
- **Persistent Storage** — All analyses saved to disk, survives restarts
- **Multi-Company Support** — Analyze multiple companies, switch between them instantly

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
│            Earnings Pipeline (main.py)                    │
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

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/earnings-research-ai.git
cd earnings-research-ai

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Build and run
docker compose up -d

# Open dashboard
open http://localhost:8090
```

### Option 2: Local Installation

#### Prerequisites

- Python 3.11+
- Node.js 20+ (for Claude CLI)
- System packages: `wkhtmltopdf`, `poppler-utils`, `ffmpeg`, `xvfb`

#### Install

```bash
# Clone
git clone https://github.com/yourusername/earnings-research-ai.git
cd earnings-research-ai

# Install system dependencies (Debian/Ubuntu)
sudo apt-get install -y wkhtmltopdf poppler-utils ffmpeg xvfb \
    libgl1-mesa-glx libgl1-mesa-dri libegl1-mesa

# Install Claude CLI
npm install -g @anthropic-ai/claude-code

# Install Python dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Start the dashboard
python server.py
```

#### CLI Usage

```bash
# Analyze a single company
python run_analysis.py AAPL

# Analyze multiple companies
python run_analysis.py AAPL MSFT GOOG

# Auto-select (finds companies that reported today)
python run_analysis.py --auto
```

### Option 3: Deploy to Cloud

#### AWS EC2 / DigitalOcean Droplet

```bash
# SSH into your server
ssh user@your-server

# Install Docker
curl -fsSL https://get.docker.com | sh

# Clone and run
git clone https://github.com/yourusername/earnings-research-ai.git
cd earnings-research-ai
cp .env.example .env
nano .env  # Add your API keys

docker compose up -d

# (Optional) Set up reverse proxy with nginx for HTTPS
```

#### Railway / Render / Fly.io

These platforms support Docker deployments. Push your repo and configure:
- **Port**: 8090
- **Environment variables**: `ANTHROPIC_API_KEY`, `RAPIDAPI_KEY`
- **Persistent volume**: Mount at `/app/reports`

## 🔑 API Keys Required

| Key | Required | Purpose | Get it at |
|-----|----------|---------|-----------|
| `ANTHROPIC_API_KEY` | ✅ Yes | Claude AI for all analysis | [console.anthropic.com](https://console.anthropic.com/) |
| `RAPIDAPI_KEY` | ⬜ Optional | Real-time stock prices | [rapidapi.com](https://rapidapi.com/letscrape-6bRBa3QguO5/api/real-time-finance-data) |

## 📁 Project Structure

```
earnings_app/
├── server.py              # HTTP server + API endpoints
├── main.py                # Pipeline orchestrator
├── run_analysis.py        # CLI entry point
├── claude_wrapper.py      # Claude CLI integration
├── finance_mcp_client.py  # RapidAPI stock data client
├── config.py              # Configuration & colors
├── utils.py               # Shared utilities
├── skills/                # 12 analysis skills
│   ├── base.py            # Abstract base skill
│   ├── select_company.py  # Step 1: Validate ticker
│   ├── research_company.py# Step 2: News research
│   ├── get_reports.py     # Step 3: SEC EDGAR download
│   ├── get_numbers.py     # Step 4: Financial extraction
│   ├── extract_goals.py   # Step 5: Strategic goals
│   ├── analyze_tone.py    # Step 6: Sentiment analysis
│   ├── analyze_price.py   # Step 7: OHLC price analysis
│   ├── get_logo.py        # Step 8: Company logo
│   ├── compare_reports.py # Step 9: Cross-period comparison
│   ├── generate_report.py # Step 10: PDF report
│   ├── ten_point_analysis.py # Step 11: Bull/bear points
│   └── animate.py         # Step 12: Video animation
├── static/                # Frontend assets
│   ├── index.html         # Dashboard HTML
│   ├── css/dashboard.css  # Styles
│   └── js/app.js          # Dashboard JavaScript
├── reports/               # Generated analysis data
│   └── {TICKER}/          # Per-company output files
├── Dockerfile             # Container build
├── docker-compose.yml     # Container orchestration
├── requirements.txt       # Python dependencies
└── .env.example           # Environment template
```

## 🖥️ Dashboard Tabs

| Tab | Content |
|-----|---------|
| **Overview** | Company info, filing dates, logo |
| **News** | Top 5 recent earnings news stories |
| **Numbers** | Extracted financial metrics (revenue, EPS, margins) |
| **Goals** | Strategic goals and forward guidance |
| **Tone** | Management sentiment analysis |
| **📈 Price** | Interactive OHLC chart with price analysis |
| **Comparison** | Side-by-side period comparison |
| **Report** | Full PDF report (embedded viewer) |
| **🎯 Bullets** | 5 bullish + 5 bearish key points |
| **🎬 Animation** | 15-second OHLC video with overlays |
| **Logs** | Real-time pipeline execution log |

## 🛠️ Configuration

Edit `config.py` to customize:

```python
CLAUDE_MODEL = "claude-sonnet-4-6"  # AI model
CLAUDE_MAX_TOKENS = 8192                    # Max response length
SERVER_PORT = 8090                          # Dashboard port
MAX_REPORT_PAGES = 5                        # PDF page limit
PDF_PAGE_SIZE = "A4"                        # Page size
```

## 📝 License

MIT License — See [LICENSE](LICENSE) for details.

## 🙏 Credits

- **Claude AI** by Anthropic — Powers all analysis
- **SEC EDGAR** — Financial filing data source
- **Real-Time Finance Data API** — Stock price data
- **Python Arcade** — Animation rendering engine