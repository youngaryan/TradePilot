#!/usr/bin/env bash
#
# dev.sh — launch the TradePilot local web view (backend API + React dashboard).
#
# Usage:  ./dev.sh
# Then open http://127.0.0.1:5173 (opened automatically on macOS).
# Press Ctrl+C to stop both servers.

set -euo pipefail

# Resolve the repo root regardless of where the script is called from.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

API_HOST="127.0.0.1"
API_PORT="8000"
WEB_PORT="5173"

# Make Homebrew tools (node/npm) available even in a minimal shell.
if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# --- sanity checks ------------------------------------------------------------
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "error: Python venv not found at .venv/. Create it with:"
  echo "  python3.11 -m venv .venv && .venv/bin/pip install -e \".[backend]\""
  exit 1
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "error: frontend deps not installed. Run:"
  echo "  (cd frontend && npm install)"
  exit 1
fi

# --- start servers ------------------------------------------------------------
PIDS=()
cleanup() {
  echo ""
  echo "Stopping servers..."
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

echo "Starting backend API on http://$API_HOST:$API_PORT ..."
.venv/bin/python -m uvicorn pairs_trading.backend.app:app \
  --host "$API_HOST" --port "$API_PORT" &
PIDS+=($!)

echo "Starting web view on http://$API_HOST:$WEB_PORT ..."
( cd frontend && npm run dev ) &
PIDS+=($!)

# --- wait for the API to be healthy (matplotlib font cache can be slow) -------
echo -n "Waiting for backend to be ready"
for _ in $(seq 1 60); do
  if curl -fsS "http://$API_HOST:$API_PORT/api/health" >/dev/null 2>&1; then
    echo " ok"
    break
  fi
  echo -n "."
  sleep 1
done

URL="http://$API_HOST:$WEB_PORT"
echo ""
echo "========================================================"
echo "  Web view:  $URL"
echo "  API docs:  http://$API_HOST:$API_PORT/docs"
echo "  Press Ctrl+C to stop both servers."
echo "========================================================"

# Open the dashboard in the default browser (macOS).
if command -v open >/dev/null 2>&1; then
  ( sleep 2 && open "$URL" ) &
fi

# Keep running until Ctrl+C; exit if either server dies.
wait -n
