#!/bin/bash
# ============================================================
# Run Script — Start the Earnings App
# All dependencies should already be installed via setup.sh
# ============================================================

set -e

cd "$(dirname "$0")"

# Load .env if exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY is not set."
    echo "Set it in .env or export it before running."
    exit 1
fi

# Start Xvfb for headless animation rendering
if ! pgrep -x Xvfb > /dev/null 2>&1; then
    Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
    sleep 1
fi
export DISPLAY=:99

mkdir -p reports

echo "Starting Earnings App on http://localhost:${SERVER_PORT:-8090}"
exec python3 server.py
