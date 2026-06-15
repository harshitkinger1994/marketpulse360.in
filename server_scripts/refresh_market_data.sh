#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${MARKET_CONTEXT_DIR:-/opt/market-context}"
VENV_PY="${ROOT_DIR}/venv/bin/python"
LOG_DIR="${ROOT_DIR}/backend/logs"
LOG_FILE="${LOG_DIR}/market_data_refresh.log"

mkdir -p "${LOG_DIR}"

IST_WEEKDAY="$(TZ=Asia/Kolkata date +%u)"
IST_HHMM="$(TZ=Asia/Kolkata date +%H%M)"

# Run only on weekdays between 09:15 and 15:30 IST.
if [[ "${IST_WEEKDAY}" -ge 6 || "${IST_HHMM}" -lt 0915 || "${IST_HHMM}" -gt 1530 ]]; then
  echo "[REFRESH] market closed; skipping dashboard refresh." >> "${LOG_FILE}"
  exit 0
fi

cd "${ROOT_DIR}"
DAILY_RUN_PUBLISH_ONLY=1 "${VENV_PY}" "${ROOT_DIR}/backend/daily_run.py" \
  >> "${LOG_FILE}" 2>&1
