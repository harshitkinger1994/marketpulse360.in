from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import get_conn, init_db
from backend.suggest_security import safe_telegram_text


STRATEGY_DIR = ROOT / "strategies"
FRONTEND_DATA_PATH = ROOT / "frontend" / "data.json"
REPORT_DIR = ROOT / "backend" / "reports" / "trading"
REPORT_PATH = REPORT_DIR / "latest_trade_plan.json"

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")

TRADING_ENABLED = os.environ.get("TRADING_ENABLED", "1").strip() == "1"
TRADING_MODE = os.environ.get("TRADING_MODE", "paper").strip().lower() or "paper"
TRADING_MARKETS = {
    part.strip().lower()
    for part in os.environ.get("TRADING_MARKETS", "india,global,commodities,crypto").split(",")
    if part.strip()
}
TRADING_MAX_ORDERS = int(os.environ.get("TRADING_MAX_ORDERS", "3"))
TRADING_MIN_RR = float(os.environ.get("TRADING_MIN_RR", "2.0"))
TRADING_RISK_PCT = float(os.environ.get("TRADING_RISK_PCT", "0.5"))
TRADING_CAPITAL = float(os.environ.get("TRADING_CAPITAL", "100000"))
TRADING_MAX_POSITION_PCT = float(os.environ.get("TRADING_MAX_POSITION_PCT", "25"))
TRADING_MAX_ENTRY_GAP_PCT = float(os.environ.get("TRADING_MAX_ENTRY_GAP_PCT", "1.5"))
TRADING_MARKET_HOURS_ONLY = os.environ.get("TRADING_MARKET_HOURS_ONLY", "1").strip() == "1"
TRADING_RETRY_BACKOFF_MINUTES = int(os.environ.get("TRADING_RETRY_BACKOFF_MINUTES", "30"))
TRADING_REQUIRE_CURRENT_PRICE = os.environ.get("TRADING_REQUIRE_CURRENT_PRICE", "1").strip() == "1"
TRADING_NOTIFY_TELEGRAM = os.environ.get("TRADING_NOTIFY_TELEGRAM", "1").strip() == "1"
TRADING_WEBHOOK_URL = os.environ.get("BROKER_WEBHOOK_URL", "").strip()
TRADING_WEBHOOK_TOKEN = os.environ.get("BROKER_WEBHOOK_TOKEN", "").strip()
TRADING_WEBHOOK_TIMEOUT = int(os.environ.get("BROKER_WEBHOOK_TIMEOUT_SEC", "12"))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (
    os.environ.get("TELEGRAM_TRADE_CHAT_ID")
    or os.environ.get("TELEGRAM_CHAT_ID")
    or ""
).strip()

PLACED_STATUSES = {"PAPER_PLACED", "SUBMITTED", "ACKED", "PLACED"}
FAILED_STATUSES = {"FAILED", "REJECTED"}


def _utc_now():
    return datetime.now(timezone.utc)


def _utc_iso(dt=None):
    dt = dt or _utc_now()
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return _utc_now().isoformat()


def _load_json_file(path: Path):
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_json_atomic(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
            tmp_path = Path(f.name)
        os.chmod(tmp_path, 0o644)
        tmp_path.replace(path)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _normalize_key(value):
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text)


def _to_float(value):
    try:
        if value in (None, ""):
            return None
        num = float(value)
        if math.isfinite(num):
            return num
    except Exception:
        return None
    return None


def _parse_dt(value, market_hint=None):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        market = str(market_hint or "").strip().lower()
        if market == "global":
            dt = dt.replace(tzinfo=ET)
        else:
            dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


def _market_from_symbol(symbol):
    s = str(symbol or "").strip().upper()
    if not s:
        return "india"
    if s.endswith((".NS", ".BO")):
        return "india"
    if s.endswith("-USD"):
        return "crypto"
    if s in {"GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F", "PL=F"}:
        return "commodities"
    return "global"


def _normalize_market(value, symbol=None):
    market = str(value or "").strip().lower()
    if market in {"india", "global", "crypto", "commodities"}:
        return market
    if market in {"all", "mixed", ""}:
        return _market_from_symbol(symbol)
    return _market_from_symbol(symbol)


def _market_timezone(market):
    market = str(market or "").strip().lower()
    if market == "global":
        return ET
    return IST


def _is_market_open(market, now=None):
    market = str(market or "").strip().lower()
    if market in {"crypto", "commodities"}:
        return True

    now = now or _utc_now()
    local_now = now.astimezone(_market_timezone(market))
    if local_now.weekday() >= 5:
        return False

    if market == "global":
        open_time = local_now.replace(hour=9, minute=30, second=0, microsecond=0)
        close_time = local_now.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        open_time = local_now.replace(hour=9, minute=15, second=0, microsecond=0)
        close_time = local_now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_time <= local_now <= close_time


def _price_from_blob(blob):
    if isinstance(blob, dict):
        for key in (
            "current_price",
            "currentPrice",
            "cmp",
            "price",
            "close",
            "lastPrice",
        ):
            val = _to_float(blob.get(key))
            if val is not None:
                return val
        nested = blob.get("live_data")
        if isinstance(nested, dict):
            val = _price_from_blob(nested)
            if val is not None:
                return val
    return None


def load_current_prices(snapshot_path: Path | None = None):
    path = snapshot_path or FRONTEND_DATA_PATH
    payload = _load_json_file(path)
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    prices = {}
    for key, blob in data.items():
        price = _price_from_blob(blob)
        if price is None:
            continue
        prices[_normalize_key(key)] = {
            "price": price,
            "source_key": key,
            "timestamp": (
                (blob or {}).get("price_timestamp")
                or (blob or {}).get("last_updated")
                or (blob or {}).get("timestamp")
            ),
        }
    return prices


def _lookup_current_price(signal, prices):
    if not prices:
        return None
    candidates = [
        signal.get("symbol"),
        signal.get("ticker"),
        signal.get("name"),
    ]
    symbol = str(signal.get("symbol") or "").strip().upper()
    if symbol.endswith(".NS"):
        candidates.append(symbol[:-3])
    if symbol.endswith(".BO"):
        candidates.append(symbol[:-3])
    if symbol.startswith("^"):
        candidates.append(symbol[1:])
    if symbol.endswith("-USD"):
        candidates.append(symbol[:-4])
    for candidate in candidates:
        key = _normalize_key(candidate)
        if key and key in prices:
            return prices[key]["price"]
    return None


def _build_signal_uid(strategy_id, item, entry_time, entry_price, stop_price, target_price):
    base = "|".join(
        [
            str(strategy_id or "").strip(),
            str(item.get("ticker") or "").strip().upper(),
            str(item.get("side") or "").strip().upper(),
            _utc_iso(entry_time) if isinstance(entry_time, datetime) else str(item.get("entry_time") or ""),
            f"{entry_price:.4f}" if isinstance(entry_price, (int, float)) else "",
            f"{stop_price:.4f}" if isinstance(stop_price, (int, float)) else "",
            f"{target_price:.4f}" if isinstance(target_price, (int, float)) else "",
            str(item.get("notify_key") or "").strip(),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _signal_score(signal, now=None):
    now = now or _utc_now()
    entry_dt = signal.get("entry_dt") or signal.get("signal_dt") or now
    if not isinstance(entry_dt, datetime):
        entry_dt = now
    age_hours = max((now - entry_dt).total_seconds() / 3600.0, 0.0)
    rr = _to_float(signal.get("rr_ratio")) or 0.0
    vol = _to_float(signal.get("vol_mult")) or 0.0
    market_bonus = 2.5 if signal.get("market") in {"india", "global"} else 1.0
    freshness_bonus = max(0.0, 48.0 - age_hours)
    return round((rr * 100.0) + (vol * 8.0) + freshness_bonus + market_bonus, 4)


def _signal_price_alignment(signal, current_price, max_entry_gap_pct):
    if current_price is None:
        return True, None
    entry = _to_float(signal.get("entry_price"))
    stop = _to_float(signal.get("stop_price"))
    target = _to_float(signal.get("target_price"))
    if entry is None or stop is None or target is None:
        return True, None

    if signal["side"] == "BUY":
        if current_price <= stop:
            return False, "price_below_stop"
        if current_price >= target:
            return False, "price_at_or_above_target"
    else:
        if current_price >= stop:
            return False, "price_above_stop"
        if current_price <= target:
            return False, "price_at_or_below_target"

    gap_pct = abs(current_price - entry) / max(entry, 1e-9) * 100.0
    if gap_pct > max_entry_gap_pct:
        return False, "entry_gap_too_wide"
    return True, None


def _activation_dt(signal):
    return signal.get("entry_dt") or signal.get("signal_dt") or signal.get("generated_dt")


def _default_max_age_hours(signal):
    trade_type = str(signal.get("trade_type") or "").strip().upper()
    if trade_type == "INTRADAY":
        return 18.0
    return 720.0


def _signal_is_eligible(signal, now=None, markets=None, max_age_hours=None, market_hours_only=None):
    now = now or _utc_now()
    market = signal.get("market")
    if markets and market not in markets:
        return False, "market_filtered"

    if signal.get("side") not in {"BUY", "SELL"}:
        return False, "invalid_side"

    entry = _to_float(signal.get("entry_price"))
    stop = _to_float(signal.get("stop_price"))
    target = _to_float(signal.get("target_price"))
    if not all(v is not None and v > 0 for v in (entry, stop, target)):
        return False, "missing_prices"

    if signal["side"] == "BUY" and not (target > entry > stop):
        return False, "invalid_buy_structure"
    if signal["side"] == "SELL" and not (target < entry < stop):
        return False, "invalid_sell_structure"

    rr = _to_float(signal.get("rr_ratio"))
    if rr is None:
        rr = abs(target - entry) / max(abs(entry - stop), 1e-9)
        signal["rr_ratio"] = round(rr, 4)
    if rr < TRADING_MIN_RR:
        return False, "rr_below_threshold"

    activation_dt = _activation_dt(signal)
    if activation_dt is None:
        return False, "missing_activation_time"
    if activation_dt > now:
        return False, "not_due_yet"

    age_hours = max((now - activation_dt).total_seconds() / 3600.0, 0.0)
    limit_hours = float(max_age_hours or _default_max_age_hours(signal))
    if age_hours > limit_hours:
        return False, "signal_too_old"

    if (market_hours_only if market_hours_only is not None else TRADING_MARKET_HOURS_ONLY) and not _is_market_open(market, now=now):
        return False, "market_closed"

    return True, None


def discover_trade_signals(strategy_dir: Path | None = None):
    base_dir = strategy_dir or STRATEGY_DIR
    signals = []
    for path in sorted(base_dir.glob("*.json")):
        name = path.name.lower()
        if name.startswith("."):
            continue
        if path.name == "top_trades.json":
            continue
        payload = _load_json_file(path)
        if not isinstance(payload, dict):
            continue
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            continue
        strategy_id = str(payload.get("strategy_id") or path.stem).strip()
        strategy_title = str(payload.get("title") or payload.get("strategy_name") or strategy_id).strip()
        market = _normalize_market(payload.get("market"), None)
        trade_type = str(payload.get("trade_type") or "SWING").strip().upper()
        generated_dt = _parse_dt(payload.get("generated_at"), market_hint=market)
        if generated_dt is None:
            generated_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        for item in items:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or item.get("symbol") or item.get("name") or "").strip().upper()
            symbol = str(item.get("symbol") or "").strip().upper()
            if not ticker and not symbol:
                continue
            side = str(item.get("side") or item.get("signal") or "").strip().upper()
            lines = item.get("lines") or []
            if side not in {"BUY", "SELL"} and isinstance(lines, list) and lines:
                first_line = str(lines[0]).upper()
                if "BUY" in first_line:
                    side = "BUY"
                elif "SELL" in first_line:
                    side = "SELL"
            if side not in {"BUY", "SELL"}:
                continue

            signal_dt = _parse_dt(item.get("signal_time") or item.get("signal") or item.get("date"), market_hint=market) or generated_dt
            entry_dt = _parse_dt(item.get("entry_time") or item.get("entry_date"), market_hint=market) or signal_dt
            entry_price = _to_float(item.get("entry_price") or item.get("entryPx") or item.get("entry"))
            stop_price = _to_float(item.get("stop_price") or item.get("stop_loss_price") or item.get("sl"))
            target_price = _to_float(item.get("target_price") or item.get("target") or item.get("target_1_price"))
            rr_ratio = _to_float(item.get("rr_ratio") or item.get("risk_reward_ratio"))
            vol_mult = _to_float(item.get("vol_mult") or item.get("volume_multiple") or item.get("volume_vs_avg"))
            name = str(item.get("name") or ticker or symbol).strip()
            if market == "all":
                market = _normalize_market(None, symbol or ticker)
            if rr_ratio is None and None not in (entry_price, stop_price, target_price):
                rr_ratio = abs(target_price - entry_price) / max(abs(entry_price - stop_price), 1e-9)
            if None in (entry_price, stop_price, target_price):
                continue
            signal_uid = _build_signal_uid(strategy_id, item, entry_dt, entry_price, stop_price, target_price)
            signals.append(
                {
                    "signal_uid": signal_uid,
                    "strategy_id": strategy_id,
                    "strategy_title": strategy_title,
                    "market": market,
                    "trade_type": trade_type,
                    "ticker": ticker or symbol,
                    "name": name,
                    "symbol": symbol or ticker,
                    "side": side,
                    "signal_dt": signal_dt,
                    "entry_dt": entry_dt,
                    "generated_dt": generated_dt,
                    "signal_time": signal_dt.astimezone(timezone.utc).isoformat(),
                    "entry_time": entry_dt.astimezone(timezone.utc).isoformat(),
                    "generated_at": generated_dt.astimezone(timezone.utc).isoformat(),
                    "entry_price": round(float(entry_price), 4),
                    "stop_price": round(float(stop_price), 4),
                    "target_price": round(float(target_price), 4),
                    "rr_ratio": round(float(rr_ratio), 4) if rr_ratio is not None else None,
                    "vol_mult": round(float(vol_mult), 4) if vol_mult is not None else None,
                    "notify_key": str(item.get("notify_key") or "").strip(),
                    "source_path": str(path),
                    "source_lines": [str(x) for x in lines[:2] if str(x).strip()],
                    "raw_item": item,
                }
            )
    return signals


def select_trade_signals(
    signals,
    *,
    now=None,
    markets=None,
    max_orders=None,
    min_rr=None,
    current_prices=None,
    max_age_hours=None,
    market_hours_only=None,
    max_entry_gap_pct=None,
    conflict_gap=15.0,
):
    now = now or _utc_now()
    max_orders = int(max_orders or TRADING_MAX_ORDERS)
    min_rr = float(min_rr or TRADING_MIN_RR)
    max_entry_gap_pct = float(max_entry_gap_pct or TRADING_MAX_ENTRY_GAP_PCT)
    market_hours_only = TRADING_MARKET_HOURS_ONLY if market_hours_only is None else bool(market_hours_only)
    normalized_prices = current_prices or {}

    eligible = []
    skipped = []
    for signal in signals or []:
        ok, reason = _signal_is_eligible(
            signal,
            now=now,
            markets=markets,
            max_age_hours=max_age_hours,
            market_hours_only=market_hours_only,
        )
        if not ok:
            skipped.append(
                {
                    "signal_uid": signal.get("signal_uid"),
                    "ticker": signal.get("ticker"),
                    "side": signal.get("side"),
                    "strategy_id": signal.get("strategy_id"),
                    "reason": reason,
                }
            )
            continue

        if _to_float(signal.get("rr_ratio")) is not None and float(signal["rr_ratio"]) < min_rr:
            skipped.append(
                {
                    "signal_uid": signal.get("signal_uid"),
                    "ticker": signal.get("ticker"),
                    "side": signal.get("side"),
                    "strategy_id": signal.get("strategy_id"),
                    "reason": "rr_below_threshold",
                }
            )
            continue

        current_price = _lookup_current_price(signal, normalized_prices)
        ok, price_reason = _signal_price_alignment(signal, current_price, max_entry_gap_pct)
        if not ok:
            skipped.append(
                {
                    "signal_uid": signal.get("signal_uid"),
                    "ticker": signal.get("ticker"),
                    "side": signal.get("side"),
                    "strategy_id": signal.get("strategy_id"),
                    "reason": price_reason,
                    "current_price": current_price,
                }
            )
            continue

        signal = dict(signal)
        signal["current_price"] = current_price
        signal["score"] = _signal_score(signal, now=now)
        eligible.append(signal)

    grouped = {}
    for signal in eligible:
        grouped.setdefault(_normalize_key(signal.get("ticker")), []).append(signal)

    selected = []
    for bucket in grouped.values():
        bucket.sort(key=lambda s: s.get("score", 0.0), reverse=True)
        if len(bucket) > 1 and bucket[0].get("side") != bucket[1].get("side"):
            bucket[0] = dict(bucket[0])
            bucket[0]["conflict_detected"] = True
            bucket[0]["conflict_runner_up_side"] = bucket[1].get("side")
            bucket[0]["conflict_runner_up_score"] = bucket[1].get("score")
            bucket[0]["conflict_gap"] = float(bucket[0].get("score", 0.0)) - float(bucket[1].get("score", 0.0))
        selected.append(bucket[0])

    selected.sort(key=lambda s: s.get("score", 0.0), reverse=True)
    return selected[:max_orders], skipped, eligible


def _record_signal_state(conn, signal, *, status, error=None, next_retry_at=None):
    now = _utc_iso()
    conn.execute(
        """
        INSERT INTO trade_signals (
            signal_uid, strategy_id, strategy_title, ticker, symbol, market, trade_type, side,
            signal_time, entry_time, entry_price, stop_price, target_price, rr_ratio, vol_mult,
            notify_key, source_path, source_generated_at, latest_status, attempt_count, next_retry_at,
            last_attempt_at, last_error, created_at, updated_at
        ) VALUES (
            :signal_uid, :strategy_id, :strategy_title, :ticker, :symbol, :market, :trade_type, :side,
            :signal_time, :entry_time, :entry_price, :stop_price, :target_price, :rr_ratio, :vol_mult,
            :notify_key, :source_path, :generated_at, :latest_status, :attempt_count, :next_retry_at,
            :last_attempt_at, :last_error, :created_at, :updated_at
        )
        ON CONFLICT(signal_uid) DO UPDATE SET
            strategy_id=excluded.strategy_id,
            strategy_title=excluded.strategy_title,
            ticker=excluded.ticker,
            symbol=excluded.symbol,
            market=excluded.market,
            trade_type=excluded.trade_type,
            side=excluded.side,
            signal_time=excluded.signal_time,
            entry_time=excluded.entry_time,
            entry_price=excluded.entry_price,
            stop_price=excluded.stop_price,
            target_price=excluded.target_price,
            rr_ratio=excluded.rr_ratio,
            vol_mult=excluded.vol_mult,
            notify_key=excluded.notify_key,
            source_path=excluded.source_path,
            source_generated_at=excluded.source_generated_at,
            latest_status=excluded.latest_status,
            attempt_count=excluded.attempt_count,
            next_retry_at=excluded.next_retry_at,
            last_attempt_at=excluded.last_attempt_at,
            last_error=excluded.last_error,
            updated_at=excluded.updated_at
        """,
        {
            "signal_uid": signal["signal_uid"],
            "strategy_id": signal["strategy_id"],
            "strategy_title": signal.get("strategy_title"),
            "ticker": signal["ticker"],
            "symbol": signal.get("symbol"),
            "market": signal.get("market"),
            "trade_type": signal.get("trade_type"),
            "side": signal["side"],
            "signal_time": signal.get("signal_time"),
            "entry_time": signal.get("entry_time"),
            "entry_price": signal.get("entry_price"),
            "stop_price": signal.get("stop_price"),
            "target_price": signal.get("target_price"),
            "rr_ratio": signal.get("rr_ratio"),
            "vol_mult": signal.get("vol_mult"),
            "notify_key": signal.get("notify_key"),
            "source_path": signal.get("source_path"),
            "generated_at": signal.get("generated_at"),
            "latest_status": status,
            "attempt_count": int(signal.get("attempt_count", 0)),
            "next_retry_at": next_retry_at,
            "last_attempt_at": now,
            "last_error": error,
            "created_at": now,
            "updated_at": now,
        },
    )


def _record_order(conn, signal, ticket, response, status, error=None):
    now = _utc_iso()
    conn.execute(
        """
        INSERT INTO trade_orders (
            signal_uid, strategy_id, ticker, symbol, market, side, order_type, quantity,
            entry_price, stop_price, target_price, risk_amount, risk_per_unit, notional,
            execution_mode, broker_order_id, broker_payload_json, broker_response_json,
            status, error, created_at, updated_at
        ) VALUES (
            :signal_uid, :strategy_id, :ticker, :symbol, :market, :side, :order_type, :quantity,
            :entry_price, :stop_price, :target_price, :risk_amount, :risk_per_unit, :notional,
            :execution_mode, :broker_order_id, :broker_payload_json, :broker_response_json,
            :status, :error, :created_at, :updated_at
        )
        ON CONFLICT(signal_uid, execution_mode) DO UPDATE SET
            ticker=excluded.ticker,
            symbol=excluded.symbol,
            market=excluded.market,
            side=excluded.side,
            order_type=excluded.order_type,
            quantity=excluded.quantity,
            entry_price=excluded.entry_price,
            stop_price=excluded.stop_price,
            target_price=excluded.target_price,
            risk_amount=excluded.risk_amount,
            risk_per_unit=excluded.risk_per_unit,
            notional=excluded.notional,
            broker_order_id=excluded.broker_order_id,
            broker_payload_json=excluded.broker_payload_json,
            broker_response_json=excluded.broker_response_json,
            status=excluded.status,
            error=excluded.error,
            updated_at=excluded.updated_at
        """,
        {
            "signal_uid": signal["signal_uid"],
            "strategy_id": signal["strategy_id"],
            "ticker": signal["ticker"],
            "symbol": signal.get("symbol"),
            "market": signal.get("market"),
            "side": signal["side"],
            "order_type": ticket["order_type"],
            "quantity": ticket["quantity"],
            "entry_price": ticket["entry_price"],
            "stop_price": ticket["stop_price"],
            "target_price": ticket["target_price"],
            "risk_amount": ticket["risk_amount"],
            "risk_per_unit": ticket["risk_per_unit"],
            "notional": ticket["notional"],
            "execution_mode": ticket["execution_mode"],
            "broker_order_id": response.get("broker_order_id"),
            "broker_payload_json": json.dumps(ticket["broker_payload"], ensure_ascii=False),
            "broker_response_json": json.dumps(response, ensure_ascii=False),
            "status": status,
            "error": error,
            "created_at": now,
            "updated_at": now,
        },
    )


def load_signal_state(signal_uid):
    init_db()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signal_uid, latest_status, attempt_count, next_retry_at, last_attempt_at, last_error
            FROM trade_signals
            WHERE signal_uid=?
            """,
            (signal_uid,),
        )
        row = cur.fetchone()
    except sqlite3.Error:
        row = None
    finally:
        conn.close()
    if not row:
        return None
    return {
        "signal_uid": row[0],
        "latest_status": row[1],
        "attempt_count": row[2] or 0,
        "next_retry_at": row[3],
        "last_attempt_at": row[4],
        "last_error": row[5],
    }


def load_recent_trade_orders(limit=25):
    init_db()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signal_uid, strategy_id, ticker, symbol, market, side, order_type, quantity,
                   entry_price, stop_price, target_price, risk_amount, risk_per_unit, notional,
                   execution_mode, broker_order_id, status, error, created_at, updated_at
            FROM trade_orders
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    out = []
    for row in rows:
        out.append(
            {
                "signal_uid": row[0],
                "strategy_id": row[1],
                "ticker": row[2],
                "symbol": row[3],
                "market": row[4],
                "side": row[5],
                "order_type": row[6],
                "quantity": row[7],
                "entry_price": row[8],
                "stop_price": row[9],
                "target_price": row[10],
                "risk_amount": row[11],
                "risk_per_unit": row[12],
                "notional": row[13],
                "execution_mode": row[14],
                "broker_order_id": row[15],
                "status": row[16],
                "error": row[17],
                "created_at": row[18],
                "updated_at": row[19],
            }
        )
    return out


def load_latest_trade_report(report_path: Path | None = None):
    payload = _load_json_file(report_path or REPORT_PATH)
    return payload if isinstance(payload, dict) else None


class PaperBroker:
    name = "paper"

    def place_order(self, ticket):
        return {
            "ok": True,
            "status": "PAPER_PLACED",
            "broker_order_id": f"PAPER-{uuid.uuid4().hex[:12].upper()}",
            "broker_response": {
                "accepted": True,
                "paper": True,
                "order": ticket["broker_payload"],
            },
        }


class WebhookBroker:
    name = "webhook"

    def __init__(self, url, token=None, timeout=12):
        self.url = str(url or "").strip()
        self.token = str(token or "").strip()
        self.timeout = int(timeout)

    def place_order(self, ticket):
        if not self.url:
            raise RuntimeError("BROKER_WEBHOOK_URL is required for live execution mode.")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = requests.post(
            self.url,
            headers=headers,
            json=ticket["broker_payload"],
            timeout=self.timeout,
        )
        body = None
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text}
        if resp.status_code >= 300:
            raise RuntimeError(f"broker_webhook_http_{resp.status_code}")
        order_id = None
        if isinstance(body, dict):
            order_id = body.get("order_id") or body.get("id") or body.get("broker_order_id")
        return {
            "ok": True,
            "status": "SUBMITTED",
            "broker_order_id": order_id or f"WEBHOOK-{uuid.uuid4().hex[:12].upper()}",
            "broker_response": body,
        }


def build_broker(mode=None):
    mode = str(mode or TRADING_MODE or "paper").strip().lower()
    if mode in {"paper", "dry_run", "dry-run", "report"}:
        return PaperBroker()
    if mode in {"live", "webhook"}:
        return WebhookBroker(TRADING_WEBHOOK_URL, token=TRADING_WEBHOOK_TOKEN, timeout=TRADING_WEBHOOK_TIMEOUT)
    return PaperBroker()


def build_order_ticket(signal, *, capital=None, risk_pct=None, max_position_pct=None):
    capital = float(capital if capital is not None else TRADING_CAPITAL)
    risk_pct = float(risk_pct if risk_pct is not None else TRADING_RISK_PCT)
    max_position_pct = float(max_position_pct if max_position_pct is not None else TRADING_MAX_POSITION_PCT)

    entry = float(signal["entry_price"])
    stop = float(signal["stop_price"])
    target = float(signal["target_price"])
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return None, "zero_risk_distance"

    risk_amount = capital * (risk_pct / 100.0)
    raw_qty_risk = math.floor(risk_amount / risk_per_unit)
    raw_qty_notional = math.floor((capital * (max_position_pct / 100.0)) / max(entry, 1e-9))
    quantity = max(0, min(raw_qty_risk, raw_qty_notional))
    if quantity <= 0:
        return None, "quantity_below_one"

    order_type = "LIMIT"
    product_type = "MIS" if str(signal.get("trade_type") or "").strip().upper() == "INTRADAY" else "CNC"
    broker_payload = {
        "signal_uid": signal["signal_uid"],
        "strategy_id": signal["strategy_id"],
        "ticker": signal["ticker"],
        "symbol": signal.get("symbol"),
        "market": signal.get("market"),
        "side": signal["side"],
        "order_type": order_type,
        "product_type": product_type,
        "quantity": int(quantity),
        "limit_price": round(entry, 2),
        "stop_loss_price": round(stop, 2),
        "target_price": round(target, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_per_unit": round(risk_per_unit, 2),
        "notional": round(entry * quantity, 2),
        "strategy_title": signal.get("strategy_title"),
        "signal_time": signal.get("signal_time"),
        "entry_time": signal.get("entry_time"),
        "notify_key": signal.get("notify_key"),
    }
    return {
        "signal_uid": signal["signal_uid"],
        "strategy_id": signal["strategy_id"],
        "ticker": signal["ticker"],
        "symbol": signal.get("symbol"),
        "market": signal.get("market"),
        "side": signal["side"],
        "order_type": order_type,
        "quantity": int(quantity),
        "entry_price": round(entry, 4),
        "stop_price": round(stop, 4),
        "target_price": round(target, 4),
        "risk_amount": round(risk_amount, 4),
        "risk_per_unit": round(risk_per_unit, 4),
        "notional": round(entry * quantity, 4),
        "execution_mode": TRADING_MODE,
        "broker_payload": broker_payload,
    }, None


def _send_telegram_summary(message):
    if not TRADING_NOTIFY_TELEGRAM:
        return False
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    text = safe_telegram_text(message, max_len=3500)
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _signal_state_for_db(signal):
    return dict(signal)


def _preflight_signal(signal, current_price, max_entry_gap_pct, require_current_price):
    if current_price is None:
        if require_current_price:
            return False, "missing_current_price"
        return True, None
    ok, reason = _signal_price_alignment(signal, current_price, max_entry_gap_pct)
    if not ok:
        return False, reason
    return True, None


def run_trading_cycle(
    *,
    strategy_dir: Path | None = None,
    report_path: Path | None = None,
    execution_mode: str | None = None,
    markets=None,
    max_orders=None,
    min_rr=None,
    capital=None,
    risk_pct=None,
    max_position_pct=None,
    current_prices=None,
    market_hours_only=None,
    max_age_hours=None,
    max_entry_gap_pct=None,
    require_current_price=None,
):
    init_db()
    strategy_dir = strategy_dir or STRATEGY_DIR
    report_path = report_path or REPORT_PATH
    execution_mode = str(execution_mode or TRADING_MODE or "paper").strip().lower()
    markets = {m.strip().lower() for m in (markets or TRADING_MARKETS) if str(m).strip()}
    max_orders = int(max_orders or TRADING_MAX_ORDERS)
    min_rr = float(min_rr or TRADING_MIN_RR)
    capital = float(capital if capital is not None else TRADING_CAPITAL)
    risk_pct = float(risk_pct if risk_pct is not None else TRADING_RISK_PCT)
    max_position_pct = float(max_position_pct if max_position_pct is not None else TRADING_MAX_POSITION_PCT)
    market_hours_only = TRADING_MARKET_HOURS_ONLY if market_hours_only is None else bool(market_hours_only)
    max_entry_gap_pct = float(max_entry_gap_pct or TRADING_MAX_ENTRY_GAP_PCT)
    require_current_price = TRADING_REQUIRE_CURRENT_PRICE if require_current_price is None else bool(require_current_price)

    now = _utc_now()
    signals = discover_trade_signals(strategy_dir=strategy_dir)
    prices = current_prices if current_prices is not None else load_current_prices()
    selected, skipped, eligible = select_trade_signals(
        signals,
        now=now,
        markets=markets,
        max_orders=max_orders,
        min_rr=min_rr,
        current_prices=prices,
        max_age_hours=max_age_hours,
        market_hours_only=market_hours_only,
        max_entry_gap_pct=max_entry_gap_pct,
    )

    broker = build_broker(execution_mode)
    selected_reports = []
    selected_candidate_reports = []
    placed_reports = []
    failed_reports = []
    selected_candidate_count = len(selected)
    conn = get_conn()
    try:
        for signal in selected:
            selected_candidate_reports.append(
                {
                    "signal_uid": signal["signal_uid"],
                    "strategy_id": signal["strategy_id"],
                    "strategy_title": signal.get("strategy_title"),
                    "ticker": signal["ticker"],
                    "symbol": signal.get("symbol"),
                    "market": signal.get("market"),
                    "side": signal["side"],
                    "entry_price": signal.get("entry_price"),
                    "stop_price": signal.get("stop_price"),
                    "target_price": signal.get("target_price"),
                    "rr_ratio": signal.get("rr_ratio"),
                    "score": signal.get("score"),
                    "current_price": signal.get("current_price"),
                    "trade_type": signal.get("trade_type"),
                    "signal_time": signal.get("signal_time"),
                    "entry_time": signal.get("entry_time"),
                    "conflict_detected": bool(signal.get("conflict_detected")),
                    "conflict_gap": signal.get("conflict_gap"),
                }
            )
        for signal in selected:
            existing = load_signal_state(signal["signal_uid"])
            if existing:
                status = str(existing.get("latest_status") or "").strip().upper()
                next_retry_at = _parse_dt(existing.get("next_retry_at"), market_hint=signal.get("market"))
                if status in PLACED_STATUSES:
                    skipped.append(
                        {
                            "signal_uid": signal.get("signal_uid"),
                            "ticker": signal.get("ticker"),
                            "side": signal.get("side"),
                            "strategy_id": signal.get("strategy_id"),
                            "reason": "already_placed",
                            "previous_status": status,
                        }
                    )
                    continue
                if status in FAILED_STATUSES and next_retry_at and next_retry_at > now:
                    skipped.append(
                        {
                            "signal_uid": signal.get("signal_uid"),
                            "ticker": signal.get("ticker"),
                            "side": signal.get("side"),
                            "strategy_id": signal.get("strategy_id"),
                            "reason": "retry_backoff_active",
                            "previous_status": status,
                        }
                    )
                    continue
                signal_state_attempts = int(existing.get("attempt_count") or 0) + 1
            else:
                signal_state_attempts = 1

            current_price = signal.get("current_price")
            ok, reason = _preflight_signal(signal, current_price, max_entry_gap_pct, require_current_price)
            if not ok:
                skipped.append(
                    {
                        "signal_uid": signal.get("signal_uid"),
                        "ticker": signal.get("ticker"),
                        "side": signal.get("side"),
                        "strategy_id": signal.get("strategy_id"),
                        "reason": reason,
                        "current_price": current_price,
                    }
                )
                continue

            ticket, ticket_reason = build_order_ticket(
                signal,
                capital=capital,
                risk_pct=risk_pct,
                max_position_pct=max_position_pct,
            )
            if ticket is None:
                skipped.append(
                    {
                        "signal_uid": signal.get("signal_uid"),
                        "ticker": signal.get("ticker"),
                        "side": signal.get("side"),
                        "strategy_id": signal.get("strategy_id"),
                        "reason": ticket_reason,
                    }
                )
                continue

            signal_state = _signal_state_for_db(signal)
            signal_state["attempt_count"] = signal_state_attempts
            try:
                response = broker.place_order(ticket)
                status = str(response.get("status") or "SUBMITTED").strip().upper()
                if status in PLACED_STATUSES:
                    status = "PLACED" if execution_mode != "paper" else "PAPER_PLACED"
                _record_signal_state(conn, signal_state, status=status)
                _record_order(conn, signal_state, ticket, response, status=status)
                conn.commit()
                report_row = {
                    "signal_uid": signal["signal_uid"],
                    "strategy_id": signal["strategy_id"],
                    "strategy_title": signal.get("strategy_title"),
                    "ticker": signal["ticker"],
                    "symbol": signal.get("symbol"),
                    "market": signal.get("market"),
                    "side": signal["side"],
                    "current_price": current_price,
                    "entry_price": ticket["entry_price"],
                    "stop_price": ticket["stop_price"],
                    "target_price": ticket["target_price"],
                    "quantity": ticket["quantity"],
                    "risk_amount": ticket["risk_amount"],
                    "risk_per_unit": ticket["risk_per_unit"],
                    "notional": ticket["notional"],
                    "rr_ratio": signal.get("rr_ratio"),
                    "score": signal.get("score"),
                    "status": status,
                    "broker_order_id": response.get("broker_order_id"),
                    "broker_response": response.get("broker_response"),
                    "source_lines": signal.get("source_lines") or [],
                    "notify_key": signal.get("notify_key"),
                    "execution_mode": execution_mode,
                }
                selected_reports.append(report_row)
                placed_reports.append(report_row)
            except Exception as exc:
                err = str(exc)
                next_retry = _utc_now() + timedelta(minutes=TRADING_RETRY_BACKOFF_MINUTES)
                signal_state["attempt_count"] = signal_state_attempts
                _record_signal_state(
                    conn,
                    signal_state,
                    status="FAILED",
                    error=err,
                    next_retry_at=next_retry.astimezone(timezone.utc).isoformat(),
                )
                _record_order(conn, signal_state, ticket, {"ok": False, "error": err}, status="FAILED", error=err)
                conn.commit()
                failed_reports.append(
                    {
                        "signal_uid": signal.get("signal_uid"),
                        "ticker": signal.get("ticker"),
                        "side": signal.get("side"),
                        "strategy_id": signal.get("strategy_id"),
                        "reason": err,
                    }
                )
    finally:
        conn.close()

    report = {
        "generated_at": _utc_iso(now),
        "execution_mode": execution_mode,
        "trading_enabled": TRADING_ENABLED,
        "capital": capital,
        "risk_per_trade_pct": risk_pct,
        "max_position_pct": max_position_pct,
        "max_orders": max_orders,
        "markets": sorted(markets),
        "discovered_count": len(signals),
        "eligible_count": len(eligible),
        "selected_count": selected_candidate_count,
        "executed_count": len(selected_reports),
        "placed_count": len(placed_reports),
        "failed_count": len(failed_reports),
        "skipped_count": len(skipped),
        "current_prices_loaded": len(prices or {}),
        "selected_signals": selected_candidate_reports,
        "executed_signals": selected_reports,
        "placed_signals": placed_reports,
        "failed_signals": failed_reports,
        "skipped_signals": skipped,
    }
    report["summary"] = {
        "discovered": report["discovered_count"],
        "eligible": report["eligible_count"],
        "selected": report["selected_count"],
        "executed": report["executed_count"],
        "placed": report["placed_count"],
        "failed": report["failed_count"],
        "skipped": report["skipped_count"],
    }
    _write_json_atomic(report_path, report)

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO trade_runs (
                generated_at, execution_mode, trading_enabled, discovered_count, eligible_count,
                selected_count, placed_count, skipped_count, failed_count, total_risk_amount,
                report_path, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["generated_at"],
                execution_mode,
                1 if TRADING_ENABLED else 0,
                report["discovered_count"],
                report["eligible_count"],
                report["selected_count"],
                report["placed_count"],
                report["skipped_count"],
                report["failed_count"],
                sum(float(item.get("risk_amount") or 0.0) for item in selected_reports),
                str(report_path),
                json.dumps(report, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    if report["placed_count"] > 0:
        summary_lines = [
            f"Trading cycle {report['generated_at']}",
            f"Mode: {execution_mode} | Placed: {report['placed_count']} | Failed: {report['failed_count']} | Skipped: {report['skipped_count']}",
        ]
        for row in selected_reports[:8]:
            rr_ratio = float(row.get("rr_ratio") or 0.0)
            summary_lines.append(
                f"{row['side']} {row['ticker']} qty {row['quantity']} entry {row['entry_price']:.2f} sl {row['stop_price']:.2f} tgt {row['target_price']:.2f} rr {rr_ratio:.2f}"
            )
        _send_telegram_summary("\n".join(summary_lines))

    return report


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Scan strategy files and place automated orders.")
    parser.add_argument("--mode", default=TRADING_MODE, choices=["paper", "live", "webhook", "dry_run", "dry-run", "report"])
    parser.add_argument("--capital", type=float, default=TRADING_CAPITAL)
    parser.add_argument("--risk-pct", type=float, default=TRADING_RISK_PCT)
    parser.add_argument("--max-orders", type=int, default=TRADING_MAX_ORDERS)
    parser.add_argument("--min-rr", type=float, default=TRADING_MIN_RR)
    parser.add_argument("--max-position-pct", type=float, default=TRADING_MAX_POSITION_PCT)
    parser.add_argument("--max-entry-gap-pct", type=float, default=TRADING_MAX_ENTRY_GAP_PCT)
    parser.add_argument("--max-age-hours", type=float, default=None)
    parser.add_argument("--strategy-dir", default=str(STRATEGY_DIR))
    parser.add_argument("--report-path", default=str(REPORT_PATH))
    parser.add_argument("--markets", default=",".join(sorted(TRADING_MARKETS)))
    parser.add_argument("--skip-current-price-check", action="store_true")
    parser.add_argument("--skip-market-hours-check", action="store_true")
    args = parser.parse_args(argv)

    if not TRADING_ENABLED and args.mode not in {"paper", "dry_run", "dry-run", "report"}:
        print("[TRADER] TRADING_ENABLED=0, so live execution is disabled.")
        return 0

    report = run_trading_cycle(
        strategy_dir=Path(args.strategy_dir),
        report_path=Path(args.report_path),
        execution_mode=args.mode,
        markets={m.strip().lower() for m in str(args.markets or "").split(",") if m.strip()},
        max_orders=args.max_orders,
        min_rr=args.min_rr,
        capital=args.capital,
        risk_pct=args.risk_pct,
        max_position_pct=args.max_position_pct,
        max_entry_gap_pct=args.max_entry_gap_pct,
        max_age_hours=args.max_age_hours,
        market_hours_only=not args.skip_market_hours_check,
        require_current_price=not args.skip_current_price_check,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
