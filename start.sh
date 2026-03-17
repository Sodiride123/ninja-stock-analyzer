#!/bin/bash
# ============================================================
# Earnings Research AI — Startup Script
# ============================================================
# Starts Xvfb (for headless animation rendering) and the server
# ============================================================

set -e

cd "$(dirname "$0")"

# Load .env file if it exists
if [ -f .env ]; then
    echo "📄 Loading environment from .env"
    export $(grep -v '^#' .env | xargs)
fi

# Pull credentials from Claude Code settings if not already set
CLAUDE_SETTINGS="/root/.claude/settings.json"
if [ -f "$CLAUDE_SETTINGS" ]; then
    export ANTHROPIC_API_KEY=$(python3 -c "import json; d=json.load(open('$CLAUDE_SETTINGS')); print(d['env']['ANTHROPIC_AUTH_TOKEN'])")
    export ANTHROPIC_BASE_URL=$(python3 -c "import json; d=json.load(open('$CLAUDE_SETTINGS')); print(d['env']['ANTHROPIC_BASE_URL'])")
    export ANTHROPIC_MODEL=claude-sonnet-4-6
fi

# Set RapidAPI key if not already set (used for stock price & news data)
if [ -z "$RAPIDAPI_KEY" ]; then
    export RAPIDAPI_KEY=$(python3 -c "from finance_mcp_client import MCP_CONFIG; print(MCP_CONFIG['rapidapi_key'])")
fi

# Check required environment variables
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "❌ ERROR: ANTHROPIC_API_KEY is not set!"
    echo "   Please set it in your .env file or environment."
    echo "   Get your key at: https://console.anthropic.com/"
    exit 1
fi

# Start Xvfb for headless OpenGL rendering (needed by Arcade)
if ! pgrep -x Xvfb > /dev/null; then
    echo "🖥️  Starting Xvfb virtual display..."
    Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
    export DISPLAY=:99
    sleep 1
    echo "   ✅ Xvfb running on DISPLAY=:99"
else
    echo "🖥️  Xvfb already running"
    export DISPLAY=:99
fi

# Create reports directory if needed
mkdir -p reports

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  🚀 Earnings Research AI"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  Dashboard:  http://localhost:${SERVER_PORT:-8090}"
echo "  API:        http://localhost:${SERVER_PORT:-8090}/api/status"
echo ""
echo "  Claude Model: ${ANTHROPIC_MODEL:-claude-sonnet-4-6}"
echo "  RapidAPI:     $([ -n "$RAPIDAPI_KEY" ] && echo '✅ Configured' || echo '⚠️  Not set (price analysis disabled)')"
echo ""
echo "══════════════════════════════════════════════════════════"
echo ""

# Start the server
exec python server.py