import json
import os
import sys
import time
import math
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_env_file(path):
    if not path.exists():
        return
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


_load_env_file(ENV_PATH)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.suggest_security import (  # noqa: E402
    auto_block_until,
    client_ip_from_headers,
    parse_and_validate_payload,
    safe_telegram_text,
    rate_limit_state,
)
from backend.suggest_store import (  # noqa: E402
    get_block_status,
    get_recent_submission_times,
    init_suggest_tables,
    log_event,
    upsert_block,
)
from backend.auto_trader import load_latest_trade_report, load_recent_trade_orders  # noqa: E402
from backend.market_snapshot_store import load_latest_market_snapshot_payload  # noqa: E402

from backend.data_fetcher import (
    SYMBOLS,
    GLOBAL_STOCKS,
    CRYPTO,
    get_nifty50_symbols,
    fetch_live_snapshot,
)
from backend.database import get_conn
from backend.exchange_universes import build_exchange_universe_manifest, build_exchange_symbol_map


HOST = os.environ.get("LIVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("LIVE_PORT", "8765"))
CACHE_TTL_SEC = int(os.environ.get("LIVE_CACHE_TTL_SEC", "20"))
MAX_WORKERS = int(os.environ.get("LIVE_MAX_WORKERS", "6"))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_SUGGEST_CHAT_ID = (
    os.environ.get("TELEGRAM_SUGGEST_CHAT_ID")
    or os.environ.get("TELEGRAM_STATUS_CHAT_ID")
    or os.environ.get("TELEGRAM_PERSONAL_CHAT_ID")
    or TELEGRAM_CHAT_ID
)
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "").strip()
TURNSTILE_REQUIRED = os.environ.get("TURNSTILE_REQUIRED", "0").strip() == "1"

_CACHE = {}
_LAST_CLOSE_CACHE = {}
_LAST_CLOSE_TTL_SEC = int(os.environ.get("LIVE_LAST_CLOSE_TTL_SEC", "300"))
_EXCHANGE_UNIVERSE_MANIFEST = None
_NIFTY50_SYMBOLS = get_nifty50_symbols()
_LIVE_SYMBOLS = {
    **SYMBOLS,
    **GLOBAL_STOCKS,
    **_NIFTY50_SYMBOLS,
    **CRYPTO,
}

_LIVE_INDIA_INDEX_KEYS = {"NIFTY", "BANKNIFTY", "SENSEX"}
_LIVE_GLOBAL_INDEX_KEYS = {"SP500", "NASDAQ", "DAX", "NIKKEI", "HANGSENG"}


def _trend_from_live_price(name, live_price):
    last_close = _get_last_close(name)
    if live_price is None or last_close is None:
        return None
    try:
        live_price = float(live_price)
        last_close = float(last_close)
    except Exception:
        return None
    if not math.isfinite(live_price) or not math.isfinite(last_close) or last_close <= 0:
        return None
    if live_price > last_close:
        return "PRIMARY_UPTREND"
    if live_price < last_close:
        return "PRIMARY_DOWNTREND"
    return "RANGE"


def _build_live_summary(prices):
    if not isinstance(prices, dict) or not prices:
        return None

    trends = {}
    for key, payload in prices.items():
        trend = _trend_from_live_price(key, payload.get("price"))
        if trend:
            trends[key] = trend

    india_index_trends = {k: trends.get(k) for k in _LIVE_INDIA_INDEX_KEYS}
    india_up = sum(1 for v in india_index_trends.values() if v == "PRIMARY_UPTREND")
    india_down = sum(1 for v in india_index_trends.values() if v == "PRIMARY_DOWNTREND")
    india_total = sum(1 for v in india_index_trends.values() if v in {"PRIMARY_UPTREND", "PRIMARY_DOWNTREND", "RANGE"})
    india_breadth_pct = round((india_up / india_total) * 100, 1) if india_total else 0.0
    vix = prices.get("INDIA_VIX", {}).get("price")
    if vix is None:
        vix = _get_last_close("INDIA_VIX")

    india_score = 50 + ((india_up - india_down) * 10)
    if india_breadth_pct >= 60:
        india_score += 8
    elif india_breadth_pct <= 40:
        india_score -= 8
    if vix is not None:
        try:
            vix_val = float(vix)
            if vix_val < 14:
                india_score += 5
            elif vix_val > 18:
                india_score -= 5
        except Exception:
            pass
    india_score = int(max(0, min(100, round(india_score))))
    if india_score >= 65:
        india_status = "RISK-ON"
    elif india_score <= 40:
        india_status = "RISK-OFF"
    else:
        india_status = "NEUTRAL"

    global_index_trends = {k: trends.get(k) for k in _LIVE_GLOBAL_INDEX_KEYS}
    global_up = sum(1 for v in global_index_trends.values() if v == "PRIMARY_UPTREND")
    global_down = sum(1 for v in global_index_trends.values() if v == "PRIMARY_DOWNTREND")
    global_total = sum(1 for v in global_index_trends.values() if v in {"PRIMARY_UPTREND", "PRIMARY_DOWNTREND", "RANGE"})
    global_score = 50 + ((global_up - global_down) * 10)
    if global_score >= 65:
        global_status = "RISK-ON"
    elif global_score <= 40:
        global_status = "RISK-OFF"
    else:
        global_status = "NEUTRAL"

    breadth = {
        "up_pct": india_breadth_pct,
        "down_pct": round(100 - india_breadth_pct, 1) if india_total else 0.0,
        "sideways_pct": 0.0,
        "source": "live",
        "universe": "live_session",
        "symbols": len([k for k in prices.keys() if k not in {"INDIA_VIX"}]),
        "priced": len(trends),
    }

    market_health = {
        "india": {
            "score": india_score,
            "status": india_status,
            "notes": [
                f"{india_up}/{india_total or 3} live India indices in uptrend; Dow NOT CONFIRMED.",
                f"Live breadth {breadth['up_pct']}% up; VIX {round(float(vix), 2) if isinstance(vix, (int, float)) or str(vix).replace('.', '', 1).isdigit() else 'n/a'}."
            ],
            "opportunity": "Live session trend. Trade the strongest leaders and avoid stale mean-reversion assumptions.",
            "breadth_source": "live",
        },
        "global": {
            "score": int(max(0, min(100, round(global_score)))),
            "status": global_status,
            "notes": [
                f"{global_up}/{global_total or 5} live global indices in uptrend.",
                "Global breadth measured from the live endpoint snapshot."
            ],
        },
    }

    executive_summary = [
        f"India: {india_status} | Global: {global_status} | Dow: NOT CONFIRMED",
        f"Breadth: {breadth['up_pct']}% up / {breadth['down_pct']}% down | VIX: {round(float(vix), 1) if isinstance(vix, (int, float)) or str(vix).replace('.', '', 1).isdigit() else 'n/a'} | Live session. Action: Trade the live leaders",
    ]

    return {
        "breadth": breadth,
        "market_health": market_health,
        "executive_summary": executive_summary,
        "trends": trends,
    }


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_exchange_universe_manifest():
    global _EXCHANGE_UNIVERSE_MANIFEST
    if _EXCHANGE_UNIVERSE_MANIFEST is None:
        _EXCHANGE_UNIVERSE_MANIFEST = build_exchange_universe_manifest()
    return _EXCHANGE_UNIVERSE_MANIFEST


def _resolve_symbol(key):
    symbol = _LIVE_SYMBOLS.get(key)
    if symbol:
        return symbol
    raw = str(key or "").strip()
    if not raw or len(raw) > 32:
        return None
    if all(ch.isalnum() or ch in "^.-=/_&" for ch in raw):
        return raw
    return None


def _fetch_symbol(key):
    symbol = _resolve_symbol(key)
    if not symbol:
        return None
    snapshot = fetch_live_snapshot(symbol, name=key)
    if not snapshot or snapshot.get("price") is None:
        return None
    price = snapshot.get("price")
    day_range = snapshot.get("day_range")
    if isinstance(day_range, dict):
        day_range = dict(day_range)
        day_range["source"] = "LIVE"
        day_range["basis"] = "intraday"
    return {
        "price": round(price, 2),
        "timestamp": snapshot.get("timestamp") or _utc_now_iso(),
        "day_range": day_range,
    }


def _get_last_close(name):
    now = time.time()
    cached = _LAST_CLOSE_CACHE.get(name)
    if cached and now - cached["fetched_at"] < _LAST_CLOSE_TTL_SEC:
        return cached["close"]
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM prices WHERE index_name=? ORDER BY date DESC LIMIT 1",
            (name,),
        )
        row = cur.fetchone()
        conn.close()
        close = float(row[0]) if row and row[0] is not None else None
    except Exception:
        close = None
    _LAST_CLOSE_CACHE[name] = {"close": close, "fetched_at": now}
    return close


def _live_move_threshold(name):
    if "VIX" in (name or "").upper():
        return 1.0
    if name in {"NIFTY", "BANKNIFTY", "SENSEX"}:
        return 0.15
    if name in {"GOLD", "SILVER"}:
        return 0.3
    if name in CRYPTO:
        return 0.6
    return 0.25


def _validate_live_price(name, live_price):
    if live_price is None:
        return None
    if not isinstance(live_price, (int, float)) or not math.isfinite(live_price):
        return None
    if live_price <= 0:
        return None
    last_close = _get_last_close(name)
    if last_close is None or not math.isfinite(last_close) or last_close <= 0:
        return live_price
    delta = abs(live_price - last_close) / last_close
    if delta > _live_move_threshold(name):
        return None
    return live_price


def _get_cached_or_fetch(key):
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached["fetched_at"] < CACHE_TTL_SEC:
        return key, {
            "price": cached["price"],
            "timestamp": cached["timestamp"],
            "day_range": cached.get("day_range"),
        }
    data = _fetch_symbol(key)
    if data:
        valid_price = _validate_live_price(key, data.get("price"))
        if valid_price is None:
            return key, None
        data["price"] = round(valid_price, 2)
        day_range = data.get("day_range")
        if isinstance(day_range, dict):
            day_range = dict(day_range)
            day_range["current"] = data["price"]
            day_range["source"] = "LIVE"
            day_range["basis"] = "intraday"
            high = day_range.get("high")
            low = day_range.get("low")
            if isinstance(high, (int, float)):
                day_range["high"] = max(high, data["price"])
            if isinstance(low, (int, float)):
                day_range["low"] = min(low, data["price"])
            data["day_range"] = day_range
    if data:
        _CACHE[key] = {
            "price": data["price"],
            "timestamp": data["timestamp"],
            "day_range": data.get("day_range"),
            "fetched_at": now,
        }
        return key, data
    return key, None


class LiveHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _send_telegram(self, email, message):
        if not TELEGRAM_TOKEN or not TELEGRAM_SUGGEST_CHAT_ID:
            return False, "telegram_not_configured"
        text = safe_telegram_text(f"Suggestion Box\nEmail: {email}\nMessage: {message}")
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_SUGGEST_CHAT_ID, "text": text},
                timeout=10,
            )
            if resp.status_code != 200:
                return False, f"telegram_error_{resp.status_code}"
        except Exception:
            return False, "telegram_exception"
        return True, None

    def _verify_turnstile(self, token, remote_ip):
        if not TURNSTILE_SECRET:
            return (not TURNSTILE_REQUIRED), "turnstile_not_configured"
        if not token:
            return False, "turnstile_missing"
        try:
            resp = requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": TURNSTILE_SECRET, "response": token, "remoteip": remote_ip or ""},
                timeout=8,
            )
            if resp.status_code != 200:
                return False, f"turnstile_http_{resp.status_code}"
            data = resp.json() if resp.content else {}
            ok = bool(data.get("success"))
            return ok, None if ok else "turnstile_failed"
        except Exception:
            return False, "turnstile_exception"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, {"status": "ok", "timestamp": _utc_now_iso()})
            return
        if parsed.path == "/universe":
            self._send(200, _get_exchange_universe_manifest())
            return
        if parsed.path == "/trading":
            params = parse_qs(parsed.query)
            try:
                limit = int((params.get("limit") or ["25"])[0])
            except Exception:
                limit = 25
            self._send(
                200,
                {
                    "timestamp": _utc_now_iso(),
                    "report": load_latest_trade_report(),
                    "recent_orders": load_recent_trade_orders(limit=limit),
                },
            )
            return
        if parsed.path == "/snapshot":
            params = parse_qs(parsed.query)
            timeframe = (params.get("timeframe") or ["dashboard"])[0]
            payload = load_latest_market_snapshot_payload(
                timeframe_preference=(timeframe,),
            )
            if not payload:
                self._send(404, {"error": "snapshot_not_found", "timeframe": timeframe})
                return
            self._send(200, payload)
            return
        if parsed.path != "/live":
            self._send(404, {"error": "not_found"})
            return

        params = parse_qs(parsed.query)
        raw_symbols = params.get("symbols", [])
        if raw_symbols:
            requested = []
            for part in ",".join(raw_symbols).split(","):
                key = part.strip()
                if key:
                    requested.append(key)
            keys = requested or list(_LIVE_SYMBOLS.keys())
        else:
            keys = list(_LIVE_SYMBOLS.keys())

        keys = [k for k in keys if _resolve_symbol(k)]
        prices = {}
        if keys:
            max_workers = min(MAX_WORKERS, len(keys))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_get_cached_or_fetch, key) for key in keys]
                for future in as_completed(futures):
                    try:
                        key, data = future.result()
                    except Exception:
                        continue
                    if data:
                        prices[key] = data

        summary = _build_live_summary(prices)
        payload = {"timestamp": _utc_now_iso(), "prices": prices}
        if summary:
            payload["summary"] = summary
        self._send(200, payload)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/suggest":
            self._send(404, {"error": "not_found"})
            return
        ip = client_ip_from_headers(self.headers, fallback_ip=(self.client_address[0] if self.client_address else ""))
        user_agent = str(self.headers.get("User-Agent") or "").strip()[:255]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except Exception:
            length = 0
        if length <= 0:
            try:
                conn = get_conn()
                init_suggest_tables(conn)
                log_event(
                    conn,
                    ip=ip,
                    user_agent=user_agent,
                    name="",
                    email="",
                    message="",
                    page="",
                    result="rejected",
                    reason="empty_body",
                    turnstile_ok=None,
                )
                conn.close()
            except Exception:
                pass
            self._send(400, {"error": "empty_body"})
            return
        try:
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body)
        except Exception:
            try:
                conn = get_conn()
                init_suggest_tables(conn)
                log_event(
                    conn,
                    ip=ip,
                    user_agent=user_agent,
                    name="",
                    email="",
                    message="",
                    page="",
                    result="rejected",
                    reason="invalid_json",
                    turnstile_ok=None,
                )
                conn.close()
            except Exception:
                pass
            self._send(400, {"error": "invalid_json"})
            return
        suggestion, err = parse_and_validate_payload(payload if isinstance(payload, dict) else {})
        name = suggestion.name if suggestion else ""
        email = suggestion.email if suggestion else ""
        message = suggestion.message if suggestion else ""
        page = suggestion.page if suggestion else ""

        now_ts = time.time()
        conn = None
        try:
            conn = get_conn()
            init_suggest_tables(conn)

            blocked, until = get_block_status(conn, ip, now_ts=now_ts)
            if blocked:
                log_event(
                    conn,
                    ip=ip,
                    user_agent=user_agent,
                    name=name,
                    email=email,
                    message=message,
                    page=page,
                    result="rejected",
                    reason="ip_blocked",
                    turnstile_ok=None,
                )
                self._send(429, {"error": "ip_blocked", "blocked_until": until})
                return

            recent = get_recent_submission_times(conn, ip, now_ts=now_ts)
            allowed, rl_reason = rate_limit_state(now_ts, recent)
            if not allowed:
                if rl_reason == "auto_block_threshold":
                    until_dt = auto_block_until(now_ts)
                    upsert_block(conn, ip, blocked_until_utc=until_dt.isoformat(), reason="auto_block_20_per_hour")
                log_event(
                    conn,
                    ip=ip,
                    user_agent=user_agent,
                    name=name,
                    email=email,
                    message=message,
                    page=page,
                    result="rejected",
                    reason=rl_reason,
                    turnstile_ok=None,
                )
                self._send(429, {"error": rl_reason})
                return
        except Exception:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            self._send(500, {"error": "server_error"})
            return

        if err:
            try:
                log_event(
                    conn,
                    ip=ip,
                    user_agent=user_agent,
                    name=name,
                    email=email,
                    message=message,
                    page=page,
                    result="rejected",
                    reason=err,
                    turnstile_ok=None,
                )
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            code = 403 if err in {"blocked_payload", "honeypot_triggered"} else 400
            self._send(code, {"error": err})
            return

        turn_ok, turn_reason = self._verify_turnstile(suggestion.turnstile_token, ip)
        if not turn_ok:
            try:
                log_event(
                    conn,
                    ip=ip,
                    user_agent=user_agent,
                    name=suggestion.name,
                    email=suggestion.email,
                    message=suggestion.message,
                    page=suggestion.page,
                    result="rejected",
                    reason=turn_reason,
                    turnstile_ok=False,
                )
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            self._send(403, {"error": turn_reason})
            return

        ok, reason = self._send_telegram(suggestion.email, suggestion.message)
        if not ok:
            try:
                log_event(
                    conn,
                    ip=ip,
                    user_agent=user_agent,
                    name=suggestion.name,
                    email=suggestion.email,
                    message=suggestion.message,
                    page=suggestion.page,
                    result="rejected",
                    reason=reason,
                    turnstile_ok=True,
                )
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            self._send(500, {"error": reason})
            return

        try:
            log_event(
                conn,
                ip=ip,
                user_agent=user_agent,
                name=suggestion.name,
                email=suggestion.email,
                message=suggestion.message,
                page=suggestion.page,
                result="accepted",
                reason=None,
                turnstile_ok=True,
            )
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        self._send(200, {"status": "sent"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), LiveHandler)
    server.daemon_threads = True
    print(f"[LIVE] Serving on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
