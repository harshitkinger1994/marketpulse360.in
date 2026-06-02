#!/usr/bin/env bash
set -euo pipefail

# Update this to your server path (or set MARKET_CONTEXT_DIR)
PROJECT_DIR="${MARKET_CONTEXT_DIR:-/opt/market-context}"
PYTHON_BIN="${PROJECT_DIR}/venv/bin/python"

cd "${PROJECT_DIR}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[CLEANUP] venv python not found at ${PYTHON_BIN}" >&2
  exit 1
fi

"${PYTHON_BIN}" backend/cleanup.py
