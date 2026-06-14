import os
import sys
import json
import math
import re
import tempfile
import atexit
import fcntl
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
import subprocess

import pytz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


_load_env_file(ROOT / "backend" / ".env")

from backend.database import init_db, get_conn
from backend.daily_intelligence import build_daily_intelligence
from backend.data_fetcher import (
    SYMBOLS,
    GLOBAL_STOCKS,
    CRYPTO,
    COMMODITIES,
    NIFTY50_FALLBACK,
    NIFTY500,
    get_nifty50_symbols,
    get_niftybank_symbols,
    get_sensex_symbols,
    fetch_incremental,
    fetch_live_snapshot,
    fetch_live_price,
    get_last_date,
    fetch_nse_index_realtime,
    fetch_nse_stock_realtime,
    fetch_bulk_last_closes,
    _fetch_dhan_commodity_daily_frame,
)
from backend.data_loader import load_index
from backend.dow_theory import primary_trend, dow_confirmation
from backend.pattern_engine import compute_ranges
from backend.macro_engine import macro_context
from backend.engines.regime_engine import detect_market_regime
from backend.engines.smart_money_engine import compute_smart_money
from backend.engines.event_engine import build_event_profile
from backend.engines.event_trigger_engine import detect_event_driven_move
from backend.reliability.freshness_engine import attach_freshness, DEFAULT_THRESHOLDS
from backend.ingestion.kotak_neo_data import fetch_kotak_deltas
from backend.constituents import get_global_index_constituents
from backend.agentic_pipeline import format_agentic_group_message, format_single_agent_group_message, run_single_agent_quant_terminal

DATA_DIR = ROOT / "frontend"
DATA_PATH = DATA_DIR / "data.json"
SNAPSHOT_PATH = DATA_DIR / "_yesterday_snapshot.json"

GLOBAL_INDEX_NAMES = ["SP500", "NASDAQ", "DAX", "NIKKEI", "HANGSENG"]
KEY_INDIA_INDEX_NAMES = {"NIFTY", "BANKNIFTY", "SENSEX", "INDIA_VIX"}
STRICT_LIVE_ONLY_NAMES = {"NIFTY", "BANKNIFTY", "SENSEX", "GOLD", "SILVER", "CRUDEOIL"}
TOP_TRADES_PATH = ROOT / "strategies" / "top_trades.json"
STRATEGY_DIR = ROOT / "strategies"
STRATEGY_CANDIDATE_POOL_DIR = ROOT / "backend" / "data" / "strategy_candidate_pools"
STRATEGY_CANDIDATE_POOL_LOOKBACK_DAYS = int(os.getenv("STRATEGY_CANDIDATE_POOL_LOOKBACK_DAYS", "4"))
STRATEGY_NOTIFY_STATE_PATH = STRATEGY_DIR / ".strategy_notify_state.json"
INDIA_MORNING_REEVAL_QUEUE_PATH = ROOT / "backend" / "data" / "india_morning_reeval_queue.json"
INDIA_MORNING_REEVAL_SENT_PATH = ROOT / "backend" / "data" / "india_morning_reeval_sent.json"
STRATEGY_NOTIFY_ENABLED = os.getenv("STRATEGY_TELEGRAM_NOTIFICATIONS", "1") == "1"
STRATEGY_NOTIFY_MAX_ITEMS = int(os.getenv("STRATEGY_NOTIFY_MAX_ITEMS", "12"))
STRATEGY_NOTIFY_CHUNK = int(os.getenv("STRATEGY_NOTIFY_CHUNK_SIZE", "3500"))
STRATEGY_NOTIFY_ON_FIRST_RUN = os.getenv("STRATEGY_NOTIFY_ON_FIRST_RUN", "1") == "1"
STRATEGY_NOTIFY_MAX_SIGNAL_AGE_DAYS = int(os.getenv("STRATEGY_NOTIFY_MAX_SIGNAL_AGE_DAYS", "0"))
FAST_DHAN_ALERTS = os.getenv("FAST_DHAN_ALERTS", "1") == "1"
FAST_DHAN_ONLY = os.getenv("FAST_DHAN_ONLY", "0") == "1"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # Back-compat default
TELEGRAM_TRADE_CHAT_ID = os.environ.get("TELEGRAM_TRADE_CHAT_ID") or TELEGRAM_CHAT_ID
TELEGRAM_STATUS_CHAT_ID = (
    os.environ.get("TELEGRAM_STATUS_CHAT_ID")
    or os.environ.get("TELEGRAM_PERSONAL_CHAT_ID")
    or ""
).strip()
LEGACY_STRATEGY_IDS = {
    "india_apollo_ema9_daily_weekly_on",
    "global_apollo_ema9_daily_weekly_on",
    "commodities_apollo_ema9_daily_weekly_on",
    "crypto_apollo_ema9_daily_weekly_on",
}
STRATEGY_CAGR_AUDIT_PATH = ROOT / "backend" / "reports" / "strategy_cagr_audit_5y.json"
STRATEGY_CAGR_ENFORCE = os.getenv("STRATEGY_CAGR_ENFORCE", "1") == "1"
STRATEGY_CAGR_MIN_PCT = float(os.getenv("STRATEGY_CAGR_MIN_PCT", "30"))
STRATEGY_CAGR_PROTECTED_TOKENS = tuple(
    tok.strip().lower()
    for tok in os.getenv(
        "STRATEGY_CAGR_PROTECTED_TOKENS",
        "ema9_growth30_on,quant_trend_breakout_on",
    ).split(",")
    if tok.strip()
)
_STRATEGY_CAGR_MAP_CACHE = None

MIN_HISTORY = int(os.getenv("MIN_HISTORY", "200"))
FAST_MODE = os.getenv("FAST_MODE", "0") == "1"
SKIP_FETCH = os.getenv("SKIP_FETCH", "0") == "1"
SKIP_BREADTH_FETCH = os.getenv("SKIP_BREADTH_FETCH", "0") == "1"
LIVE_PRICES = os.getenv("LIVE_PRICES", "1") == "1"
GLOBAL_GAP_THRESHOLD_PCT = float(os.getenv("GLOBAL_GAP_THRESHOLD_PCT", "1.5"))
LIVE_SYMBOLS = {
    s.strip().upper()
    for s in os.getenv("LIVE_SYMBOLS", "").split(",")
    if s.strip()
}
COMMODITY_MOMENTUM_TICKERS = [
    s.strip()
    for s in os.getenv(
        "COMMODITY_MOMENTUM_TICKERS",
        "GC=F,SI=F,CL=F,NG=F,HG=F,PL=F",
    ).split(",")
    if s.strip()
]

NOW_UTC = datetime.now(timezone.utc).isoformat()
IST = pytz.timezone("Asia/Kolkata")
NOW_IST = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
SILVER_INR_OZ_SYMBOL = "XAGINR=X"
TROY_OZ_PER_KG = 32.1507466
GOLD_OZ_TO_10G = 10 / 31.1034768
SILVER_GOLD_RATIO = float(os.getenv("SILVER_GOLD_RATIO", "80"))
USDINR_OVERRIDE = os.getenv("USDINR_OVERRIDE")
GOLD_INR_10G_OVERRIDE = os.getenv("GOLD_INR_10G_OVERRIDE")
SILVER_INR_KG_OVERRIDE = os.getenv("SILVER_INR_KG_OVERRIDE")

CACHE_DIR = ROOT / "backend" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
RUN_LOCK_PATH = CACHE_DIR / "daily_run.lock"
SILVER_MIGRATION_SENTINEL = CACHE_DIR / "silver_inr_migrated.json"
PREOPEN_MIN = int(os.getenv("PREOPEN_MIN", "20"))
ET = pytz.timezone("America/New_York")
GLOBAL_GL_CACHE_TTL_MIN = int(os.getenv("GLOBAL_GL_CACHE_TTL_MIN", "60"))
BREADTH_CACHE_TTL_MIN = int(os.getenv("BREADTH_CACHE_TTL_MIN", "120"))
DHAN_BREADTH_CACHE_TTL_MIN = int(os.getenv("DHAN_BREADTH_CACHE_TTL_MIN", str(BREADTH_CACHE_TTL_MIN)))
DHAN_BREADTH_SYMBOLS = [
    s.strip().upper()
    for s in os.getenv(
        "DHAN_BREADTH_SYMBOLS",
        ",".join(NIFTY50_FALLBACK.keys()),
    ).split(",")
    if s.strip()
]


def _clone_default_json(default):
    if isinstance(default, dict):
        return dict(default)
    if isinstance(default, list):
        return list(default)
    return default


def _load_json_file(path, default=None, label=None):
    fallback = _clone_default_json(default)
    if not path.exists():
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        name = label or path.name
        print(f"[WARN] failed to read {name}: {exc}")
        return fallback


PREVIOUS_FRONTEND_DATA = _load_json_file(DATA_PATH, default={}, label="previous frontend data")


def _publish_dashboard_snapshot_store(final_payload):
    try:
        from backend.market_snapshot_store import MarketSnapshotStore

        store = MarketSnapshotStore()
        store.write_payload(final_payload, timeframe="dashboard")
    except Exception as exc:
        print(f"[WARN] failed to publish dashboard snapshot store: {exc}")


def _publish_commodity_snapshot_store(final_payload):
    try:
        from backend.market_snapshot_store import MarketSnapshotStore

        commodities = {
            str(name).strip().upper(): value
            for name, value in (final_payload.get("data") or {}).items()
            if str(name).strip().upper() in COMMODITIES
        }
        commodity_strategies = [
            strategy
            for strategy in (final_payload.get("strategies") or [])
            if str(strategy.get("market") or "").strip().lower() == "commodities"
        ]
        if not commodities:
            return
        commodity_payload = {
            "generated_at": final_payload.get("generated_at"),
            "source": "daily_run",
            "market": "commodities",
            "data": commodities,
            "strategies": commodity_strategies,
            "commodity_trends": final_payload.get("commodity_trends") or {},
            "top_trades": final_payload.get("top_trades") or [],
        }
        store = MarketSnapshotStore()
        store.write_payload(commodity_payload, timeframe="commodities_daily")
    except Exception as exc:
        print(f"[WARN] failed to publish commodity snapshot store: {exc}")


def _write_json_atomic(path, payload):
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
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
            tmp_path = Path(f.name)
        os.chmod(tmp_path, 0o644)
        tmp_path.replace(path)
        os.chmod(path, 0o644)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _acquire_daily_run_lock(lock_path):
    handle = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print("[LOCK] daily_run already active; skipping overlapping run")
        return None
    handle.write(f"{os.getpid()}\n")
    handle.flush()

    def _release():
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass

    atexit.register(_release)
    return handle


_RUN_LOCK_HANDLE = _acquire_daily_run_lock(RUN_LOCK_PATH)
if _RUN_LOCK_HANDLE is None:
    raise SystemExit(0)

init_db()
output = {}
_cached_dfs = {}
_live_prices = {}

if SYMBOLS.get("SILVER") == SILVER_INR_OZ_SYMBOL and not SILVER_MIGRATION_SENTINEL.exists():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM prices WHERE index_name=?", ("SILVER",))
        conn.commit()
        conn.close()
        SILVER_MIGRATION_SENTINEL.write_text(json.dumps({"migrated_at": NOW_UTC}, indent=2))
    except Exception:
        pass


def _load_live_prices():
    if not LIVE_PRICES:
        return
    for name, symbol in SYMBOLS.items():
        if LIVE_SYMBOLS and name not in LIVE_SYMBOLS:
            continue
        if not _should_fetch_now(name, "INDEX"):
            continue
        snapshot = None
        try:
            snapshot = fetch_live_snapshot(symbol, name=name)
        except Exception:
            snapshot = None
        price = snapshot.get("price") if snapshot else None
        ts = snapshot.get("timestamp") if snapshot else None
        day_range = snapshot.get("day_range") if snapshot else None
        if price is None and name in KEY_INDIA_INDEX_NAMES:
            rt = fetch_nse_index_realtime(name)
            if rt:
                price = rt.get("price")
                ts = rt.get("timestamp")
                day_range = {
                    "open": _round_finite(rt.get("open")),
                    "high": _round_finite(rt.get("high")),
                    "low": _round_finite(rt.get("low")),
                    "current": _round_finite(rt.get("price")),
                    "timestamp": rt.get("timestamp"),
                    "basis": "intraday",
                    "source": "NSE_INDEX",
                }
        if price is None:
            continue
        _live_prices[name] = {
            "price": round(price, 2),
            "timestamp": ts,
            "day_range": day_range if isinstance(day_range, dict) else None,
        }


def _maybe_fetch(name, symbol):
    if SKIP_FETCH:
        return
    if not _should_fetch_now(name, None) and not _should_force_catchup_fetch(name):
        return
    fetch_incremental(name, symbol)


def _load_index_cached(name):
    if name in _cached_dfs:
        return _cached_dfs[name]
    df = load_index(name)
    _cached_dfs[name] = df
    return df


def _get_usdinr_rate():
    if USDINR_OVERRIDE:
        try:
            return float(USDINR_OVERRIDE)
        except Exception:
            return None
    try:
        resp = requests.get(
            "https://api.exchangerate.host/latest",
            params={"base": "USD", "symbols": "INR"},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = data.get("rates", {}).get("INR")
        if rate:
            return float(rate)
    except Exception:
        pass
    try:
        price, _ = fetch_live_price("USDINR=X")
        if price:
            return float(price)
    except Exception:
        pass
    return None


def _normalize_metal_price(name, symbol, price):
    try:
        raw = float(price)
    except Exception:
        return None
    if raw <= 0:
        return None

    if name == "GOLD":
        if symbol == "GC=F":
            rate = _get_usdinr_rate()
            if rate and rate > 0:
                # GC=F is USD/oz; convert to INR/10g.
                return raw * rate * GOLD_OZ_TO_10G
            return raw
        if raw >= 10000:
            # Already likely INR/10g from override/custom feed.
            return raw
        # Unknown source unit without FX rate; keep raw to avoid bad scaling.
        return raw

    if name == "SILVER":
        if symbol == "SI=F":
            rate = _get_usdinr_rate()
            if rate and rate > 0:
                # SI=F is USD/oz; convert to INR/kg.
                return raw * rate * TROY_OZ_PER_KG
            return raw
        if symbol == SILVER_INR_OZ_SYMBOL:
            if raw >= 10000:
                # Already likely INR/kg.
                return raw
            if raw < 500:
                # XAGINR frequently arrives as INR/gram.
                return raw * 1000.0
            # Treat as INR/oz.
            return raw * TROY_OZ_PER_KG
        if raw >= 10000:
            return raw
        return raw * TROY_OZ_PER_KG

    return raw


def _parse_override(value):
    try:
        return float(value)
    except Exception:
        return None


def _metric_cache_path(key):
    return CACHE_DIR / f"{key}.json"


def _load_metric_cache(key, ttl_minutes):
    path = _metric_cache_path(key)
    if not path.exists():
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if age > timedelta(minutes=ttl_minutes):
            return None
        payload = json.loads(path.read_text())
        return payload
    except Exception:
        return None


def _load_metric_cache_any_age(key):
    path = _metric_cache_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save_metric_cache(key, payload):
    try:
        path = _metric_cache_path(key)
        payload = dict(payload or {})
        payload["cached_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def _breadth_payload_is_valid(payload, require_priced=True):
    if not isinstance(payload, dict):
        return False
    breadth = payload.get("breadth")
    if not isinstance(breadth, dict):
        return False
    if not require_priced:
        vals = [breadth.get("up_pct"), breadth.get("down_pct"), breadth.get("sideways_pct")]
        try:
            return any((float(v) if v is not None else 0.0) != 0.0 for v in vals)
        except Exception:
            return False
    try:
        priced = int(breadth.get("priced", 0) or 0)
    except Exception:
        priced = 0
    return priced > 0


def _breadth_last_available_payload(market, fallback_payload=None):
    candidates = [
        _load_metric_cache_any_age(f"dhan_{market}_breadth"),
        _load_metric_cache_any_age("nifty500_breadth"),
    ]
    prev_breadth = (PREVIOUS_FRONTEND_DATA or {}).get("breadth")
    if isinstance(prev_breadth, dict):
        candidates.append({"breadth": prev_breadth})
    if isinstance(fallback_payload, dict):
        candidates.insert(0, fallback_payload)
    for candidate in candidates:
        if _breadth_payload_is_valid(candidate, require_priced=False):
            payload = dict(candidate)
            breadth = dict(payload.get("breadth") or {})
            breadth.setdefault("source", "legacy_last_available")
            breadth["freshness"] = "last_available"
            payload["breadth"] = breadth
            payload["generated_at"] = NOW_UTC
            return payload
    return None


def _next_open(now, open_hour, open_minute):
    open_today = now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
    if now < open_today and now.weekday() < 5:
        return open_today
    nxt = open_today + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _should_fetch_now(name, asset_type):
    if asset_type == "CRYPTO" or name in CRYPTO:
        return True

    if name in COMMODITIES:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            next_open = _next_open(now, 0, 0)
            return (next_open - now) <= timedelta(minutes=PREOPEN_MIN)
        return True

    is_global = asset_type == "GLOBAL_STOCK" or name in GLOBAL_INDEX_NAMES or name in GLOBAL_STOCKS
    if is_global:
        now = datetime.now(ET)
        open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
        close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        now = datetime.now(IST)
        open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
        close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if now.weekday() >= 5:
        next_open = _next_open(now, open_time.hour, open_time.minute)
        return (next_open - now) <= timedelta(minutes=PREOPEN_MIN)

    if now < open_time:
        return (open_time - now) <= timedelta(minutes=PREOPEN_MIN)
    if now <= close_time:
        return True
    next_open = _next_open(now, open_time.hour, open_time.minute)
    return (next_open - now) <= timedelta(minutes=PREOPEN_MIN)


def _previous_business_day(day):
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def _expected_latest_trading_date(name, asset_type=None):
    is_global = asset_type == "GLOBAL_STOCK" or name in GLOBAL_INDEX_NAMES or name in GLOBAL_STOCKS
    if name in CRYPTO or asset_type == "CRYPTO":
        return datetime.now(timezone.utc).date()
    if is_global:
        now = datetime.now(ET)
        close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        now = datetime.now(IST)
        close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)

    today = now.date()
    if today.weekday() >= 5:
        return _previous_business_day(today)
    if now >= close_time:
        return today
    return _previous_business_day(today)


def _should_force_catchup_fetch(name):
    if name not in KEY_INDIA_INDEX_NAMES:
        return False
    last_date = get_last_date(name)
    if not last_date:
        return True
    try:
        last_dt = pd.to_datetime(last_date, errors="coerce")
    except Exception:
        return True
    if pd.isna(last_dt):
        return True
    expected = _expected_latest_trading_date(name, asset_type="INDEX")
    if expected is None:
        return False
    return last_dt.date() < expected


def _compute_rr_from_ranges(ranges):
    if not ranges:
        return None

    rr = {}
    for horizon, r in ranges.items():
        upside = r.get("high_pct", 0)
        downside = abs(r.get("low_pct", 0))
        rr[horizon] = {
            "upside_pct": upside,
            "downside_pct": downside,
            "rr_ratio": round(upside / downside, 2) if downside > 0 else None,
            "samples": r.get("samples", 0)
        }

    return rr


def _gainers_losers_for_symbols(symbols):
    gainers = 0
    losers = 0
    total = 0
    for name, symbol in symbols.items():
        _maybe_fetch(name, symbol)
        df = _load_index_cached(name)
        if df is None or len(df) < 2:
            continue
        prev_close = float(df["close"].iloc[-2])
        last_close = float(df["close"].iloc[-1])
        if prev_close <= 0:
            continue
        total += 1
        if last_close > prev_close:
            gainers += 1
        elif last_close < prev_close:
            losers += 1
    return {"gainers": gainers, "losers": losers, "total": total}


def _gainers_losers_from_output(asset_type):
    gainers = 0
    losers = 0
    unchanged = 0
    total = 0
    total_assets = 0
    for _, v in output.items():
        if v.get("type") != asset_type:
            continue
        total_assets += 1
        history = v.get("history") or []
        if len(history) < 2:
            continue
        try:
            prev_close = float(history[-2]["close"])
            last_close = float(history[-1]["close"])
        except Exception:
            continue
        if prev_close <= 0:
            continue
        total += 1
        if last_close > prev_close:
            gainers += 1
        elif last_close < prev_close:
            losers += 1
        else:
            unchanged += 1
    return {
        "gainers": gainers,
        "losers": losers,
        "unchanged": unchanged,
        "total": total,
        "total_assets": total_assets
    }


def _gainers_losers_for_index_names(names):
    gainers = 0
    losers = 0
    total = 0
    for name in names:
        meta = output.get(name, {})
        history = meta.get("history") or []
        if len(history) < 2:
            continue
        try:
            prev_close = float(history[-2]["close"])
            last_close = float(history[-1]["close"])
        except Exception:
            continue
        if prev_close <= 0:
            continue
        total += 1
        if last_close > prev_close:
            gainers += 1
        elif last_close < prev_close:
            losers += 1
    return {"gainers": gainers, "losers": losers, "total": total}


def _gainers_losers_per_index(names):
    detail = {}
    for name in names:
        meta = output.get(name, {})
        history = meta.get("history") or []
        gainers = losers = total = 0
        if len(history) >= 2:
            try:
                prev_close = float(history[-2]["close"])
                last_close = float(history[-1]["close"])
            except Exception:
                prev_close = last_close = None
            if prev_close and prev_close > 0 and last_close is not None:
                total = 1
                if last_close > prev_close:
                    gainers = 1
                elif last_close < prev_close:
                    losers = 1
        detail[name] = {"gainers": gainers, "losers": losers, "total": total}
    return detail


def _gainers_losers_for_tickers(tickers):
    gainers = losers = 0
    total = len(tickers or [])
    priced = 0
    tickers = list(tickers or [])
    batch_size = 100
    if any(t.endswith((".T", ".HK", ".DE")) for t in tickers):
        batch_size = 50
    closes = fetch_bulk_last_closes(tickers, batch_size=batch_size)
    if total > 0 and not closes:
        closes = fetch_bulk_last_closes(tickers, batch_size=10)
    for prev_close, last_close in closes.values():
        if prev_close <= 0:
            continue
        priced += 1
        if last_close > prev_close:
            gainers += 1
        elif last_close < prev_close:
            losers += 1
    return {"gainers": gainers, "losers": losers, "total": total, "priced": priced}


def _aggregate_gainers_losers(detail):
    gainers = losers = total = priced = 0
    for stats in detail.values():
        gainers += stats.get("gainers", 0)
        losers += stats.get("losers", 0)
        total += stats.get("total", 0)
        priced += stats.get("priced", 0)
    return {"gainers": gainers, "losers": losers, "total": total, "priced": priced}


def _live_move_threshold(name, asset_type):
    if "VIX" in (name or "").upper():
        return 1.0
    if asset_type == "INDEX":
        return 0.15
    if asset_type in ("INDIA_STOCK", "GLOBAL_STOCK"):
        return 0.25
    if asset_type == "COMMODITY":
        return 0.3
    if asset_type == "CRYPTO":
        return 0.6
    return 0.25


def _validate_live_price(name, asset_type, live_price, last_close):
    if live_price is None:
        return None
    if not isinstance(live_price, (int, float)) or not math.isfinite(live_price):
        return None
    if live_price <= 0:
        return None
    if last_close is None or not isinstance(last_close, (int, float)) or not math.isfinite(last_close) or last_close <= 0:
        return live_price
    threshold = _live_move_threshold(name, asset_type)
    delta = abs(live_price - last_close) / last_close
    if delta > threshold:
        return None
    return live_price


def _round_finite(value, digits=2):
    try:
        value = float(value)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _normalize_live_day_range(day_range, current_price):
    if not isinstance(day_range, dict):
        return None
    normalized = dict(day_range)
    current = _round_finite(current_price)
    if current is None:
        return normalized
    normalized["current"] = current
    high = _round_finite(normalized.get("high"))
    low = _round_finite(normalized.get("low"))
    if high is not None:
        normalized["high"] = max(high, current)
    if low is not None:
        normalized["low"] = min(low, current)
    return normalized


def _estimate_series_timestamp(name, asset_type, df):
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    try:
        row_date = pd.to_datetime(df["date"].iloc[-1], errors="coerce")
    except Exception:
        return None
    if pd.isna(row_date):
        return None

    is_global = asset_type == "GLOBAL_STOCK" or name in GLOBAL_INDEX_NAMES or name in GLOBAL_STOCKS
    if name in CRYPTO or asset_type == "CRYPTO":
        now_local = datetime.now(timezone.utc)
        stamp = now_local.replace(
            year=row_date.year,
            month=row_date.month,
            day=row_date.day,
            hour=23,
            minute=59,
            second=0,
            microsecond=0,
        )
        if stamp > now_local:
            stamp = now_local
        return stamp.isoformat()

    if is_global:
        now_local = datetime.now(ET)
        close_hour, close_minute = 16, 0
    else:
        now_local = datetime.now(IST)
        close_hour, close_minute = 15, 30

    stamp = now_local.replace(
        year=row_date.year,
        month=row_date.month,
        day=row_date.day,
        hour=close_hour,
        minute=close_minute,
        second=0,
        microsecond=0,
    )
    if stamp > now_local:
        stamp = now_local
    return stamp.isoformat()


def _build_latest_daily_range(name, symbol, df):
    if df is None or len(df) == 0:
        return None
    try:
        dated = df.copy()
        dated["date"] = pd.to_datetime(dated["date"], errors="coerce")
        dated = dated.dropna(subset=["date"])
        today = datetime.now(IST).date()
        dated = dated[dated["date"].dt.date <= today]
        if dated.empty:
            return None
        row = dated.iloc[-1]
        row_date = pd.to_datetime(row.get("date"))
        if row_date is None or pd.isna(row_date):
            return None
        if (today - row_date.date()).days > 7:
            return None
        prev_close = float(dated["close"].iloc[-2]) if len(dated) >= 2 else None
        payload = {
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "current": row.get("close"),
            "previous_close": prev_close,
            "timestamp": row_date.isoformat(),
            "basis": "daily",
            "source": "DATABASE_EOD",
        }
    except Exception:
        return None

    normalized = {}
    for key in ["open", "high", "low", "current", "previous_close"]:
        value = payload.get(key)
        if value is None:
            normalized[key] = None
            continue
        if name in {"GOLD", "SILVER"}:
            value = _normalize_metal_price(name, symbol, value)
        normalized[key] = _round_finite(value)

    if normalized.get("current") is None:
        return None
    high = normalized.get("high")
    low = normalized.get("low")
    current = normalized.get("current")
    if high is None:
        high = current
    if low is None:
        low = current
    normalized["high"] = max(high, current)
    normalized["low"] = min(low, current)

    return {
        **normalized,
        "timestamp": payload.get("timestamp"),
        "basis": payload.get("basis"),
        "source": payload.get("source"),
    }


def _ema_state(price, ema):
    if price is None or ema is None:
        return None
    if price > ema:
        return "ABOVE"
    if price < ema:
        return "BELOW"
    return "AT"


def compute_ema9_signal(df):
    if df is None or df.empty:
        return None
    try:
        close = df["close"].astype(float).dropna()
        if close.empty:
            return None
        last_close = float(close.iloc[-1])
        ema9_daily = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
        daily_state = _ema_state(last_close, ema9_daily)

        weekly = (
            df.set_index("date")["close"]
            .astype(float)
            .dropna()
            .resample("W-FRI")
            .last()
            .dropna()
        )
        if weekly.empty:
            return {
                "ema9_daily": round(ema9_daily, 2),
                "ema9_weekly": None,
                "ema9_daily_state": daily_state,
                "ema9_weekly_state": None,
                "ema9_signal": "INSUFFICIENT_DATA",
            }

        weekly_close = float(weekly.iloc[-1])
        ema9_weekly = float(weekly.ewm(span=9, adjust=False).mean().iloc[-1])
        weekly_state = _ema_state(weekly_close, ema9_weekly)

        if daily_state == "ABOVE" and weekly_state == "ABOVE":
            signal = "BULLISH"
        elif daily_state == "BELOW" and weekly_state == "BELOW":
            signal = "BEARISH"
        elif daily_state in ("ABOVE", "BELOW") and weekly_state in ("ABOVE", "BELOW"):
            signal = "TRAP / NO TRADE"
        else:
            signal = "MIXED"

        return {
            "ema9_daily": round(ema9_daily, 2),
            "ema9_weekly": round(ema9_weekly, 2),
            "ema9_daily_state": daily_state,
            "ema9_weekly_state": weekly_state,
            "ema9_signal": signal,
        }
    except Exception:
        return None


def _safe_fetch_news():
    return []


def _safe_kotak_deltas(symbol):
    try:
        data = fetch_kotak_deltas(symbol)
        return data if isinstance(data, dict) else {"status": "ERROR", "reason": "invalid_response"}
    except Exception as exc:
        return {"status": "ERROR", "reason": "exception", "error": str(exc)}


def _fmt_pct(val):
    try:
        return f"{float(val):.1f}%"
    except Exception:
        return "N/A"


def _fmt_num(val, digits=1):
    try:
        return f"{float(val):.{digits}f}"
    except Exception:
        return "N/A"


def build_executive_summary(regime, confirmation, breadth, action_guidance, risk_trend, market_health=None, vix_level=None):
    confirmation_label = str(confirmation or "UNKNOWN").replace("_", " ")
    action = action_guidance[0] if action_guidance else "Stay selective"
    india = (market_health or {}).get("india", {})
    global_ctx = (market_health or {}).get("global", {})
    india_status = india.get("status", "UNKNOWN")
    global_status = global_ctx.get("status", "UNKNOWN")

    line1 = f"India: {india_status} | Global: {global_status} | Dow: {confirmation_label}"
    line2 = (
        f"Breadth: {_fmt_pct(breadth.get('up_pct', 0))} up / {_fmt_pct(breadth.get('down_pct', 0))} down"
        f" | VIX: {_fmt_num(vix_level, 1)} | {risk_trend}. Action: {action}"
    )

    return [line1, line2]


def compute_support_resistance(df, lookback=120, pivot_window=5):
    if df is None or len(df) < (pivot_window * 2 + 2):
        return None

    data = df.tail(lookback)
    highs = data["high"].to_numpy()
    lows = data["low"].to_numpy()
    closes = data["close"].to_numpy()
    if len(closes) == 0:
        return None

    current = float(closes[-1])
    n = len(data)
    pivot_highs = []
    pivot_lows = []

    for i in range(pivot_window, n - pivot_window):
        window_high = highs[i - pivot_window:i + pivot_window + 1]
        window_low = lows[i - pivot_window:i + pivot_window + 1]
        if lows[i] == window_low.min():
            pivot_lows.append(float(lows[i]))
        if highs[i] == window_high.max():
            pivot_highs.append(float(highs[i]))

    if not pivot_lows:
        pivot_lows = [float(lows.min())]
    if not pivot_highs:
        pivot_highs = [float(highs.max())]

    supports = sorted({round(x, 2) for x in pivot_lows})
    resistances = sorted({round(x, 2) for x in pivot_highs})

    supports_below = [s for s in supports if s < current]
    resistances_above = [r for r in resistances if r > current]

    support_near = max(supports_below) if supports_below else round(float(lows.min()), 2)
    resistance_near = min(resistances_above) if resistances_above else round(float(highs.max()), 2)
    support_major = min(supports_below) if supports_below else round(float(lows.min()), 2)
    resistance_major = max(resistances_above) if resistances_above else round(float(highs.max()), 2)

    return {
        "support_near": support_near,
        "resistance_near": resistance_near,
        "support_major": support_major,
        "resistance_major": resistance_major,
        "lookback_days": int(min(lookback, len(data))),
        "pivot_window": int(pivot_window)
    }


def _trend_score(trend):
    if trend == "PRIMARY_UPTREND":
        return 1
    if trend == "PRIMARY_DOWNTREND":
        return -1
    return 0


def _health_status(score):
    if score >= 65:
        return "RISK-ON"
    if score <= 40:
        return "RISK-OFF"
    return "NEUTRAL"


def build_market_health(output, breadth, vix_level, confirmation, regime, leadership):
    india_symbols = ["NIFTY", "BANKNIFTY", "SENSEX"]
    global_symbols = ["SP500", "NASDAQ", "DAX", "NIKKEI", "HANGSENG"]

    def _compute(trends, base_scale=20):
        total = len(trends)
        up = sum(1 for t in trends.values() if t == "PRIMARY_UPTREND")
        down = sum(1 for t in trends.values() if t == "PRIMARY_DOWNTREND")
        score = 50 + ((up - down) / total * base_scale if total else 0)
        return int(max(0, min(100, round(score)))), up, down, total

    india_trends = {k: output.get(k, {}).get("trend") for k in india_symbols}
    india_score, india_up, india_down, india_total = _compute(india_trends, base_scale=22)
    if breadth.get("up_pct", 0) >= 60:
        india_score += 10
    elif breadth.get("up_pct", 0) <= 40:
        india_score -= 10
    if vix_level:
        if vix_level < 14:
            india_score += 8
        elif vix_level > 18:
            india_score -= 8
    if confirmation == "CONFIRMED":
        india_score += 7
    elif confirmation == "NOT_CONFIRMED":
        india_score -= 7
    india_score = int(max(0, min(100, india_score)))

    confirmation_label = str(confirmation or "UNKNOWN").replace("_", " ")
    vix_note = f"VIX {round(vix_level, 2)}" if vix_level is not None else "VIX n/a"
    breadth_source = str(breadth.get("source") or "legacy").strip().lower()
    breadth_freshness = str(breadth.get("freshness") or "").strip().lower()
    if breadth_source == "dhan" and breadth_freshness == "last_available":
        breadth_label = "Dhan last available breadth"
    else:
        breadth_label = "Dhan breadth" if breadth_source == "dhan" else "Breadth"
    india_notes = [
        f"{india_up}/{india_total} key Indian indices in uptrend; Dow {confirmation_label}.",
        f"{breadth_label} {breadth.get('up_pct', 0)}% up; {vix_note}."
    ]

    if regime.get("regime") == "TRENDING":
        mode = "Trend-following opportunities"
    elif regime.get("regime") == "DISTRIBUTION":
        mode = "Defensive / mean-reversion setups"
    else:
        mode = "Range / mean-reversion setups"

    lead = leadership[0] if leadership else "Leadership mixed"
    india_opportunity = f"{mode}. {lead}."

    global_trends = {k: output.get(k, {}).get("trend") for k in global_symbols}
    global_score, global_up, global_down, global_total = _compute(global_trends, base_scale=25)
    global_status = _health_status(global_score)

    global_notes = [
        f"{global_up}/{global_total} global indices in uptrend.",
        "Global strength measured across SP500, NASDAQ, DAX, NIKKEI, HANGSENG."
    ]

    return {
        "india": {
            "score": india_score,
            "status": _health_status(india_score),
            "notes": india_notes,
            "opportunity": india_opportunity
            ,"breadth_source": breadth_source,
        },
        "global": {
            "score": global_score,
            "status": global_status,
            "notes": global_notes
        }
    }


def _intraday_to_trend_frame(frame):
    if frame is None or frame.empty:
        return None
    out = frame.copy().sort_index()
    out.columns = [str(col).lower() for col in out.columns]
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(out.columns)):
        return None
    ordered_cols = [col for col in ["open", "high", "low", "close", "volume"] if col in out.columns]
    out = out[ordered_cols].copy()
    out = out.dropna(subset=["close"])
    if out.empty:
        return None
    out["high"] = pd.to_numeric(out["high"], errors="coerce")
    out["low"] = pd.to_numeric(out["low"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    if "open" in out.columns:
        out["open"] = pd.to_numeric(out["open"], errors="coerce")
    if "volume" in out.columns:
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce")
    return out.dropna(subset=["high", "low", "close"])


def build_dhan_breadth(market="india"):
    cache_key = f"dhan_{market}_breadth"
    cache = _load_metric_cache(cache_key, DHAN_BREADTH_CACHE_TTL_MIN)
    if _breadth_payload_is_valid(cache):
        return cache

    try:
        from backend.dhan_intraday import fetch_intraday_history
    except Exception as exc:
        return {"breadth": {}, "source": "dhan", "error": str(exc)}

    trend_counts = {"PRIMARY_UPTREND": 0, "PRIMARY_DOWNTREND": 0, "TRANSITION": 0, "INSUFFICIENT_DATA": 0}
    trend_map = {}
    symbols = DHAN_BREADTH_SYMBOLS or list(NIFTY50_FALLBACK.keys())
    for symbol in symbols:
        try:
            frame, _meta = fetch_intraday_history(symbol, interval="15m", data_range="60d", market=market)
            trend_frame = _intraday_to_trend_frame(frame)
            trend = primary_trend(trend_frame)
        except Exception:
            trend = "INSUFFICIENT_DATA"
        trend_map[symbol] = trend
        if trend not in trend_counts:
            trend_counts[trend] = 0
        trend_counts[trend] += 1

    total = sum(1 for v in trend_map.values() if v != "INSUFFICIENT_DATA")
    up = trend_counts.get("PRIMARY_UPTREND", 0)
    down = trend_counts.get("PRIMARY_DOWNTREND", 0)
    sideways = max(total - up - down, 0)
    breadth = {
        "up_pct": round((up / total) * 100, 1) if total else 0,
        "down_pct": round((down / total) * 100, 1) if total else 0,
        "sideways_pct": round((sideways / total) * 100, 1) if total else 0,
        "source": "dhan",
        "universe": "nifty50_proxy",
        "symbols": len(symbols),
        "priced": total,
    }
    payload = {
        "breadth": breadth,
        "trend_map": trend_map,
        "trend_counts": trend_counts,
        "generated_at": NOW_UTC,
    }
    if total <= 0:
        fallback = _breadth_last_available_payload(market, fallback_payload=cache)
        if fallback is not None:
            _save_metric_cache(cache_key, fallback)
            return fallback
    _save_metric_cache(cache_key, payload)
    return payload


def load_top_trades():
    if not TOP_TRADES_PATH.exists():
        return {
            "generated_at": None,
            "source": "reliance_open_close",
            "trade_type": "SWING",
            "items": []
        }
    data = _load_json_file(TOP_TRADES_PATH, default=None, label="top trades")
    if isinstance(data, dict):
        return data
    return {
        "generated_at": None,
        "source": "reliance_open_close",
        "trade_type": "SWING",
        "items": []
    }


def _load_strategy_cagr_map():
    global _STRATEGY_CAGR_MAP_CACHE
    if _STRATEGY_CAGR_MAP_CACHE is not None:
        return _STRATEGY_CAGR_MAP_CACHE

    out = {}
    if STRATEGY_CAGR_AUDIT_PATH.exists():
        try:
            payload = json.loads(STRATEGY_CAGR_AUDIT_PATH.read_text())
            rows = payload.get("ranked_results") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("strategy_id") or "").strip()
                if not sid:
                    continue
                try:
                    out[sid] = float(row.get("cagr_pct"))
                except Exception:
                    continue
        except Exception:
            out = {}
    _STRATEGY_CAGR_MAP_CACHE = out
    return out


def _is_protected_strategy_id(strategy_id):
    sid = str(strategy_id or "").strip().lower()
    if not sid:
        return False
    return any(tok in sid for tok in STRATEGY_CAGR_PROTECTED_TOKENS)


def _strategy_passes_cagr_gate(strategy_id):
    sid = str(strategy_id or "").strip()
    if not sid:
        return False
    if sid in LEGACY_STRATEGY_IDS:
        return False
    if not STRATEGY_CAGR_ENFORCE:
        return True
    if _is_protected_strategy_id(sid):
        return True
    cagr_map = _load_strategy_cagr_map()
    cagr = cagr_map.get(sid)
    if cagr is None:
        return False
    return cagr >= STRATEGY_CAGR_MIN_PCT


def _filter_strategies_by_cagr(strategies):
    if not isinstance(strategies, list):
        return []
    kept = []
    dropped = []
    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue
        sid = str(strategy.get("strategy_id") or "").strip()
        if _strategy_passes_cagr_gate(sid):
            kept.append(strategy)
        else:
            dropped.append(sid or "UNKNOWN")
    if dropped:
        uniq = sorted(set(dropped))
        print(
            f"[CAGR_FILTER] min={STRATEGY_CAGR_MIN_PCT:.1f}% "
            f"kept={len(kept)} dropped={len(uniq)} ids={','.join(uniq)}"
        )
    return kept


def _cagr_gate_enabled_ids(strategy_ids, label):
    ids = [str(sid or "").strip() for sid in strategy_ids]
    ids = [sid for sid in ids if sid]
    enabled = [sid for sid in ids if _strategy_passes_cagr_gate(sid)]
    if enabled:
        print(f"[CAGR_GATE] run={label} enabled={','.join(enabled)}")
        return enabled
    print(
        f"[CAGR_GATE] skip={label} min={STRATEGY_CAGR_MIN_PCT:.1f}% "
        f"ids={','.join(ids)}"
    )
    return []


def load_strategies():
    strategies = []
    if not STRATEGY_DIR.exists():
        return strategies
    history_dir = STRATEGY_DIR / "history"
    history_cutoff = datetime.now(IST).date() - timedelta(days=6)

    def _parse_history_date(text):
        value = str(text or "").strip()
        if not value:
            return None
        try:
            if len(value) >= 10 and value[4] == "-" and value[7] == "-":
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            if len(value) == 8 and value.isdigit():
                return datetime.strptime(value, "%Y%m%d").date()
        except Exception:
            return None
        return None

    for path in sorted(STRATEGY_DIR.glob("*.json")):
        name = path.name.lower()
        if name.startswith(".") or "cache" in name or "notify" in name:
            continue
        try:
            data = _load_json_file(path, default=None, label=f"strategy {path.name}")
            if not isinstance(data, dict):
                continue
            strategy_id = data.get("strategy_id") or path.stem
            if not _strategy_passes_cagr_gate(strategy_id):
                continue
            title = data.get("title") or data.get("strategy_name") or strategy_id.replace("_", " ").title()
            data["strategy_id"] = strategy_id
            data["title"] = title
            if not data.get("market"):
                data["market"] = "india"
            if history_dir.exists():
                history_items = []
                for hpath in sorted(history_dir.glob(f"{strategy_id}_*.json")):
                    try:
                        hdata = _load_json_file(hpath, default=None, label=f"history {hpath.name}")
                        if not isinstance(hdata, dict):
                            continue
                        items = hdata.get("items") or []
                        hdate_raw = (hdata.get("generated_at") or "")[:10] or hpath.stem.split("_")[-1]
                        hdate_obj = _parse_history_date(hdate_raw)
                        if hdate_obj and hdate_obj < history_cutoff:
                            continue
                        tickers = []
                        for it in items:
                            t = it.get("ticker") or it.get("symbol") or it.get("name")
                            if t:
                                tickers.append(t)
                        history_items.append({
                            "date": hdate_obj.isoformat() if hdate_obj else hdate_raw,
                            "count": len(items),
                            "tickers": tickers[:8],
                        })
                    except Exception:
                        continue
                if history_items:
                    history_items.sort(key=lambda x: str(x.get("date", "")))
                    data["history"] = history_items[-7:]
            strategies.append(data)
        except Exception:
            continue
    return strategies


def _strategy_candidate_pool_path(strategy_id: str) -> Path:
    safe_id = str(strategy_id or "").strip() or "unknown_strategy"
    return STRATEGY_CANDIDATE_POOL_DIR / f"{safe_id}.json"


def _strategy_candidate_symbol(item):
    if not isinstance(item, dict):
        return None
    symbol = str(item.get("ticker") or item.get("symbol") or item.get("name") or "").strip().upper()
    if not symbol:
        return None
    return symbol[:-3] if symbol.endswith(".NS") else symbol


def _publish_strategy_candidate_pool(strategy_id: str = "india_ema9_growth30_on", lookback_days: int = 4):
    today = datetime.now(IST).date()
    cutoff = today - timedelta(days=max(1, lookback_days) - 1)
    current = STRATEGY_DIR / f"{strategy_id}.json"
    history_dir = STRATEGY_DIR / "history"
    source_files: list[str] = []
    items_by_symbol: dict[str, dict[str, Any]] = {}

    def _maybe_add_file(path: Path) -> None:
        if not path.exists():
            return
        try:
            payload = _load_json_file(path, default=None, label=f"strategy pool {path.name}")
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        generated_at = str(payload.get("generated_at") or "").strip()
        generated_date = None
        if generated_at:
            try:
                generated_date = pd.Timestamp(generated_at).tz_convert(IST).date()
            except Exception:
                try:
                    generated_date = pd.Timestamp(generated_at).date()
                except Exception:
                    generated_date = None
        if generated_date is not None and generated_date < cutoff:
            return
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            return
        source_files.append(path.name)
        for item in items:
            symbol = _strategy_candidate_symbol(item)
            if not symbol:
                continue
            side = str(item.get("side") or item.get("signal") or "").strip().upper()
            signal_time = str(item.get("signal_time") or item.get("entry_time") or item.get("date") or "").strip()
            key = symbol
            existing = items_by_symbol.get(key)
            current_rank = signal_time
            if existing:
                existing_rank = str(existing.get("signal_time") or "")
                if existing_rank >= current_rank:
                    continue
            items_by_symbol[key] = {
                "ticker": symbol,
                "side": side,
                "signal_time": signal_time,
                "entry_time": str(item.get("entry_time") or "").strip(),
                "entry_price": item.get("entry_price"),
                "notify_key": item.get("notify_key"),
                "source_strategy_id": strategy_id,
            }

    _maybe_add_file(current)
    if history_dir.exists():
        for path in sorted(history_dir.glob(f"{strategy_id}_*.json")):
            _maybe_add_file(path)

    symbols = sorted(items_by_symbol.keys())
    payload = {
        "strategy_id": strategy_id,
        "generated_at": NOW_UTC,
        "lookback_days": max(1, lookback_days),
        "cutoff_date": cutoff.isoformat(),
        "source_files": source_files,
        "symbols": symbols,
        "items": [items_by_symbol[symbol] for symbol in symbols],
        "counts": {
            "symbols": len(symbols),
            "items": len(items_by_symbol),
            "source_files": len(source_files),
        },
        "notes": [
            "Explicit 4-day candidate pool published from the EMA9 strategy outputs.",
            "Used by pattern_oi_vwap_ema_scanner as the primary watchlist source.",
        ],
    }
    _write_json_atomic(_strategy_candidate_pool_path(strategy_id), payload)
    return payload


def _send_telegram_to(chat_id, message):
    if not STRATEGY_NOTIFY_ENABLED:
        return False
    if not TELEGRAM_TOKEN or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _send_telegram_trade(message):
    return _send_telegram_to(TELEGRAM_TRADE_CHAT_ID, message)


def _send_telegram_status(message):
    if not TELEGRAM_STATUS_CHAT_ID:
        return False
    return _send_telegram_to(TELEGRAM_STATUS_CHAT_ID, message)


def _best_cmp(item):
    # Prefer the most "current" price field available in the payload.
    for key in ("cmp", "current_price", "price", "close", "entry_px", "entry_price", "entryPx"):
        val = item.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except Exception:
                continue
    return None


def _compact_trade_line(item, *, market_tag: str = "", currency_symbol: str = ""):
    ticker = item.get("ticker") or item.get("symbol") or item.get("name") or ""
    side = (item.get("side") or item.get("signal") or "").upper()
    signal_time = item.get("signal_time") or item.get("signal") or item.get("time") or item.get("entry_time") or ""
    cmp_val = _best_cmp(item)
    if isinstance(cmp_val, (int, float)):
        sym = currency_symbol or ""
        cmp_text = f" | CMP: {sym}{cmp_val:.2f}".rstrip()
    else:
        cmp_text = ""
    tag = (market_tag or "").strip().upper()
    prefix = f"{tag} | " if tag else ""
    return f"{prefix}{ticker} | {side} | {signal_time}{cmp_text}".strip()


def _agent_input_for_trade(ticker, market, item_block):
    # Minimal stock context for the agent pipeline:
    # Stock name + market + the raw stock row that already carries the important indicators.
    parts = [
        f"Stock Name: {ticker}",
        f"Market: {market}",
        *[ln.rstrip() for ln in (item_block or []) if str(ln).strip()],
    ]
    return "\n".join(parts).strip()


def _market_currency_symbol(market: str) -> str:
    m = str(market or "").strip().upper()
    if m == "INDIA":
        return "₹"
    if m in {"GLOBAL", "CRYPTO"}:
        return "$"
    # Commodities can be INR or USD depending on feed; default to no symbol to avoid lying.
    return ""

def _send_telegram_chunks(message):
    if not message:
        return 0
    if len(message) <= STRATEGY_NOTIFY_CHUNK:
        return int(_send_telegram_status(message))

    sent = 0
    buf = ""
    for line in message.splitlines():
        candidate = f"{buf}\n{line}" if buf else line
        if len(candidate) <= STRATEGY_NOTIFY_CHUNK:
            buf = candidate
            continue
        if buf:
            sent += int(_send_telegram_status(buf))
        buf = line
    if buf:
        sent += int(_send_telegram_status(buf))
    return sent


def _load_strategy_notify_state():
    if not STRATEGY_NOTIFY_STATE_PATH.exists():
        return False, {}
    try:
        data = json.loads(STRATEGY_NOTIFY_STATE_PATH.read_text())
        if isinstance(data, dict):
            out = {}
            for sid, values in data.items():
                if isinstance(values, list):
                    normalized = []
                    seen = set()
                    for v in values:
                        if not v:
                            continue
                        txt = str(v)
                        parts = txt.split("|")
                        if len(parts) >= 3:
                            ticker = parts[0].strip().upper()
                            day = parts[1].strip()
                            side = parts[2].strip().upper()
                            if ticker and len(day) == 10 and day[4] == "-" and day[7] == "-" and side in {"BUY", "SELL"}:
                                txt = f"{ticker}|{day}|{side}"
                        if txt and txt not in seen:
                            seen.add(txt)
                            normalized.append(txt)
                    out[str(sid)] = normalized
            return True, out
    except Exception:
        pass
    return True, {}


def _save_strategy_notify_state(state):
    try:
        payload = {k: v[-1000:] for k, v in state.items() if isinstance(v, list)}
        STRATEGY_NOTIFY_STATE_PATH.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def _safe_read_json(path: Path, default):
    try:
        if not path.exists():
            return default
        data = json.loads(path.read_text())
        return data
    except Exception:
        return default


def _safe_write_json_atomic(path: Path, data) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            tmp_path = Path(f.name)
        tmp_path.replace(path)
        return True
    except Exception:
        return False


def _extract_day_ist_from_signal_text(signal_time_text: str) -> str:
    """
    Returns YYYY-MM-DD for queue bucketing.
    Prefers parsing timestamps (UTC->IST) but falls back to YYYY-MM-DD prefix or today's IST date.
    """
    text = str(signal_time_text or "").strip()
    if text:
        dt = pd.to_datetime(text, errors="coerce", utc=True)
        if not pd.isna(dt):
            try:
                return dt.tz_convert(IST).date().isoformat()
            except Exception:
                pass
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
    return datetime.now(IST).date().isoformat()


def _enqueue_india_morning_reeval(records: list[dict]) -> bool:
    """
    Persist today's INDIA trade agent inputs so we can re-evaluate next working day morning.
    """
    if not records:
        return False
    existing = _safe_read_json(INDIA_MORNING_REEVAL_QUEUE_PATH, default={})
    if not isinstance(existing, dict):
        existing = {}

    changed = False
    for r in records:
        if not isinstance(r, dict):
            continue
        day = str(r.get("day") or "").strip()
        trade_key = str(r.get("trade_key") or "").strip()
        agent_input = str(r.get("agent_input") or "").strip()
        if not day or not trade_key or not agent_input:
            continue
        bucket = existing.get(day)
        if not isinstance(bucket, dict):
            bucket = {}
            existing[day] = bucket
        if trade_key in bucket:
            continue
        bucket[trade_key] = {
            "day": day,
            "trade_key": trade_key,
            "ticker": str(r.get("ticker") or "").strip(),
            "side": str(r.get("side") or "").strip(),
            "signal_time": str(r.get("signal_time") or "").strip(),
            "agent_input": agent_input,
            "queued_at": datetime.now(IST).isoformat(timespec="seconds"),
        }
        changed = True

    return _safe_write_json_atomic(INDIA_MORNING_REEVAL_QUEUE_PATH, existing) if changed else True


def _sanitize_json_value(value):
    if isinstance(value, dict):
        return {k: _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(v) for v in value]
    if isinstance(value, np.floating):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _strategy_pattern_hint(item):
    if not isinstance(item, dict):
        return ""
    for key in ("pattern", "strategy_pattern", "daily_candle_type", "setup_type", "summary", "reason", "signal_text"):
        text = str(item.get(key) or "").strip()
        if text:
            return text[:160]
    lines = item.get("lines") or []
    if isinstance(lines, list):
        for raw in lines[:2]:
            text = str(raw or "").strip()
            if text:
                # Keep the leading setup phrase but strip trailing numeric noise.
                head = text.split("|", 1)[0].strip()
                if head:
                    return head[:160]
                return text[:160]
    return ""


def _strategy_signal_day(item):
    for key in ("signal_time", "entry_time", "time", "date"):
        text = str(item.get(key) or "").strip()
        if not text:
            continue
        dt = pd.to_datetime(text, errors="coerce", utc=True)
        if not pd.isna(dt):
            try:
                return dt.tz_convert(IST).date().isoformat()
            except Exception:
                pass
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
    return datetime.now(IST).date().isoformat()


def _normalize_notify_key(custom: str, item: dict | None = None, strategy_id: str | None = None) -> str:
    text = str(custom or "").strip()
    ticker = str((item or {}).get("ticker") or (item or {}).get("symbol") or (item or {}).get("name") or "").strip().upper()
    side = str((item or {}).get("side") or (item or {}).get("signal") or "").strip().upper()
    signal_day = _strategy_signal_day(item or {})
    pattern = _strategy_pattern_hint(item or {})

    if text:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        if parts and not ticker:
            ticker = parts[0].strip().upper()
        if not side:
            for part in parts:
                up = part.strip().upper()
                if up in {"BUY", "SELL"}:
                    side = up
                    break
        # Find any date-like token in the notify key, even if it is not the second field.
        for part in parts:
            dt = pd.to_datetime(part, errors="coerce", utc=True)
            if not pd.isna(dt):
                try:
                    signal_day = dt.tz_convert(IST).date().isoformat()
                    break
                except Exception:
                    pass
            if len(part) >= 10 and part[4] == "-" and part[7] == "-":
                signal_day = part[:10]
                break
        if not pattern:
            pattern = _strategy_pattern_hint(item or {})

    prefix = str(strategy_id or "").strip().upper()
    parts = [p for p in [prefix, ticker, signal_day, side, pattern] if p]
    return "|".join(parts)[:300]


def _strategy_item_signature(item, strategy_id=None):
    custom = str(item.get("notify_key") or "").strip()
    if custom:
        return _normalize_notify_key(custom, item=item, strategy_id=strategy_id)
    ticker = str(item.get("ticker") or item.get("symbol") or item.get("name") or "").strip().upper()
    side = _infer_alert_side(item)
    signal_day = _strategy_signal_day(item)
    pattern = _strategy_pattern_hint(item)
    if ticker and side in {"BUY", "SELL"}:
        prefix = str(strategy_id or "").strip().upper()
        parts = [p for p in [prefix, ticker, signal_day, side, pattern] if p]
        return "|".join(parts)[:300]
    lines = item.get("lines") or []
    head = " | ".join(str(x).strip() for x in lines[:2] if str(x).strip()).strip()
    if len(head) > 220:
        head = head[:220]
    prefix = str(strategy_id or "").strip().upper()
    parts = [p for p in [prefix, ticker, signal_day, pattern or head] if p]
    return "|".join(parts)[:300]


def _strategy_rules_lines(strategy):
    rules = strategy.get("rules")
    if not isinstance(rules, dict) or not rules:
        notes = strategy.get("notes") or []
        if isinstance(notes, list):
            fallback = []
            for note in notes[:2]:
                text = str(note or "").strip()
                if text:
                    fallback.append(f"  - {text}")
            if fallback:
                return ["Filters:", *fallback]
        return []
    key_order = [
        "breakout_condition",
        "entry_window_candles",
        "entry_trigger",
        "volume_multiple",
        "lookback_volume",
        "min_rr",
        "target_atr_multiple",
        "filter_mode",
    ]
    pretty_map = {
        "breakout_condition": "Breakout",
        "entry_window_candles": "Entry Window",
        "entry_trigger": "Entry Trigger",
        "volume_multiple": "Volume Mult",
        "lookback_volume": "Vol Lookback",
        "min_rr": "Min RR",
        "target_atr_multiple": "Target ATR x",
        "filter_mode": "Mode",
    }
    parts = []
    for key in key_order:
        if key not in rules:
            continue
        val = rules.get(key)
        if val is None or val == "":
            continue
        parts.append(f"{pretty_map.get(key, key)}: {val}")
    if not parts:
        return []
    return ["Filters:", *[f"  - {p}" for p in parts]]


def _format_strategy_item_for_alert(item, idx):
    ticker = str(item.get("ticker") or item.get("symbol") or item.get("name") or "UNKNOWN").strip()
    side = _infer_alert_side(item)
    signal_time = str(item.get("signal_time") or "").strip()
    entry_time = str(item.get("entry_time") or "").strip()
    entry_price = item.get("entry_price")
    stop_price = item.get("stop_price")
    target_price = item.get("target_price")
    rr_ratio = item.get("rr_ratio")
    vol_mult = item.get("vol_mult")

    header = f"{idx}) {ticker} | Side: {side}"
    timing_parts = []
    if signal_time:
        timing_parts.append(f"Signal: {signal_time}")
    if entry_time and entry_time.upper() != "NA":
        timing_parts.append(f"Entry: {entry_time}")

    trade_parts = []
    if isinstance(entry_price, (int, float)):
        trade_parts.append(f"EntryPx: {entry_price:.2f}")
    if isinstance(stop_price, (int, float)):
        trade_parts.append(f"SL: {stop_price:.2f}")
    if isinstance(target_price, (int, float)):
        trade_parts.append(f"TGT: {target_price:.2f}")
    if isinstance(rr_ratio, (int, float)):
        trade_parts.append(f"RR: {rr_ratio:.2f}")
    if isinstance(vol_mult, (int, float)):
        trade_parts.append(f"VolX: {vol_mult:.2f}")

    lines = [header]
    if timing_parts:
        lines.append("   " + " | ".join(timing_parts))
    if trade_parts:
        lines.append("   " + " | ".join(trade_parts))

    raw_lines = item.get("lines") or []
    text_lines = [str(x).strip() for x in raw_lines[:2] if str(x).strip()]
    if text_lines:
        lines.append("   Note: " + " | ".join(text_lines))
    return lines


def _infer_alert_side(item):
    for key in ("side", "trade_side", "signal_side", "direction"):
        value = str(item.get(key) or "").strip().upper()
        if value in {"BUY", "SELL"}:
            return value

    probe_fields = [
        item.get("action"),
        item.get("summary"),
        item.get("signal_text"),
        item.get("notes"),
        item.get("reason"),
    ]
    lines = item.get("lines") or []
    probe_lines = [str(x) for x in lines[:6]]
    probe = " ".join([str(x) for x in probe_fields if x] + probe_lines).upper()
    probe = re.sub(r"\s+", " ", probe)

    if re.search(r"\b(SELL|SHORT|BEARISH)\b", probe):
        return "SELL"
    if re.search(r"\b(BUY|LONG|BULLISH)\b", probe):
        return "BUY"
    return "N/A"


def _item_time_rank(item):
    ts = item.get("entry_time") or item.get("signal_time") or item.get("time")
    dt = pd.to_datetime(ts, errors="coerce", utc=True)
    if pd.isna(dt):
        return -1
    return int(dt.value)


def _strategy_generated_date_ist(strategy):
    ts = str((strategy or {}).get("generated_at") or "").strip()
    if not ts:
        return None
    dt = pd.to_datetime(ts, errors="coerce", utc=True)
    if pd.isna(dt):
        return None
    try:
        return dt.tz_convert(IST).date()
    except Exception:
        return None


def _item_signal_date_ist(item, fallback_date=None):
    candidates = [
        item.get("entry_time"),
        item.get("signal_time"),
        item.get("time"),
        item.get("date"),
    ]
    for ts in candidates:
        text = str(ts or "").strip()
        if not text:
            continue
        dt = pd.to_datetime(text, errors="coerce", utc=True)
        if pd.isna(dt):
            continue
        try:
            return dt.tz_convert(IST).date()
        except Exception:
            continue
    return fallback_date


def _filter_recent_alert_sigs(sig_to_item, sigs, strategy):
    max_age_days = STRATEGY_NOTIFY_MAX_SIGNAL_AGE_DAYS
    if max_age_days < 0:
        return list(sigs), 0

    now_ist = datetime.now(IST).date()
    fallback_date = _strategy_generated_date_ist(strategy)
    kept = []
    stale_dropped = 0
    for sig in sigs:
        item = sig_to_item.get(sig) or {}
        item_date = _item_signal_date_ist(item, fallback_date=fallback_date)
        if item_date is None:
            stale_dropped += 1
            continue
        age_days = max(0, (now_ist - item_date).days)
        if age_days <= max_age_days:
            kept.append(sig)
        else:
            stale_dropped += 1
    return kept, stale_dropped


def _select_alert_sigs(sig_to_item, sigs):
    ordered = []
    for sig in sigs:
        item = sig_to_item.get(sig) or {}
        ordered.append((sig, item, _item_time_rank(item)))
    ordered.sort(key=lambda x: x[2], reverse=True)
    return [sig for sig, _, _ in ordered]


def _notify_new_strategy_trades(strategies):
    if not STRATEGY_NOTIFY_ENABLED:
        return
    if not TELEGRAM_TOKEN or not TELEGRAM_TRADE_CHAT_ID:
        return
    if not isinstance(strategies, list):
        return

    state_exists, prev_state = _load_strategy_notify_state()
    # Preserve prior alert signatures so empty scans do not wipe dedupe history.
    next_state = dict(prev_state)
    alerts = []
    compact_lines = []
    compact_markets = []
    india_reeval_records = []
    seen_trade_keys = set()

    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue

        strategy_id = str(strategy.get("strategy_id") or strategy.get("title") or "unknown")
        title = str(strategy.get("title") or strategy_id)
        items = strategy.get("items") or []
        if not isinstance(items, list):
            items = []

        sig_to_item = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            sig = _strategy_item_signature(item, strategy_id=strategy_id)
            if sig:
                sig_to_item[sig] = item

        current_sigs = sorted(sig_to_item.keys())
        prev_sigs = set(prev_state.get(strategy_id, []))
        if current_sigs:
            next_state[strategy_id] = current_sigs
        new_sigs = [s for s in current_sigs if s not in prev_sigs]
        should_send = (state_exists and bool(new_sigs)) or (not state_exists and STRATEGY_NOTIFY_ON_FIRST_RUN and bool(current_sigs))
        if not should_send:
            continue
        send_sigs = new_sigs if state_exists else current_sigs
        send_sigs, stale_dropped = _filter_recent_alert_sigs(sig_to_item, send_sigs, strategy)
        if not send_sigs:
            continue
        send_sigs = _select_alert_sigs(sig_to_item, send_sigs)
        if not send_sigs:
            continue

        mode_text = "NEW TRADES" if state_exists else "INITIAL ACTIVE TRADES"
        market = str(strategy.get("market") or "india").upper()
        trade_type = str(strategy.get("trade_type") or "STRATEGY").upper()
        page_size = STRATEGY_NOTIFY_MAX_ITEMS if STRATEGY_NOTIFY_MAX_ITEMS > 0 else len(send_sigs)

        for page_start in range(0, len(send_sigs), page_size):
            chunk_sigs = send_sigs[page_start:page_start + page_size]
            page_no = (page_start // page_size) + 1
            page_total = (len(send_sigs) + page_size - 1) // page_size
            lines = [
                f"Strategy: {title}",
                f"ID: {strategy_id}",
                f"Mode: {mode_text}",
                f"Market: {market} | Type: {trade_type}",
                "Selection: all new eligible trades (no per-asset cap)",
                (
                    f"Freshness: signal age <= {STRATEGY_NOTIFY_MAX_SIGNAL_AGE_DAYS} day(s)"
                    if STRATEGY_NOTIFY_MAX_SIGNAL_AGE_DAYS >= 0
                    else "Freshness: disabled"
                ),
                f"Trades in message: {len(chunk_sigs)} / {len(send_sigs)}"
                + (f" | Page {page_no}/{page_total}" if page_total > 1 else ""),
            ]
            if stale_dropped > 0 and page_start == 0:
                lines.append(f"Skipped stale signals: {stale_dropped}")
            lines.extend(_strategy_rules_lines(strategy))
            lines.append("")
            for local_idx, sig in enumerate(chunk_sigs, start=page_start + 1):
                item = sig_to_item.get(sig, {})
                lines.extend(_format_strategy_item_for_alert(item, local_idx))
                lines.append("")
                # De-dupe per trade key so agent/group doesn't repeat the same stock multiple times.
                ticker = str(item.get("ticker") or item.get("symbol") or item.get("name") or "").strip()
                if ticker.upper() == "TATASTEEL":
                    continue
                side = str(item.get("side") or item.get("signal") or "").strip().upper()
                signal_time = str(item.get("signal_time") or item.get("signal") or item.get("time") or item.get("entry_time") or "").strip()
                trade_key = f"{ticker}|{side}|{signal_time}"
                if trade_key in seen_trade_keys:
                    continue
                seen_trade_keys.add(trade_key)

                compact_lines.append(_compact_trade_line(item, market_tag=market, currency_symbol=_market_currency_symbol(market)))
                compact_markets.append(market)
                # Queue INDIA new trades for next-working-day morning re-eval.
                if state_exists and market == "INDIA":
                    day = _extract_day_ist_from_signal_text(signal_time)
                    india_reeval_records.append(
                        {
                            "day": day,
                            "trade_key": f"{ticker}|{side}|{day}",
                            "ticker": ticker,
                            "side": side,
                            "signal_time": signal_time,
                            "compact_line": _compact_trade_line(item, market_tag=market, currency_symbol=_market_currency_symbol(market)),
                        }
                    )
            alerts.append("\n".join(lines))

    _save_strategy_notify_state(next_state)
    for message in alerts:
        _send_telegram_chunks(message)

    # Group gets 1 message per stock: tag | ticker | side | time | CMP + combined agent output
    compact_lines = [ln for ln in compact_lines if ln]
    if compact_lines:
        for idx, ln in enumerate(compact_lines[:50]):
            market = compact_markets[idx] if idx < len(compact_markets) else ""
            ticker_match = re.match(r"^(?:[A-Z]+ \| )?([A-Z0-9&_.-]+)\s*\|", ln.strip())
            ticker = ticker_match.group(1).strip().upper() if ticker_match else ""
            terminal_result = run_single_agent_quant_terminal(ticker or "UNKNOWN", strategy_item=item)
            try:
                payload = format_single_agent_group_message(
                    ln,
                    terminal_result,
                    market=market,
                    strategy_context={
                        "title": title,
                        "id": strategy_id,
                        "mode": mode_text,
                        "market": market,
                        "trade_type": trade_type,
                        "selection": "all new eligible trades (no per-asset cap)",
                        "freshness": (
                            f"signal age <= {STRATEGY_NOTIFY_MAX_SIGNAL_AGE_DAYS} day(s)"
                            if STRATEGY_NOTIFY_MAX_SIGNAL_AGE_DAYS >= 0
                            else "freshness disabled"
                        ),
                        "filters": " | ".join(
                            line.strip().lstrip("-").strip()
                            for line in _strategy_rules_lines(strategy)[1:]
                            if line.strip() and line.strip() != "Filters:"
                        ),
                    },
                )
            except Exception:
                payload = format_agentic_group_message(ln, terminal_result, market=market)
            _send_telegram_trade(payload)

    # Persist INDIA queue even if Telegram is down; we still want the next-morning re-eval.
    if india_reeval_records:
        _enqueue_india_morning_reeval(india_reeval_records)


def run_intraday_momentum_scan():
    scan_script = ROOT / "strategies" / "intraday_momentum_scan.py"
    if not scan_script.exists():
        scan_script = ROOT.parent / "market-context-local-data" / "momentum_breakout_scan.py"
    if not scan_script.exists():
        return
    equities_ids = _cagr_gate_enabled_ids(
        ["intraday_momentum_on", "intraday_momentum_wait"],
        "intraday_momentum_equities",
    )
    commodities_ids = _cagr_gate_enabled_ids(
        ["intraday_momentum_commodities_on", "intraday_momentum_commodities_wait"],
        "intraday_momentum_commodities",
    )
    if not equities_ids and not commodities_ids:
        return

    def _run_scan(args):
        try:
            subprocess.run(
                [sys.executable, str(scan_script), *args],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return

    # Existing equities momentum scan (all pages via frontend filtering)
    if equities_ids:
        _run_scan([
            "--fno",
            "--backfill-days", "5",
            "--strategy-prefix", "intraday_momentum",
            "--market", "all",
            "--title-prefix", "Intraday Momentum",
        ])

    # Commodity-specific momentum scan (shown directly on commodities page)
    if commodities_ids and COMMODITY_MOMENTUM_TICKERS:
        _run_scan([
            "--tickers", ",".join(COMMODITY_MOMENTUM_TICKERS),
            "--backfill-days", "5",
            "--strategy-prefix", "intraday_momentum_commodities",
            "--market", "commodities",
            "--title-prefix", "Commodities Intraday Momentum",
        ])


def run_gold_breakout_retest_scan():
    scan_script = ROOT / "strategies" / "gold_breakout_retest_scan.py"
    if not scan_script.exists():
        return
    enabled_ids = _cagr_gate_enabled_ids(
        [
            "india_breakout_retest_on",
            "global_breakout_retest_on",
            "crypto_breakout_retest_on",
            "commodities_breakout_retest_on",
        ],
        "breakout_retest_scan",
    )
    if not enabled_ids:
        return
    try:
        subprocess.run(
            [sys.executable, str(scan_script)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(ROOT),
        )
    except Exception:
        return


def run_ema9_growth30_scan():
    scan_script = ROOT / "strategies" / "apollo_ema9_strategy.py"
    if not scan_script.exists():
        return
    enabled_ids = set(
        _cagr_gate_enabled_ids(
            [
                "india_ema9_growth30_on",
                "global_ema9_growth30_on",
                "commodities_ema9_growth30_on",
                "crypto_ema9_growth30_on",
            ],
            "ema9_growth30_scan",
        )
    )
    if not enabled_ids:
        return

    def _run(args):
        try:
            subprocess.run(
                [sys.executable, str(scan_script), *args],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(ROOT),
            )
        except Exception:
            return

    common = [
        "--only-signal",
        "--last-n-days", "30",
        "--max-items", "200",
        "--write-strategy-json",
        "--strategy-owner", "HARSHIT",
        "--strategy-trade-type", "SWING",
    ]

    scan_targets = [
        {
            "market": "india",
            "strategy_id": "india_ema9_growth30_on",
            "strategy_title": "India F&O + Index Radar (EMA9 Growth 30)",
            "extra_args": ["--nifty-futures", "--include-index-futures"],
        },
        {
            "market": "global",
            "strategy_id": "global_ema9_growth30_on",
            "strategy_title": "Global EMA9 Growth 30",
            "extra_args": ["--include-index-futures"],
        },
        {
            "market": "commodities",
            "strategy_id": "commodities_ema9_growth30_on",
            "strategy_title": "Commodities EMA9 Growth 30",
            "extra_args": [],
        },
        {
            "market": "crypto",
            "strategy_id": "crypto_ema9_growth30_on",
            "strategy_title": "Crypto EMA9 Growth 30",
            "extra_args": [],
        },
    ]

    for target in scan_targets:
        strategy_id = target["strategy_id"]
        if strategy_id not in enabled_ids:
            continue
        _run([
            "--market", target["market"],
            *target["extra_args"],
            "--strategy-id", strategy_id,
            "--strategy-title", target["strategy_title"],
            "--strategy-market", target["market"],
            *common,
        ])


def run_quant_trend_breakout_scan():
    scan_script = ROOT / "strategies" / "quant_trend_breakout_strategy.py"
    if not scan_script.exists():
        return
    enabled_ids = set(
        _cagr_gate_enabled_ids(
            [
                "india_quant_trend_breakout_on",
                "global_quant_trend_breakout_on",
                "commodities_quant_trend_breakout_on",
                "crypto_quant_trend_breakout_on",
            ],
            "quant_trend_breakout_scan",
        )
    )
    if not enabled_ids:
        return

    def _run(extra_args):
        try:
            subprocess.run(
                [sys.executable, str(scan_script), *extra_args],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(ROOT),
            )
        except Exception:
            return

    common = [
        "--only-signal",
        "--last-n-days", "1",
        "--max-items", "200",
        "--write-strategy-json",
        "--strategy-owner", "HARSHIT",
        "--strategy-trade-type", "SWING",
        "--trend-fast", "50",
        "--trend-slow", "200",
        "--breakout-lookback", "10",
        "--breakout-max-distance-pct", "3.0",
        "--volume-sma", "20",
        "--min-volume-multiple", "1.5",
        "--rsi-window", "14",
        "--buy-rsi-min", "55",
        "--buy-rsi-max", "75",
        "--sell-rsi-min", "25",
        "--sell-rsi-max", "45",
        "--atr-window", "14",
        "--atr-pct-min", "1.0",
        "--atr-pct-max", "6.0",
        "--stop-atr-multiple", "1.8",
        "--target-atr-multiple", "3.6",
    ]

    scan_targets = [
        {
            "market": "india",
            "strategy_id": "india_quant_trend_breakout_on",
            "strategy_title": "India F&O + Index Radar (1-Week Breakout)",
            "extra_args": ["--nifty-futures", "--include-index-futures"],
        },
        {
            "market": "global",
            "strategy_id": "global_quant_trend_breakout_on",
            "strategy_title": "Global 1-Week Breakout Strategy",
            "extra_args": ["--include-index-futures"],
        },
        {
            "market": "commodities",
            "strategy_id": "commodities_quant_trend_breakout_on",
            "strategy_title": "Commodities 1-Week Breakout Strategy",
            "extra_args": ["--use-slope-filter", "--use-candle-filter", "--use-rsi-cross-filter"],
        },
        {
            "market": "crypto",
            "strategy_id": "crypto_quant_trend_breakout_on",
            "strategy_title": "Crypto 1-Week Breakout Strategy",
            "extra_args": [],
        },
    ]

    for target in scan_targets:
        strategy_id = target["strategy_id"]
        if strategy_id not in enabled_ids:
            continue
        _run([
            "--market", target["market"],
            *target["extra_args"],
            "--strategy-id", strategy_id,
            "--strategy-title", target["strategy_title"],
            "--strategy-market", target["market"],
            *common,
        ])


def refresh_dhan_live_strategy():
    if os.environ.get("DHAN_PILOT_ENABLED", "1").strip() == "0":
        return
    if not os.environ.get("DHAN_ACCESS_TOKEN", "").strip():
        return
    try:
        from backend.dhan_live_strategy_builder import refresh_dhan_live_strategy as _refresh_dhan_live_strategy
        _refresh_dhan_live_strategy(
            market="india",
            strategy_id="india_dhan_ema9_growth30_on",
            title="India Dhan 15m OI EMA9 Growth 30",
            symbols=os.environ.get("DHAN_PILOT_SYMBOLS", "BAJFINANCE,TITAN,SBIN").split(","),
            side=os.environ.get("DHAN_PILOT_SIDE", "bullish").strip().lower() or "bullish",
            min_range_multiple=float(os.environ.get("DHAN_PILOT_MIN_RANGE_MULTIPLE", "2.5")),
            max_range_multiple=float(os.environ.get("DHAN_PILOT_MAX_RANGE_MULTIPLE", "4.0")),
            min_reward_risk=float(os.environ.get("DHAN_PILOT_MIN_REWARD_RISK", "1.0")),
        )
        _refresh_dhan_live_strategy(
            market="commodities",
            strategy_id="commodities_ema9_growth30_on",
            title="Commodities Dhan 15m OI EMA9 Growth 30",
            symbols=os.environ.get("DHAN_COMMODITY_SYMBOLS", "GOLD,SILVER,CRUDEOIL,NATGAS,COPPER").split(","),
            side=os.environ.get("DHAN_PILOT_SIDE", "bullish").strip().lower() or "bullish",
            min_range_multiple=float(os.environ.get("DHAN_PILOT_MIN_RANGE_MULTIPLE", "2.5")),
            max_range_multiple=float(os.environ.get("DHAN_PILOT_MAX_RANGE_MULTIPLE", "4.0")),
            min_reward_risk=float(os.environ.get("DHAN_PILOT_MIN_REWARD_RISK", "1.0")),
        )
    except Exception as exc:
        print(f"[DHAN_PILOT] build failed: {exc}")


def notify_fast_dhan_strategies():
    if not FAST_DHAN_ALERTS:
        return
    try:
        strategies = load_strategies()
    except Exception:
        return
    fast_ids = {"india_dhan_ema9_growth30_on", "commodities_ema9_growth30_on"}
    fast_strategies = [s for s in strategies if str(s.get("strategy_id") or "") in fast_ids]
    if not fast_strategies:
        return
    try:
        _notify_new_strategy_trades(fast_strategies)
    except Exception as exc:
        print(f"[FAST_DHAN_ALERTS] notify failed: {exc}")


def _history_change_pct(history):
    if not history or len(history) < 2:
        return None
    try:
        prev_close = float(history[-2]["close"])
        last_close = float(history[-1]["close"])
    except Exception:
        return None
    if prev_close <= 0:
        return None
    return ((last_close - prev_close) / prev_close) * 100


def _strategy_line(trend, price, ema9, sr, allow_short=True):
    ema_daily = ema9.get("ema9_daily") if isinstance(ema9, dict) else None
    support_near = sr.get("support_near") if isinstance(sr, dict) else None
    resistance_near = sr.get("resistance_near") if isinstance(sr, dict) else None
    if trend == "PRIMARY_UPTREND":
        entry = f"Buy on pullback near EMA9 {ema_daily:.2f}" if isinstance(ema_daily, (int, float)) else "Buy on pullback"
        sl = f"SL {support_near:.2f}" if isinstance(support_near, (int, float)) else "SL below support"
        t1 = f"T1 {resistance_near:.2f}" if isinstance(resistance_near, (int, float)) else "T1 at recent high"
        return f"Action: {entry} | {sl} | {t1}"
    if trend == "PRIMARY_DOWNTREND":
        if not allow_short:
            return "Action: Avoid shorts — wait for strength"
        entry = f"Sell on bounce near EMA9 {ema_daily:.2f}" if isinstance(ema_daily, (int, float)) else "Sell on bounce"
        sl = f"SL {resistance_near:.2f}" if isinstance(resistance_near, (int, float)) else "SL above resistance"
        t1 = f"T1 {support_near:.2f}" if isinstance(support_near, (int, float)) else "T1 at recent low"
        return f"Action: {entry} | {sl} | {t1}"
    return "Action: Range mode — wait for breakout"


def _gap_action_line(direction, sr):
    support_near = sr.get("support_near") if isinstance(sr, dict) else None
    support_major = sr.get("support_major") if isinstance(sr, dict) else None
    resistance_near = sr.get("resistance_near") if isinstance(sr, dict) else None
    resistance_major = sr.get("resistance_major") if isinstance(sr, dict) else None
    if direction == "GAP UP":
        entry = f"Buy on pullback near {support_near:.2f}" if isinstance(support_near, (int, float)) else "Buy on pullback"
        sl = f"SL {support_major:.2f}" if isinstance(support_major, (int, float)) else "SL below support"
        t1 = f"T1 {resistance_near:.2f} (Tech)" if isinstance(resistance_near, (int, float)) else "T1 at recent high"
        return f"Action: {entry} | {sl} | {t1}"
    entry = f"Sell on bounce near {resistance_near:.2f}" if isinstance(resistance_near, (int, float)) else "Sell on bounce"
    sl = f"SL {resistance_major:.2f}" if isinstance(resistance_major, (int, float)) else "SL above resistance"
    t1 = f"T1 {support_near:.2f} (Tech)" if isinstance(support_near, (int, float)) else "T1 at recent low"
    return f"Action: {entry} | {sl} | {t1}"


def _build_market_strategy(market, title, assets, allow_short=True):
    items = []
    for key in assets:
        v = output.get(key)
        if not v:
            continue
        history = v.get("history") or []
        change = _history_change_pct(history)
        trend = v.get("trend") or "UNKNOWN"
        price = v.get("current_price")
        line = _strategy_line(
            trend,
            price,
            v.get("ema9") or {},
            v.get("support_resistance") or {},
            allow_short=allow_short
        )
        items.append({
            "ticker": key,
            "name": key,
            "lines": [line],
            "score": abs(change) if change is not None else 0
        })
    items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:5]
    return {
        "strategy_id": f"{market}_trend_setups",
        "title": title,
        "owner": "HARSHIT",
        "trade_type": "SWING",
        "market": market,
        "notes": ["Top setups based on trend + EMA9 + support/resistance."],
        "items": items
    }


def _market_regime_side(index_key):
    index_data = output.get(index_key) or {}
    ema = (index_data.get("ema9") or {}).get("ema9_daily")
    price = index_data.get("current_price")
    if not isinstance(ema, (int, float)) or not isinstance(price, (int, float)):
        return "NEUTRAL", None, None
    if price > ema:
        return "BUY", price, ema
    if price < ema:
        return "SELL", price, ema
    return "NEUTRAL", price, ema


def _build_regime_ema9_strategy(market, title, assets, regime_index):
    side, regime_price, regime_ema = _market_regime_side(regime_index)
    items = []

    for key in assets:
        v = output.get(key) or {}
        ema = (v.get("ema9") or {}).get("ema9_daily")
        price = v.get("current_price")
        trend = str(v.get("trend") or "")
        if not isinstance(ema, (int, float)) or not isinstance(price, (int, float)):
            continue

        aligned = (
            (side == "BUY" and price > ema)
            or (side == "SELL" and price < ema)
        )
        if side == "NEUTRAL" or not aligned:
            continue

        sr = v.get("support_resistance") or {}
        if side == "BUY":
            sl = sr.get("support_near")
            t1 = sr.get("resistance_near")
            line = (
                f"BUY bias | Entry near EMA9 {ema:.2f} | "
                f"SL {sl:.2f} | T1 {t1:.2f}"
                if isinstance(sl, (int, float)) and isinstance(t1, (int, float))
                else f"BUY bias | Entry near EMA9 {ema:.2f}"
            )
        else:
            sl = sr.get("resistance_near")
            t1 = sr.get("support_near")
            line = (
                f"SELL bias | Entry near EMA9 {ema:.2f} | "
                f"SL {sl:.2f} | T1 {t1:.2f}"
                if isinstance(sl, (int, float)) and isinstance(t1, (int, float))
                else f"SELL bias | Entry near EMA9 {ema:.2f}"
            )

        dist_pct = abs((price - ema) / ema) * 100 if ema else 999
        trend_bonus = 1 if (
            (side == "BUY" and trend == "PRIMARY_UPTREND")
            or (side == "SELL" and trend == "PRIMARY_DOWNTREND")
        ) else 0
        score = max(0.0, 10.0 - dist_pct) + trend_bonus

        items.append({
            "ticker": key,
            "name": key,
            "lines": [
                line,
                f"Price {price:.2f} | EMA9 {ema:.2f} | Dist {dist_pct:.2f}%"
            ],
            "score": score
        })

    items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:5]
    notes = [
        f"Regime: {regime_index} {'above' if side == 'BUY' else 'below' if side == 'SELL' else 'at'} EMA9.",
        "Rule: Buy setups only in BUY regime; Sell setups only in SELL regime.",
    ]
    if isinstance(regime_price, (int, float)) and isinstance(regime_ema, (int, float)):
        notes.append(f"{regime_index} price {regime_price:.2f} vs EMA9 {regime_ema:.2f}.")

    return {
        "strategy_id": f"{market}_ema9_regime_setups",
        "title": title,
        "owner": "HARSHIT",
        "trade_type": "SWING",
        "market": market,
        "notes": notes,
        "items": items
    }


def _build_gap_strategy(market, title, assets, gap_threshold, direction_filter="both"):
    items = []
    for key in assets:
        df = _load_index_cached(key)
        if df is None or len(df) < 2:
            continue
        try:
            prev_close = float(df["close"].iloc[-2])
            today_open = float(df["open"].iloc[-1])
            today_close = float(df["close"].iloc[-1])
            gap_date = df["date"].iloc[-1]
        except Exception:
            continue
        if prev_close <= 0:
            continue
        gap_pct = (today_open - prev_close) / prev_close * 100
        if abs(gap_pct) < gap_threshold:
            continue
        if direction_filter == "up" and gap_pct <= 0:
            continue
        if direction_filter == "down" and gap_pct >= 0:
            continue
        v = output.get(key, {})
        sr = v.get("support_resistance") or {}
        direction = "GAP UP" if gap_pct > 0 else "GAP DOWN"
        action = _gap_action_line(direction, sr)
        if not action:
            action = "Action: Gap alert — wait for confirmation"
        if hasattr(gap_date, "date"):
            gap_date = gap_date.date()
        items.append({
            "ticker": key,
            "name": key,
            "lines": [
                f"{direction} {gap_pct:.2f}% on {gap_date}",
                action
            ],
            "score": abs(gap_pct)
        })
    items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:5]
    return {
        "strategy_id": f"{market}_gap_setups",
        "title": title,
        "owner": "HARSHIT",
        "trade_type": "SWING",
        "market": market,
        "notes": [f"Top gaps above {gap_threshold:.1f}% (open vs prev close)."],
        "items": items
    }


def process_full(name, symbol, asset_type):
    if name == "GOLD" and GOLD_INR_10G_OVERRIDE:
        override = _parse_override(GOLD_INR_10G_OVERRIDE)
        if override:
            output[name] = {
                "type": asset_type,
                "trend": "INSUFFICIENT_DATA",
                "current_price": round(override, 2),
                "ranges": None,
                "risk_reward": None,
                "last_updated": NOW_UTC,
                "price_source": "OVERRIDE",
                "price_timestamp": None,
                "day_range": None,
                "ema9": None,
                "history": []
            }
            return
    if name == "SILVER" and SILVER_INR_KG_OVERRIDE:
        override = _parse_override(SILVER_INR_KG_OVERRIDE)
        if override:
            output[name] = {
                "type": asset_type,
                "trend": "INSUFFICIENT_DATA",
                "current_price": round(override, 2),
                "ranges": None,
                "risk_reward": None,
                "last_updated": NOW_UTC,
                "price_source": "OVERRIDE",
                "price_timestamp": None,
                "day_range": None,
                "ema9": None,
                "history": []
            }
            return

    if name in COMMODITIES:
        commodity_daily = None
        try:
            commodity_daily, commodity_snapshot = _fetch_dhan_commodity_daily_frame(name)
            if commodity_daily is not None and len(commodity_daily):
                _cached_dfs[name] = commodity_daily.copy()
                if commodity_snapshot and commodity_snapshot.get("price") is not None:
                    _live_prices[name] = {
                        "price": round(float(commodity_snapshot["price"]), 2),
                        "timestamp": commodity_snapshot.get("timestamp"),
                        "day_range": commodity_snapshot.get("day_range"),
                        "meta": commodity_snapshot.get("meta") or {},
                    }
                    if name in {"GOLD", "SILVER"}:
                        current = float(commodity_snapshot["price"])
                        normalized = _normalize_metal_price(name, symbol, current)
                        if normalized is not None:
                            current = normalized
                        output[name] = {
                            "type": asset_type,
                            "trend": "INSUFFICIENT_DATA",
                            "current_price": round(current, 2),
                            "ranges": None,
                            "risk_reward": None,
                            "last_updated": commodity_snapshot.get("timestamp") or NOW_UTC,
                            "price_source": "DHAN_CACHE" if (commodity_snapshot.get("meta") or {}).get("source") == "LOCAL_DHAN_CACHE" else "LIVE_INR",
                            "price_timestamp": commodity_snapshot.get("timestamp"),
                            "day_range": commodity_snapshot.get("day_range"),
                            "ema9": None,
                            "history": commodity_snapshot.get("history", []),
                        }
                        return
        except Exception:
            pass
        if commodity_daily is None or len(commodity_daily) == 0:
            return

    _maybe_fetch(name, symbol)
    df = _load_index_cached(name)

    if name in {"GOLD", "SILVER"}:
        try:
            price, ts = fetch_live_price(symbol)
            if price is not None:
                normalized = _normalize_metal_price(name, symbol, price)
                if normalized is not None:
                    price = normalized
                output[name] = {
                    "type": asset_type,
                    "trend": "INSUFFICIENT_DATA",
                    "current_price": round(price, 2),
                    "ranges": None,
                    "risk_reward": None,
                    "last_updated": ts or NOW_UTC,
                    "price_source": "LIVE_INR",
                    "price_timestamp": ts,
                    "day_range": None,
                    "ema9": None,
                    "history": []
                }
                return
        except Exception:
            pass

    if df is None or len(df) == 0:
        try:
            fetch_incremental(name, symbol)
            _cached_dfs.pop(name, None)
            df = _load_index_cached(name)
        except Exception:
            df = df

    if df is None or len(df) == 0:
        try:
            price, ts = fetch_live_price(symbol)
            if price is not None:
                normalized = _normalize_metal_price(name, symbol, price)
                if normalized is not None:
                    price = normalized
                output[name] = {
                    "type": asset_type,
                    "trend": "INSUFFICIENT_DATA",
                    "current_price": round(price, 2),
                    "ranges": None,
                    "risk_reward": None,
                    "last_updated": ts or NOW_UTC,
                    "price_source": "LIVE",
                    "price_timestamp": ts,
                    "day_range": None,
                    "ema9": None,
                    "history": []
                }
                return
        except Exception:
            pass

    if name == "SILVER" and (df is None or len(df) == 0):
        try:
            gold = output.get("GOLD", {})
            gold_price = gold.get("current_price")
            gold_source = gold.get("price_source")
            if gold_price and gold_source in {"LIVE_INR", "OVERRIDE", "FX_CONVERTED"}:
                silver_est = (gold_price * 100) / SILVER_GOLD_RATIO
                output[name] = {
                    "type": asset_type,
                    "trend": "INSUFFICIENT_DATA",
                    "current_price": round(silver_est, 2),
                    "ranges": None,
                    "risk_reward": None,
                    "last_updated": NOW_UTC,
                    "price_source": "ESTIMATE",
                    "price_timestamp": None,
                    "day_range": None,
                    "ema9": None,
                    "history": []
                }
                return
        except Exception:
            pass

    if name in {"GOLD", "SILVER"} and df is not None and len(df):
        live = _live_prices.get(name)
        last_close = float(df["close"].iloc[-1]) if len(df) else None
        if not live and symbol:
            try:
                price, ts = fetch_live_price(symbol)
                if price is not None:
                    normalized = _normalize_metal_price(name, symbol, price)
                    if normalized is not None:
                        price = normalized
                    live = {"price": price, "timestamp": ts}
                    _live_prices[name] = live
            except Exception:
                live = None
        if name == "GOLD" and not live and last_close and last_close > 0:
            rate = _get_usdinr_rate()
            if rate:
                live = {
                    "price": last_close * rate * GOLD_OZ_TO_10G,
                    "timestamp": NOW_UTC
                }
                _live_prices[name] = live
                output[name] = {
                    "type": asset_type,
                    "trend": "INSUFFICIENT_DATA",
                    "current_price": round(live["price"], 2),
                    "ranges": None,
                    "risk_reward": None,
                    "last_updated": NOW_UTC,
                    "price_source": "FX_CONVERTED",
                    "price_timestamp": None,
                    "day_range": None,
                    "ema9": None,
                    "history": []
                }
                return
        if live and last_close and last_close > 0:
            factor = float(live["price"]) / last_close
            for col in ("open", "high", "low", "close"):
                if col in df.columns:
                    df[col] = df[col].astype(float) * factor

    if df is None or len(df) == 0:
        live = _live_prices.get(name)
        if live:
            output[name] = {
                "type": asset_type,
                "trend": "INSUFFICIENT_DATA",
                "current_price": live["price"],
                "ranges": None,
                "risk_reward": None,
                "last_updated": live["timestamp"],
                "price_source": "LIVE",
                "price_timestamp": live["timestamp"],
                "day_range": None,
                "ema9": None,
                "history": []
            }
        return
    min_history = 30 if name == "INDIA_VIX" else MIN_HISTORY
    if len(df) < min_history and asset_type in ("INDIA_STOCK", "GLOBAL_STOCK", "CRYPTO"):
        return

    trend = primary_trend(df)

    ranges = None
    rr = None
    if not (FAST_MODE and asset_type in ("INDIA_STOCK", "GLOBAL_STOCK")):
        ranges = compute_ranges(df, trend)
        rr = _compute_rr_from_ranges(ranges)

    live = _live_prices.get(name)
    last_close = float(df["close"].iloc[-1]) if df is not None and len(df) else None
    live_price = live["price"] if live else None
    if asset_type == "INDIA_STOCK" and symbol and symbol.endswith(".NS"):
        base = symbol.replace(".NS", "")
        nse_price, nse_ts = fetch_nse_stock_realtime(base)
        if nse_price is not None:
            live = {"price": nse_price, "timestamp": nse_ts}
            live_price = nse_price
    validated_live = _validate_live_price(name, asset_type, live_price, last_close)
    if live and validated_live is None:
        live = None
    elif live and validated_live is not None:
        live = {
            "price": round(validated_live, 2),
            "timestamp": live.get("timestamp"),
            "day_range": (live or {}).get("day_range"),
        }
    if name in STRICT_LIVE_ONLY_NAMES and live is None:
        return
    eod_timestamp = _estimate_series_timestamp(name, asset_type, df)
    last_updated = (live or {}).get("timestamp") or eod_timestamp or NOW_UTC
    sr = compute_support_resistance(df)
    ema9 = compute_ema9_signal(df)
    current_price = float(live["price"]) if live else float(df["close"].iloc[-1])
    if name in {"GOLD", "SILVER"}:
        normalized_price = _normalize_metal_price(name, symbol, current_price)
        if normalized_price is not None:
            current_price = normalized_price
    day_range = _normalize_live_day_range((live or {}).get("day_range"), current_price)
    if day_range is None:
        day_range = _build_latest_daily_range(name, symbol, df)
    live_meta_source = ((live or {}).get("meta") or {}).get("source")

    output[name] = {
        "type": asset_type,
        "trend": trend,
        "current_price": round(current_price, 2),
        "ranges": ranges,
        "risk_reward": rr,
        "support_resistance": sr,
        "ema9": ema9,
        "last_updated": last_updated,
        "price_source": "DHAN_CACHE" if live_meta_source == "LOCAL_DHAN_CACHE" else ("LIVE" if live else "EOD"),
        "price_timestamp": (live or {}).get("timestamp") or eod_timestamp,
        "day_range": day_range,
        "history": [
            {
                "date": str(d.date()),
                "close": round(c, 2)
            }
            for d, c in zip(
                df["date"].iloc[-22:],
                df["close"].iloc[-22:]
            )
        ]
    }


def process_trend_only(name, symbol):
    if not SKIP_BREADTH_FETCH:
        _maybe_fetch(name, symbol)
    df = _load_index_cached(name)

    if df is None or len(df) < MIN_HISTORY:
        return

    output[name] = {
        "type": "STOCK_BREADTH",
        "trend": primary_trend(df),
        "current_price": round(df["close"].iloc[-1], 2)
    }


# ---------------- LIVE PRICES (OPTIONAL) ----------------
_load_live_prices()

# ---------------- MARKET ASSETS ----------------
for k, v in SYMBOLS.items():
    process_full(k, v, "INDEX")

# ---------------- SMART MONEY (KOTAK NEO) ----------------
nifty_deltas = _safe_kotak_deltas("NIFTY")

smart_money = compute_smart_money(
    "NIFTY",
    nifty_deltas
)

# Confidence downgrade if Kotak unavailable
if nifty_deltas.get("status") != "OK":
    smart_money["confidence"] = "LOW"
    smart_money["reason"] = "kotak_data_unavailable"


# ---------------- INDIA TOP STOCKS (NIFTY 50) ----------------
nifty50_symbols = get_nifty50_symbols()
for k, v in nifty50_symbols.items():
    process_full(k, v, "INDIA_STOCK")

# ---------------- BANKNIFTY / SENSEX CONSTITUENTS ----------------
banknifty_symbols = get_niftybank_symbols()
sensex_symbols = get_sensex_symbols()

# ---------------- GLOBAL TOP STOCKS ----------------
for k, v in GLOBAL_STOCKS.items():
    process_full(k, v, "GLOBAL_STOCK")

# ---------------- CRYPTO ----------------
for k, v in CRYPTO.items():
    process_full(k, v, "CRYPTO")

# ---------------- NIFTY 50 TREND COUNTS ----------------
nifty50_trends = {"bullish": 0, "bearish": 0, "range": 0, "total": 0}
for k in nifty50_symbols.keys():
    trend = output.get(k, {}).get("trend")
    if not trend:
        continue
    nifty50_trends["total"] += 1
    if trend == "PRIMARY_UPTREND":
        nifty50_trends["bullish"] += 1
    elif trend == "PRIMARY_DOWNTREND":
        nifty50_trends["bearish"] += 1
    else:
        nifty50_trends["range"] += 1

global_trends = {"bullish": 0, "bearish": 0, "range": 0, "total": 0}
for k in GLOBAL_STOCKS.keys():
    trend = output.get(k, {}).get("trend")
    if not trend:
        continue
    global_trends["total"] += 1
    if trend == "PRIMARY_UPTREND":
        global_trends["bullish"] += 1
    elif trend == "PRIMARY_DOWNTREND":
        global_trends["bearish"] += 1
    else:
        global_trends["range"] += 1

commodity_trends = {"bullish": 0, "bearish": 0, "range": 0, "total": 0}
for k in COMMODITIES:
    trend = output.get(k, {}).get("trend")
    if not trend:
        continue
    commodity_trends["total"] += 1
    if trend == "PRIMARY_UPTREND":
        commodity_trends["bullish"] += 1
    elif trend == "PRIMARY_DOWNTREND":
        commodity_trends["bearish"] += 1
    else:
        commodity_trends["range"] += 1

india_index_gainers = _gainers_losers_for_symbols(nifty50_symbols)
banknifty_gainers = _gainers_losers_for_symbols(banknifty_symbols)
sensex_gainers = _gainers_losers_for_symbols(sensex_symbols)
india_overall_gainers = _gainers_losers_from_output("INDIA_STOCK")
global_constituents = get_global_index_constituents()
global_gl_cache = _load_metric_cache("global_index_gl", GLOBAL_GL_CACHE_TTL_MIN)
if global_gl_cache and isinstance(global_gl_cache.get("detail"), dict):
    global_index_detail = global_gl_cache.get("detail", {})
    global_index_gainers = global_gl_cache.get("aggregate", {})
else:
    global_index_detail = {
        name: _gainers_losers_for_tickers(tickers)
        for name, tickers in global_constituents.items()
    }
    global_index_gainers = _aggregate_gainers_losers(global_index_detail)
    _save_metric_cache(
        "global_index_gl",
        {"detail": global_index_detail, "aggregate": global_index_gainers}
    )
global_overall_gainers = _gainers_losers_from_output("GLOBAL_STOCK")
crypto_overall_gainers = _gainers_losers_from_output("CRYPTO")

# ---------------- NIFTY 500 (BREADTH ONLY) ----------------
dhan_breadth_payload = build_dhan_breadth("india")
breadth = dhan_breadth_payload.get("breadth") if isinstance(dhan_breadth_payload, dict) else {}
if not isinstance(breadth, dict) or not breadth:
    up = down = side = 0
    breadth_cache = _load_metric_cache("nifty500_breadth", BREADTH_CACHE_TTL_MIN)
    if breadth_cache and isinstance(breadth_cache.get("breadth"), dict):
        breadth = breadth_cache.get("breadth", {})
    else:
        for k, v in NIFTY500.items():
            process_trend_only(k, v)
            t = output.get(k, {}).get("trend")

            if t == "PRIMARY_UPTREND":
                up += 1
            elif t == "PRIMARY_DOWNTREND":
                down += 1
            else:
                side += 1

        total = up + down + side
        breadth = {
            "up_pct": round((up / total) * 100, 1) if total else 0,
            "down_pct": round((down / total) * 100, 1) if total else 0,
            "sideways_pct": round((side / total) * 100, 1) if total else 0
        }
        _save_metric_cache("nifty500_breadth", {"breadth": breadth})


# ---------------- CHANGE & LEADERSHIP ----------------
DATA_DIR.mkdir(parents=True, exist_ok=True)

change_snapshot_path = SNAPSHOT_PATH

yesterday = {}
if change_snapshot_path.exists():
    yesterday = _load_json_file(change_snapshot_path, default={}, label="change snapshot")

context_change = []

if yesterday.get("breadth_up_pct") is not None:
    delta = round(breadth["up_pct"] - yesterday["breadth_up_pct"], 1)
    if delta > 0:
        context_change.append(f"Breadth up from {yesterday['breadth_up_pct']}% to {breadth['up_pct']}%")
    elif delta < 0:
        context_change.append(f"Breadth down from {yesterday['breadth_up_pct']}% to {breadth['up_pct']}%")

# Save today's snapshot for tomorrow
_write_json_atomic(change_snapshot_path, {
    "breadth_up_pct": breadth["up_pct"]
})


# --- LEADERSHIP CHECK (NIFTY50 only, reliable)
sector_map = {
    "HDFCBANK": "FINANCIALS",
    "ICICIBANK": "FINANCIALS",
    "SBIN": "FINANCIALS",
    "INFY": "IT",
    "TCS": "IT",
    "WIPRO": "IT",
    "ITC": "FMCG",
    "HINDUNILVR": "FMCG",
    "RELIANCE": "ENERGY"
}

sector_trends = []

for stock, meta in output.items():
    if stock in sector_map and meta.get("trend"):
        sector_trends.append(
            (sector_map[stock], meta["trend"])
        )

sector_counter = Counter(sector_trends)

leadership = []

for sector in set(sector_map.values()):
    up = sector_counter.get((sector, "PRIMARY_UPTREND"), 0)
    down = sector_counter.get((sector, "PRIMARY_DOWNTREND"), 0)

    if up >= 2:
        leadership.append(f"{sector}: leadership intact")
    elif down >= 2:
        leadership.append(f"{sector}: weakening")


# ---------------- DOW CONFIRMATION ----------------
confirmation = dow_confirmation(
    output.get("NIFTY", {}).get("trend"),
    output.get("BANKNIFTY", {}).get("trend")
)


# ---------------- MARKET REGIME (SAFE HEURISTIC) ----------------
vix_level = output.get("INDIA_VIX", {}).get("current_price", 0)

if confirmation == "CONFIRMED" and vix_level < 14 and breadth["up_pct"] > 60:
    regime = {
        "regime": "TRENDING",
        "volatility": "LOW",
        "confidence": "HIGH"
    }
elif vix_level > 18:
    regime = {
        "regime": "DISTRIBUTION",
        "volatility": "HIGH",
        "confidence": "HIGH"
    }
else:
    regime = {
        "regime": "RANGE / TRANSITION",
        "volatility": "MODERATE",
        "confidence": "MEDIUM"
    }


# ---------------- ACTION GUIDANCE ----------------
if regime["regime"] == "TRENDING":
    action_guidance = [
        "Favor trend continuation trades",
        "Buy pullbacks, avoid chasing breakouts",
        "Avoid counter-trend shorts"
    ]
elif regime["regime"] == "DISTRIBUTION":
    action_guidance = [
        "Reduce position size",
        "Avoid aggressive longs",
        "Focus on capital protection"
    ]
else:
    action_guidance = [
        "Trade lighter",
        "Prefer mean reversion setups",
        "Avoid leverage"
    ]

# ---------------- TIMESTAMP ----------------
now = NOW_IST

# ---------------- DAILY INTELLIGENCE ----------------
daily_intelligence = build_daily_intelligence(
    nifty_trend=output.get("NIFTY", {}).get("trend"),
    regime=regime,
    breadth=breadth,
    ranges=output.get("NIFTY", {}).get("ranges"),
    vix_today=output.get("INDIA_VIX", {}).get("current_price"),
    india_top_trends=nifty50_trends,
    global_top_trends=global_trends
)

nifty_df = _cached_dfs.get("NIFTY")
if nifty_df is None:
    nifty_df = load_index("NIFTY")
vix_val = output.get("INDIA_VIX", {}).get("current_price")

market_regime = detect_market_regime(nifty_df, vix_value=vix_val)

# ---------------- EVENTS (BACKEND-ONLY, SAFE) ----------------
raw_events = []

event_profiles = []
for e in raw_events:
    event_profiles.append(build_event_profile(e))

event_trigger = detect_event_driven_move({
    "vix_jump": output.get("INDIA_VIX", {}).get("current_price", 0) > 20,
    "volume_spike": abs(nifty_deltas.get("volume_delta", 0)) > 0.25,
    "oi_dislocation": abs(nifty_deltas.get("oi_delta", 0)) > 0.15,
    "correlation_break": False
})

# ---------------- EXECUTIVE SUMMARY ----------------
risk_trend = (
    "Risk contracting" if breadth["up_pct"] > 60 and vix_level < 14
    else "Risk expanding / uncertain"
)
market_health = build_market_health(
    output,
    breadth,
    vix_level,
    confirmation,
    regime,
    leadership
)
executive_summary = build_executive_summary(
    regime,
    confirmation,
    breadth,
    action_guidance,
    risk_trend,
    market_health=market_health,
    vix_level=vix_level
)


# ---------------- FINAL PAYLOAD ----------------
refresh_dhan_live_strategy()
notify_fast_dhan_strategies()
if FAST_DHAN_ONLY:
    print("[FAST_DHAN_ONLY] exiting after Dhan pilot refresh")
    raise SystemExit(0)
run_intraday_momentum_scan()
run_gold_breakout_retest_scan()
run_ema9_growth30_scan()
_publish_strategy_candidate_pool("india_ema9_growth30_on", lookback_days=STRATEGY_CANDIDATE_POOL_LOOKBACK_DAYS)
run_quant_trend_breakout_scan()
strategies = load_strategies()
_notify_new_strategy_trades(strategies)
if _strategy_passes_cagr_gate("global_gap_setups"):
    strategies.append(
        _build_gap_strategy(
            "global",
            "Global Gap Setups",
            list(GLOBAL_STOCKS.keys()),
            GLOBAL_GAP_THRESHOLD_PCT,
            direction_filter="up"
        )
    )
else:
    print("[CAGR_GATE] skip=synthetic sid=global_gap_setups")
if _strategy_passes_cagr_gate("commodities_trend_setups"):
    strategies.append(
        _build_market_strategy("commodities", "Commodities Swing Setups", list(COMMODITIES.keys()), allow_short=False)
    )
else:
    print("[CAGR_GATE] skip=synthetic sid=commodities_trend_setups")
if _strategy_passes_cagr_gate("crypto_trend_setups"):
    strategies.append(
        _build_market_strategy("crypto", "Crypto Swing Setups", list(CRYPTO.keys()), allow_short=False)
    )
else:
    print("[CAGR_GATE] skip=synthetic sid=crypto_trend_setups")
strategies = _filter_strategies_by_cagr(strategies)

final = {
    "generated_at": now,
    "dow_confirmation": confirmation,
    "regime": regime,
    "action_guidance": action_guidance,
    "breadth": breadth,
    "macro": macro_context(),
    "news": _safe_fetch_news(),
    "daily_intelligence": daily_intelligence,
    "data": output,
    "context_change": context_change,
    "leadership": leadership,
    "market_regime": market_regime,
    "smart_money": smart_money,
    "executive_summary": executive_summary,
    "market_health": market_health,
    "top_trades": load_top_trades(),
    "strategies": strategies,
    "nifty50_trends": nifty50_trends,
    "global_trends": global_trends,
    "commodity_trends": commodity_trends,
    "gainers_losers": {
        "india_nifty50": india_index_gainers,
        "india_banknifty": banknifty_gainers,
        "india_sensex": sensex_gainers,
        "india_overall": india_overall_gainers,
        "global_indices": global_index_gainers,
        "global_indices_detail": global_index_detail,
        "global_overall": global_overall_gainers,
        "crypto_overall": crypto_overall_gainers
    },
    "event_context": {
        "trigger": event_trigger,
        "profiles": event_profiles
    },
    "risk_trend": risk_trend,
}


# ---------------- DATA FRESHNESS TAGGING ----------------
for _, v in output.items():
    attach_freshness(
        v,
        v.get("last_updated"),
        DEFAULT_THRESHOLDS["prices_slow"]
    )


# ---------------- WRITE FILE ----------------
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
safe_final = _sanitize_json_value(final)
tmp_path = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(DATA_PATH.parent),
        prefix=f"{DATA_PATH.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        json.dump(safe_final, f, indent=2, allow_nan=False)
        tmp_path = Path(f.name)
    os.chmod(tmp_path, 0o644)
    tmp_path.replace(DATA_PATH)
    os.chmod(DATA_PATH, 0o644)
finally:
    if tmp_path and tmp_path.exists():
        try:
            tmp_path.unlink()
        except Exception:
            pass

_publish_dashboard_snapshot_store(safe_final)
_publish_commodity_snapshot_store(safe_final)
print("Daily data updated - stable & production-safe")
