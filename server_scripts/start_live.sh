#!/usr/bin/env bash
set -euo pipefail

# Update this to your server path (or set MARKET_CONTEXT_DIR)
PROJECT_DIR="${MARKET_CONTEXT_DIR:-/opt/market-context}"
PYTHON_BIN="${PROJECT_DIR}/venv/bin/python"

cd "${PROJECT_DIR}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[LIVE] venv python not found at ${PYTHON_BIN}" >&2
  exit 1
fi

# Optional: log output to a file
LOG_FILE="${PROJECT_DIR}/backend/logs/start_live.log"
mkdir -p "$(dirname "${LOG_FILE}")"

exec "${PYTHON_BIN}" backend/start_live.py >> "${LOG_FILE}" 2>&1
