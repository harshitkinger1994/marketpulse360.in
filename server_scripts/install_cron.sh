#!/usr/bin/env bash
set -euo pipefail

# Update this to your server path
PROJECT_DIR="/path/to/market-context"

CLEANUP_SH="${PROJECT_DIR}/server_scripts/cleanup.sh"
START_SH="${PROJECT_DIR}/server_scripts/start_live.sh"
LOG_DIR="${PROJECT_DIR}/backend/logs"

CRON_BEGIN="# MARKET_CONTEXT_CRON_BEGIN"
CRON_END="# MARKET_CONTEXT_CRON_END"

if [[ ! -x "${CLEANUP_SH}" ]]; then
  echo "[CRON] cleanup.sh not executable: ${CLEANUP_SH}" >&2
  exit 1
fi

if [[ ! -x "${START_SH}" ]]; then
  echo "[CRON] start_live.sh not executable: ${START_SH}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"

tmp_file="$(mktemp)"
crontab -l 2>/dev/null | sed "/${CRON_BEGIN}/,/${CRON_END}/d" > "${tmp_file}"

{
  cat "${tmp_file}"
  echo "${CRON_BEGIN}"
  echo "@reboot ${START_SH}"
  echo "0 * * * * ${CLEANUP_SH} >> ${LOG_DIR}/cleanup.log 2>&1"
  echo "${CRON_END}"
} | crontab -

rm -f "${tmp_file}"
echo "[CRON] Installed market-context cron entries."
