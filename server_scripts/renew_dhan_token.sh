#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${MARKET_CONTEXT_DIR:-/opt/market-context}"
VENV_PY="${ROOT_DIR}/venv/bin/python"
LOG_DIR="${ROOT_DIR}/backend/logs"
LOG_FILE="${LOG_DIR}/dhan_token_renew.log"

mkdir -p "${LOG_DIR}"

cd "${ROOT_DIR}"
"${VENV_PY}" "${ROOT_DIR}/backend/dhan_token_renew.py" \
  --env-file "${ROOT_DIR}/backend/.env" \
  --current-token "${DHAN_ACCESS_TOKEN:-}" \
  --client-id "${DHAN_CLIENT_ID:-}" >> "${LOG_FILE}" 2>&1
