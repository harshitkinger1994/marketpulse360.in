#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVER="${DEPLOY_SERVER:-root@143.110.185.245}"
DOMAIN="${DEPLOY_DOMAIN:-marketpulse360.in}"
REMOTE_DIR="${DEPLOY_REMOTE_DIR:-/opt/market-context}"
WEB_ROOT="${DEPLOY_WEB_ROOT:-/var/www/market-context}"
LIVE_PORT="${DEPLOY_LIVE_PORT:-8765}"
RELEASE_DIR="${DEPLOY_RELEASE_DIR:-${REMOTE_DIR}.release}"
BACKUP_ROOT="${DEPLOY_BACKUP_ROOT:-/opt/market-context-backups}"
CLEANUP_CALENDAR="${DEPLOY_CLEANUP_CALENDAR:-daily}"
MORNING_REEVAL_CALENDAR="${DEPLOY_MORNING_REEVAL_CALENDAR:-Mon..Fri *-*-* 04:00:00}"
DHAN_TOKEN_REFRESH_CALENDAR="${DEPLOY_DHAN_TOKEN_REFRESH_CALENDAR:-*-*-* 11:30:00 UTC}"
SSH_KEY="${DEPLOY_SSH_KEY:-${HOME}/.ssh/id_digitalocean}"
RSYNC_PROGRESS_FLAG=""
RSYNC_PROGRESS_NOTE=""

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required but not installed. Install it and retry."
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ssh is required but not installed. Install it and retry."
  exit 1
fi

if [[ ! -d "${SCRIPT_DIR}/backend" || ! -d "${SCRIPT_DIR}/frontend" ]]; then
  echo "Run this script from the market-context folder."
  exit 1
fi

if rsync --info=progress2 --version >/dev/null 2>&1; then
  RSYNC_PROGRESS_FLAG="--info=progress2"
  RSYNC_PROGRESS_NOTE="overall 0-100%"
else
  RSYNC_PROGRESS_FLAG="--progress"
  RSYNC_PROGRESS_NOTE="per-file 0-100%"
fi

echo "==> Syncing project to ${SERVER}:${RELEASE_DIR} (progress: ${RSYNC_PROGRESS_NOTE})"
ssh -i "${SSH_KEY}" -o IdentitiesOnly=yes "${SERVER}" "rm -rf '${RELEASE_DIR}' && mkdir -p '${RELEASE_DIR}'"
rsync -az --delete ${RSYNC_PROGRESS_FLAG} \
  --exclude ".git" \
  --exclude "venv" \
  --exclude "__pycache__" \
  --exclude ".DS_Store" \
  --exclude "market.db" \
  --exclude "backend/cache" \
  --exclude "backend/logs" \
  --exclude "backend/reports" \
  --exclude "backend/.env" \
  --exclude "frontend/data.json" \
  --exclude "strategies/.price_cache" \
  --exclude "strategies/history" \
  -e "ssh -i ${SSH_KEY} -o IdentitiesOnly=yes" \
  "${SCRIPT_DIR}/" "${SERVER}:${RELEASE_DIR}/"

echo "==> Running remote setup"
ssh -i "${SSH_KEY}" -o IdentitiesOnly=yes "${SERVER}" "DOMAIN='${DOMAIN}' WEB_ROOT='${WEB_ROOT}' REMOTE_DIR='${REMOTE_DIR}' RELEASE_DIR='${RELEASE_DIR}' BACKUP_ROOT='${BACKUP_ROOT}' LIVE_PORT='${LIVE_PORT}' CLEANUP_CALENDAR='${CLEANUP_CALENDAR}' MORNING_REEVAL_CALENDAR='${MORNING_REEVAL_CALENDAR}' DHAN_TOKEN_REFRESH_CALENDAR='${DHAN_TOKEN_REFRESH_CALENDAR}' bash -s" <<'EOF'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

BACKUP_TAG="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/${BACKUP_TAG}"

rollback() {
  echo "[ROLLBACK] Restoring previous state..."
  systemctl stop market-context-live market-context-updater >/dev/null 2>&1 || true
  systemctl stop market-context-cleanup.timer >/dev/null 2>&1 || true
  systemctl stop market-context-morning-reeval.timer >/dev/null 2>&1 || true
  systemctl stop market-context-dhan-token-refresh.timer >/dev/null 2>&1 || true
  if [ -d "${BACKUP_DIR}/market-context" ]; then
    rm -rf "${REMOTE_DIR}"
    mv "${BACKUP_DIR}/market-context" "${REMOTE_DIR}"
  fi
  if [ -d "${BACKUP_DIR}/web-root" ]; then
    rm -rf "${WEB_ROOT}"
    mv "${BACKUP_DIR}/web-root" "${WEB_ROOT}"
  fi
  if [ -f "${BACKUP_DIR}/nginx-market-context" ]; then
    cp -a "${BACKUP_DIR}/nginx-market-context" /etc/nginx/sites-available/market-context
  fi
  if [ -f "${BACKUP_DIR}/nginx-market-context-ip" ]; then
    cp -a "${BACKUP_DIR}/nginx-market-context-ip" /etc/nginx/sites-available/market-context-ip
  fi
  if [ -f "${BACKUP_DIR}/market-context-live.service" ]; then
    cp -a "${BACKUP_DIR}/market-context-live.service" /etc/systemd/system/market-context-live.service
  fi
  if [ -f "${BACKUP_DIR}/market-context-updater.service" ]; then
    cp -a "${BACKUP_DIR}/market-context-updater.service" /etc/systemd/system/market-context-updater.service
  fi
  if [ -f "${BACKUP_DIR}/market-context-cleanup.service" ]; then
    cp -a "${BACKUP_DIR}/market-context-cleanup.service" /etc/systemd/system/market-context-cleanup.service
  fi
  if [ -f "${BACKUP_DIR}/market-context-cleanup.timer" ]; then
    cp -a "${BACKUP_DIR}/market-context-cleanup.timer" /etc/systemd/system/market-context-cleanup.timer
  fi
  if [ -f "${BACKUP_DIR}/market-context-morning-reeval.service" ]; then
    cp -a "${BACKUP_DIR}/market-context-morning-reeval.service" /etc/systemd/system/market-context-morning-reeval.service
  fi
  if [ -f "${BACKUP_DIR}/market-context-morning-reeval.timer" ]; then
    cp -a "${BACKUP_DIR}/market-context-morning-reeval.timer" /etc/systemd/system/market-context-morning-reeval.timer
  fi
  if [ -f "${BACKUP_DIR}/market-context-dhan-token-refresh.service" ]; then
    cp -a "${BACKUP_DIR}/market-context-dhan-token-refresh.service" /etc/systemd/system/market-context-dhan-token-refresh.service
  fi
  if [ -f "${BACKUP_DIR}/market-context-dhan-token-refresh.timer" ]; then
    cp -a "${BACKUP_DIR}/market-context-dhan-token-refresh.timer" /etc/systemd/system/market-context-dhan-token-refresh.timer
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  systemctl restart market-context-live >/dev/null 2>&1 || true
  systemctl restart market-context-updater >/dev/null 2>&1 || true
  systemctl restart market-context-cleanup.timer >/dev/null 2>&1 || true
  systemctl restart market-context-morning-reeval.timer >/dev/null 2>&1 || true
  systemctl restart market-context-dhan-token-refresh.timer >/dev/null 2>&1 || true
  echo "[ROLLBACK] Completed."
}

trap 'echo "[ERROR] Deploy failed. Rolling back."; rollback; exit 1' ERR

mkdir -p "${BACKUP_DIR}"
if [ -f /etc/nginx/sites-available/market-context ]; then
  cp -a /etc/nginx/sites-available/market-context "${BACKUP_DIR}/nginx-market-context"
fi
if [ -f /etc/nginx/sites-available/market-context-ip ]; then
  cp -a /etc/nginx/sites-available/market-context-ip "${BACKUP_DIR}/nginx-market-context-ip"
fi
if [ -f /etc/systemd/system/market-context-live.service ]; then
  cp -a /etc/systemd/system/market-context-live.service "${BACKUP_DIR}/market-context-live.service"
fi
if [ -f /etc/systemd/system/market-context-updater.service ]; then
  cp -a /etc/systemd/system/market-context-updater.service "${BACKUP_DIR}/market-context-updater.service"
fi
if [ -f /etc/systemd/system/market-context-cleanup.service ]; then
  cp -a /etc/systemd/system/market-context-cleanup.service "${BACKUP_DIR}/market-context-cleanup.service"
fi
if [ -f /etc/systemd/system/market-context-cleanup.timer ]; then
  cp -a /etc/systemd/system/market-context-cleanup.timer "${BACKUP_DIR}/market-context-cleanup.timer"
fi
if [ -f /etc/systemd/system/market-context-morning-reeval.service ]; then
  cp -a /etc/systemd/system/market-context-morning-reeval.service "${BACKUP_DIR}/market-context-morning-reeval.service"
fi
if [ -f /etc/systemd/system/market-context-morning-reeval.timer ]; then
  cp -a /etc/systemd/system/market-context-morning-reeval.timer "${BACKUP_DIR}/market-context-morning-reeval.timer"
fi
if [ -f /etc/systemd/system/market-context-dhan-token-refresh.service ]; then
  cp -a /etc/systemd/system/market-context-dhan-token-refresh.service "${BACKUP_DIR}/market-context-dhan-token-refresh.service"
fi
if [ -f /etc/systemd/system/market-context-dhan-token-refresh.timer ]; then
  cp -a /etc/systemd/system/market-context-dhan-token-refresh.timer "${BACKUP_DIR}/market-context-dhan-token-refresh.timer"
fi

systemctl stop market-context-live market-context-updater >/dev/null 2>&1 || true
systemctl stop market-context-cleanup.timer >/dev/null 2>&1 || true
systemctl stop market-context-morning-reeval.timer >/dev/null 2>&1 || true
systemctl stop market-context-dhan-token-refresh.timer >/dev/null 2>&1 || true

if [ -d "${WEB_ROOT}" ]; then
  mv "${WEB_ROOT}" "${BACKUP_DIR}/web-root"
fi
if [ -d "${REMOTE_DIR}" ]; then
  mv "${REMOTE_DIR}" "${BACKUP_DIR}/market-context"
fi

	if [ ! -d "${RELEASE_DIR}" ]; then
	  echo "[ERROR] Release dir not found: ${RELEASE_DIR}"
	  exit 1
	fi
	mv "${RELEASE_DIR}" "${REMOTE_DIR}"

	# Preserve server-side secrets/config: keep the previous backend/.env if it existed.
	if [ -f "${BACKUP_DIR}/market-context/backend/.env" ]; then
	  cp -a "${BACKUP_DIR}/market-context/backend/.env" "${REMOTE_DIR}/backend/.env"
	  chmod 600 "${REMOTE_DIR}/backend/.env" || true
	fi

	# Keep site up immediately after deploy: preserve last known data.json so /data.json doesn't 404
	# while the first daily_run is still generating a fresh snapshot.
	if [ ! -f "${REMOTE_DIR}/frontend/data.json" ]; then
	  if [ -f "${BACKUP_DIR}/market-context/frontend/data.json" ]; then
	    cp -a "${BACKUP_DIR}/market-context/frontend/data.json" "${REMOTE_DIR}/frontend/data.json" || true
	    chmod 644 "${REMOTE_DIR}/frontend/data.json" || true
	  fi
	fi

	apt-get update
	apt-get install -y python3 python3-venv python3-pip nginx jq certbot python3-certbot-nginx curl

python3 -m venv "${REMOTE_DIR}/venv"

"${REMOTE_DIR}/venv/bin/pip" install --upgrade pip
"${REMOTE_DIR}/venv/bin/pip" install -r "${REMOTE_DIR}/requirements.txt"

rm -rf "${WEB_ROOT}"
mkdir -p "${WEB_ROOT}"
rsync -az --delete "${REMOTE_DIR}/frontend/" "${WEB_ROOT}/"
chown -R www-data:www-data "${WEB_ROOT}"
chmod -R 755 "${WEB_ROOT}"
ln -sf "${REMOTE_DIR}/frontend/data.json" "${WEB_ROOT}/data.json"
ln -sf "${REMOTE_DIR}/frontend/_yesterday_snapshot.json" "${WEB_ROOT}/_yesterday_snapshot.json"

chmod +x "${REMOTE_DIR}/server_scripts/start_live.sh" "${REMOTE_DIR}/server_scripts/cleanup.sh"
chmod +x "${REMOTE_DIR}/server_scripts/refresh_dhan_token.sh"

cat >/etc/nginx/sites-available/market-context <<NGINXCONF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};

    root ${WEB_ROOT};
    index index.html;

    location /live {
        proxy_pass http://127.0.0.1:${LIVE_PORT}/live;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /suggest {
        proxy_pass http://127.0.0.1:${LIVE_PORT}/suggest;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }

    location = /data.json {
        add_header Cache-Control "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0";
    }
}
NGINXCONF

cat >/etc/nginx/sites-available/market-context-ip <<NGINXCONF
server {
    listen 80 default_server;
    server_name _;

    root ${WEB_ROOT};
    index index.html;

    location /live {
        proxy_pass http://127.0.0.1:${LIVE_PORT}/live;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /suggest {
        proxy_pass http://127.0.0.1:${LIVE_PORT}/suggest;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }

    location = /data.json {
        add_header Cache-Control "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0";
    }
}
NGINXCONF

ln -sf /etc/nginx/sites-available/market-context /etc/nginx/sites-enabled/market-context
ln -sf /etc/nginx/sites-available/market-context-ip /etc/nginx/sites-enabled/market-context-ip
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

cat >/etc/systemd/system/market-context-live.service <<SERVICE
[Unit]
Description=Market Context Live API
After=network.target

[Service]
Type=simple
WorkingDirectory=${REMOTE_DIR}
EnvironmentFile=${REMOTE_DIR}/backend/.env
Environment=MARKET_CONTEXT_DIR=${REMOTE_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=LIVE_HOST=127.0.0.1
Environment=LIVE_PORT=${LIVE_PORT}
ExecStart=${REMOTE_DIR}/server_scripts/start_live.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

cat >/etc/systemd/system/market-context-cleanup.service <<SERVICE
[Unit]
Description=Market Context Cleanup
After=network.target

[Service]
Type=oneshot
WorkingDirectory=${REMOTE_DIR}
EnvironmentFile=${REMOTE_DIR}/backend/.env
Environment=MARKET_CONTEXT_DIR=${REMOTE_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${REMOTE_DIR}/server_scripts/cleanup.sh
SERVICE

cat >/etc/systemd/system/market-context-cleanup.timer <<TIMER
[Unit]
Description=Market Context Cleanup Timer

[Timer]
OnCalendar=${CLEANUP_CALENDAR}
Persistent=true

[Install]
WantedBy=timers.target
TIMER

cat >/etc/systemd/system/market-context-morning-reeval.service <<SERVICE
[Unit]
Description=Market Context Morning Re-evaluate India Trades
After=network.target

[Service]
Type=oneshot
WorkingDirectory=${REMOTE_DIR}
EnvironmentFile=${REMOTE_DIR}/backend/.env
Environment=MARKET_CONTEXT_DIR=${REMOTE_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${REMOTE_DIR}/server_scripts/morning_reeval_india.sh
SERVICE

cat >/etc/systemd/system/market-context-morning-reeval.timer <<TIMER
[Unit]
Description=Market Context Morning Re-evaluate India Trades Timer

[Timer]
OnCalendar=${MORNING_REEVAL_CALENDAR}
Persistent=true

[Install]
WantedBy=timers.target
TIMER

cat >/etc/systemd/system/market-context-dhan-token-refresh.service <<SERVICE
[Unit]
Description=Market Context Dhan Token Refresh
After=network.target

[Service]
Type=oneshot
WorkingDirectory=${REMOTE_DIR}
EnvironmentFile=${REMOTE_DIR}/backend/.env
Environment=MARKET_CONTEXT_DIR=${REMOTE_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${REMOTE_DIR}/server_scripts/refresh_dhan_token.sh
SERVICE

cat >/etc/systemd/system/market-context-dhan-token-refresh.timer <<TIMER
[Unit]
Description=Market Context Dhan Token Refresh Timer

[Timer]
OnCalendar=${DHAN_TOKEN_REFRESH_CALENDAR}
Persistent=true

[Install]
WantedBy=timers.target
TIMER

systemctl disable --now market-context-updater >/dev/null 2>&1 || true
rm -f /etc/systemd/system/market-context-updater.service
systemctl daemon-reload
systemctl enable --now market-context-live
systemctl enable --now market-context-cleanup.timer
systemctl enable --now market-context-morning-reeval.timer
systemctl enable --now market-context-dhan-token-refresh.timer
systemctl restart market-context-live
systemctl start market-context-cleanup.service || true

if [ ! -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
  certbot --nginx -d ${DOMAIN} -d www.${DOMAIN} \
    --agree-tos --register-unsafely-without-email --non-interactive --redirect
else
  certbot renew --quiet || true
fi
systemctl reload nginx

if ! ss -ltnp | grep -q ':443'; then
  echo "[WARN] Port 443 not listening. Re-applying certbot nginx config."
  certbot --nginx -d ${DOMAIN} -d www.${DOMAIN} \
    --agree-tos --register-unsafely-without-email --non-interactive --redirect --keep-until-expiring
  systemctl reload nginx
fi

echo "==> Listener check"
ss -ltnp | grep -E ':80|:443' || true
echo "==> Health check"
curl -I http://127.0.0.1 || true
curl -I https://${DOMAIN} || true

echo "==> Backup location: ${BACKUP_DIR}"

if [ -d "${BACKUP_ROOT}" ]; then
  ls -1dt "${BACKUP_ROOT}"/* 2>/dev/null | tail -n +4 | xargs -r rm -rf || true
fi

trap - ERR
EOF

echo "==> Final check (local)"
HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" "http://${DOMAIN}" || echo "000")"
HTTPS_STATUS="$(curl -s -o /dev/null -w "%{http_code}" "https://${DOMAIN}" || echo "000")"
IP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" "http://143.110.185.245" || echo "000")"
DATA_STATUS="$(curl -s -o /tmp/market-context-data.json -w "%{http_code}" "https://${DOMAIN}/data.json" || echo "000")"
DATA_VALID="no"
DATA_UPDATED="missing"
if [ "${DATA_STATUS}" = "200" ] && command -v jq >/dev/null 2>&1; then
  if jq -e '.generated_at and (.strategies | type == "array")' /tmp/market-context-data.json >/dev/null 2>&1; then
    DATA_VALID="yes"
    DATA_UPDATED="$(jq -r '.generated_at // "missing"' /tmp/market-context-data.json 2>/dev/null || echo "missing")"
  fi
fi
LIVE_SERVICE_STATUS="unknown"
if ssh -i "${SSH_KEY}" -o IdentitiesOnly=yes "${SERVER}" "systemctl is-active --quiet market-context-live"; then
  LIVE_SERVICE_STATUS="active"
else
  LIVE_SERVICE_STATUS="inactive"
fi

echo "==> OK summary"
echo "http://${DOMAIN} -> ${HTTP_STATUS}"
echo "https://${DOMAIN} -> ${HTTPS_STATUS}"
echo "http://143.110.185.245 -> ${IP_STATUS}"
echo "https://${DOMAIN}/data.json -> ${DATA_STATUS}"
echo "data.json valid -> ${DATA_VALID}"
echo "data.json generated_at -> ${DATA_UPDATED}"
echo "market-context-live service -> ${LIVE_SERVICE_STATUS}"
echo "==> Done"
