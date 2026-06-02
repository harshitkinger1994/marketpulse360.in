#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${MARKET_CONTEXT_DIR:-/opt/market-context}"
VENV_PY="${ROOT_DIR}/venv/bin/python"
LOG_DIR="${ROOT_DIR}/backend/logs"
LOG_FILE="${LOG_DIR}/morning_reeval_india.log"

mkdir -p "${LOG_DIR}"

cd "${ROOT_DIR}"
exec "${VENV_PY}" "${ROOT_DIR}/backend/morning_reevaluate_india.py" >> "${LOG_FILE}" 2>&1

