from __future__ import annotations

import argparse
import concurrent.futures
import csv
import gc
import json
import logging
import math
import os
import sys
import time
from functools import lru_cache
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import requests
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency fallback for local smoke tests
    def load_dotenv(*args, **kwargs):  # type: ignore[override]
        return False


ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT
STRATEGY_DIR = ROOT / "strategies"
IST = ZoneInfo("Asia/Kolkata")
DHAN_BASE_URL = "https://api.dhan.co/v2"
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)
DEFAULT_BUFFER_SECONDS = 1.5
DEFAULT_SCAN_WORKERS = int(os.environ.get("DHAN_SCAN_WORKERS", "1"))
DEFAULT_FAST_STRIKE_WINDOW = int(os.environ.get("DHAN_FAST_STRIKE_WINDOW", "3"))
DEFAULT_INTRADAY_RETRIES = int(os.environ.get("DHAN_INTRADAY_RETRIES", "3"))
DEFAULT_LIVE_LOOKBACK_DAYS = int(os.environ.get("DHAN_LIVE_LOOKBACK_DAYS", "10"))
DEFAULT_LIVE_OPTION_LOOKBACK_DAYS = int(os.environ.get("DHAN_LIVE_OPTION_LOOKBACK_DAYS", "1"))
DEFAULT_CANDLE_HISTORY_FETCH_DAYS = int(os.environ.get("DHAN_CANDLE_HISTORY_FETCH_DAYS", "1"))
DEFAULT_CANDLE_HISTORY_RETENTION_MULTIPLIER = float(os.environ.get("DHAN_CANDLE_HISTORY_RETENTION_MULTIPLIER", "2"))
DEFAULT_EQUITY_CACHE_REFRESH_DAYS = int(os.environ.get("DHAN_EQUITY_HISTORY_REFRESH_DAYS", "2"))
DEFAULT_OPTION_CHAIN_WORKERS = int(os.environ.get("DHAN_OPTION_CHAIN_WORKERS", "1"))
DEFAULT_BODY_MULTIPLIER = float(os.environ.get("DHAN_BODY_MULTIPLIER", "5.5"))
DEFAULT_SOURCE_STRATEGY_ID = os.environ.get("DHAN_WATCH_STRATEGY_ID", "india_ema9_growth30_on").strip() or "india_ema9_growth30_on"
DEFAULT_SOURCE_STRATEGY_LOOKBACK_DAYS = int(os.environ.get("DHAN_WATCH_STRATEGY_LOOKBACK_DAYS", "4"))
DEFAULT_STRATEGY_NAME = os.environ.get("DHAN_PATTERN_STRATEGY_NAME", "Pattern+OI+VWAP/EMA").strip() or "Pattern+OI+VWAP/EMA"
DEFAULT_SCAN_UNIVERSE = os.environ.get("PATTERN_OI_VWAP_EMA_UNIVERSE", "all").strip().lower() or "all"
FNO_CACHE_PATH = ROOT / "strategies" / ".fno_cache.json"
EQUITY_HISTORY_CACHE_DIR = ROOT / "backend" / "data" / "dhan_equity_cache"
STRATEGY_CANDIDATE_POOL_DIR = ROOT / "backend" / "data" / "strategy_candidate_pools"
LOCAL_DHAN_MASTER_CACHE = ROOT.parent / "market-context-local-data" / "dhan_scrip_master_cache.csv"
MANUAL_DHAN_SECURITY_MAP_FILE = ROOT / "backend" / "data" / "manual_dhan_security_map.json"
OUTPUT_DIR = ROOT / "backend" / "reports" / "pattern_oi_vwap_ema"

if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from backend.agentic_pipeline import format_single_agent_group_message
from backend.suggest_security import safe_telegram_text
try:
    from backend.market_snapshot_store import MarketSnapshotStore
except Exception:  # pragma: no cover - optional for older environments
    MarketSnapshotStore = None

try:
    from strategies.apollo_ema9_strategy import NIFTY50_TICKERS  # type: ignore
except Exception:
    NIFTY50_TICKERS = (
        "ADANIPORTS.NS",
        "APOLLOHOSP.NS",
        "ASIANPAINT.NS",
        "AXISBANK.NS",
        "BAJAJ-AUTO.NS",
        "BAJFINANCE.NS",
        "BAJAJFINSV.NS",
        "BPCL.NS",
        "BHARTIARTL.NS",
        "BRITANNIA.NS",
        "CIPLA.NS",
        "COALINDIA.NS",
        "DIVISLAB.NS",
        "DRREDDY.NS",
        "EICHERMOT.NS",
        "GRASIM.NS",
        "HCLTECH.NS",
        "HDFCBANK.NS",
        "HDFCLIFE.NS",
        "HEROMOTOCO.NS",
        "HINDALCO.NS",
        "HINDUNILVR.NS",
        "ICICIBANK.NS",
        "INDUSINDBK.NS",
        "INFY.NS",
        "ITC.NS",
        "JSWSTEEL.NS",
        "KOTAKBANK.NS",
        "LT.NS",
        "M&M.NS",
        "MARUTI.NS",
        "NESTLEIND.NS",
        "NTPC.NS",
        "ONGC.NS",
        "POWERGRID.NS",
        "RELIANCE.NS",
        "SBILIFE.NS",
        "SBIN.NS",
        "SUNPHARMA.NS",
        "TATACONSUM.NS",
        "TATAMOTORS.NS",
        "TATASTEEL.NS",
        "TCS.NS",
        "TECHM.NS",
        "TITAN.NS",
        "ULTRACEMCO.NS",
        "UPL.NS",
        "WIPRO.NS",
        "SHREECEM.NS",
        "SHRIRAMFIN.NS",
    )

from backend.dhan_intraday import (  # type: ignore
    _fetch_dhan_scrip_master,
    _dhan_request,
    _interval_to_dhan,
    _normalize_exchange_segment,
    _parse_epoch_to_utc,
    _parse_expiry_date,
    _pick_col,
    _range_to_days,
    resolve_contract_candidates,
)


logger = logging.getLogger("backend.pattern_oi_vwap_ema_scanner")

WATCHLIST: dict[str, int | None] = {
    "SHRIRAMFIN": 10322,
    "TRENT": 1948,
    "JSWENERGY": 13353,
}
SCANNED_SIGNALS_CSV = OUTPUT_DIR / "scanned_signals.csv"
GATE3_STATE_PATH = OUTPUT_DIR / "dhan_gate3_state.json"
GATE3_DRY_RUN_LOG = OUTPUT_DIR / "gate3_telegram_dry_run.log"
GATE3_REPLAY_OUTPUT = OUTPUT_DIR / "dhan_gate3_replay.json"
REPEAT_PATTERN_STATE_PATH = OUTPUT_DIR / "repeat_pattern_state.json"
DEFAULT_STORE_TIMEFRAME = os.environ.get("DHAN_STORE_TIMEFRAME", "15m").strip() or "15m"
STORE_PUBLISH_ENABLED = os.environ.get("DHAN_STORE_PUBLISH_ENABLED", "1") == "1"
DEFAULT_SETUP_ALERTS = os.environ.get("DHAN_SETUP_ALERTS", "1") == "1"
DEFAULT_REPEAT_PATTERN_ALERTS = os.environ.get("DHAN_REPEAT_PATTERN_ALERTS", "0") == "1"
INDIA_INDEX_SCAN_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")


def _load_env_files() -> None:
    load_dotenv(ROOT.parent / ".env", override=False)
    load_dotenv(ROOT / "backend" / ".env", override=False)


def _load_json_file(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp_path.replace(path)


def _load_gate3_state() -> dict[str, Any]:
    state = _load_json_file(GATE3_STATE_PATH, default={})
    return state if isinstance(state, dict) else {}


def _save_gate3_state(payload: dict[str, Any]) -> None:
    _write_json_atomic(GATE3_STATE_PATH, payload)


def _load_repeat_pattern_state() -> dict[str, Any]:
    state = _load_json_file(REPEAT_PATTERN_STATE_PATH, default={})
    return state if isinstance(state, dict) else {}


def _save_repeat_pattern_state(payload: dict[str, Any]) -> None:
    _write_json_atomic(REPEAT_PATTERN_STATE_PATH, payload)


def _publish_market_snapshot_store(payload: dict[str, Any], timeframe: str) -> None:
    if not STORE_PUBLISH_ENABLED or MarketSnapshotStore is None:
        return
    try:
        store = MarketSnapshotStore()
        store.write_payload(payload, timeframe=timeframe)
    except Exception as exc:
        logger.warning("Market snapshot store publish failed for %s: %s", timeframe, exc)


def _telegram_config() -> tuple[str | None, str | None, str | None]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
    group_chat_id = (
        os.getenv("TELEGRAM_TRADE_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or ""
    ).strip() or None
    personal_chat_id = (
        os.getenv("TELEGRAM_PERSONAL_CHAT_ID")
        or os.getenv("TELEGRAM_STATUS_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or ""
    ).strip() or None
    return token, group_chat_id, personal_chat_id


def _send_telegram_to(chat_id: str | None, message: str) -> bool:
    token, _, _ = _telegram_config()
    if not token or not chat_id:
        logger.warning("Telegram is not configured; skipping alert.")
        return False
    text = safe_telegram_text(message, max_len=3500)
    if os.getenv("TELEGRAM_DRY_RUN", "").strip() == "1":
        GATE3_DRY_RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with GATE3_DRY_RUN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n\n---\n\n")
        logger.info("Telegram dry-run captured to %s", GATE3_DRY_RUN_LOG)
        return True
    base_url = str(os.getenv("TELEGRAM_API_BASE_URL") or "https://api.telegram.org").rstrip("/")
    try:
        resp = requests.post(
            f"{base_url}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.warning("Telegram send failed: %s", resp.text[:240])
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram send exception: %s", exc)
        return False


def _is_fresh_gate3(previous: dict[str, str] | None, current: dict[str, str]) -> bool:
    if not previous:
        return True
    # Keep Gate 3 from re-alerting every 15m while the same setup stays valid.
    # We only treat it as fresh when the symbol/pattern/direction/day bucket changes.
    keys = ("symbol", "direction", "pattern", "signal_day")
    for key in keys:
        if str(previous.get(key) or "") != str(current.get(key) or ""):
            return True
    return False


def _is_fresh_gate12(previous: dict[str, str] | None, current: dict[str, str]) -> bool:
    if not previous:
        return True
    # Keep Gate 1/2 from spamming the same symbol on every new candle.
    # Only alert again if the symbol/pattern/direction/day bucket changes.
    keys = ("symbol", "direction", "pattern", "signal_day")
    for key in keys:
        if str(previous.get(key) or "") != str(current.get(key) or ""):
            return True
    return False


def _gate3_trigger_signature(symbol: str, snapshot: "SymbolSnapshot", strategy: dict[str, Any]) -> dict[str, str]:
    candle_day = ""
    if snapshot.candle_time_ist:
        candle_day = str(snapshot.candle_time_ist)[:10]
    return {
        "symbol": str(symbol or "").strip().upper(),
        "direction": str(strategy.get("direction") or "NEUTRAL").upper(),
        "pattern": str(strategy.get("gate1_pattern") or "UNAVAILABLE"),
        "signal_day": candle_day,
        "candle_time_ist": str(snapshot.candle_time_ist or ""),
        "pcr": str(strategy.get("pcr") or ""),
        "pcr_prev": str(strategy.get("pcr_prev") or ""),
        "put_velocity": str(strategy.get("put_oi_velocity_pct") or ""),
        "call_velocity": str(strategy.get("call_oi_velocity_pct") or ""),
    }


def _gate3_trigger_signature_from_snapshot_record(symbol: str, snapshot_record: dict[str, Any]) -> dict[str, str]:
    strategy = snapshot_record.get("strategy") if isinstance(snapshot_record.get("strategy"), dict) else {}
    candle_time = str(snapshot_record.get("candle_time_ist") or "")
    return {
        "symbol": str(symbol or "").strip().upper(),
        "direction": str(strategy.get("direction") or "NEUTRAL").upper(),
        "pattern": str(strategy.get("pattern") or strategy.get("gate1_pattern") or "UNAVAILABLE"),
        "signal_day": candle_time[:10],
        "candle_time_ist": candle_time,
        "pcr": str(strategy.get("pcr") or ""),
        "pcr_prev": str(strategy.get("pcr_prev") or ""),
        "put_velocity": str(strategy.get("put_oi_velocity_pct") or ""),
        "call_velocity": str(strategy.get("call_oi_velocity_pct") or ""),
    }


def _gate12_trigger_signature(symbol: str, snapshot: "SymbolSnapshot", strategy: dict[str, Any]) -> dict[str, str]:
    candle_day = ""
    if snapshot.candle_time_ist:
        candle_day = str(snapshot.candle_time_ist)[:10]
    return {
        "symbol": str(symbol or "").strip().upper(),
        "direction": str(strategy.get("direction") or "NEUTRAL").upper(),
        "pattern": str(strategy.get("gate1_pattern") or "UNAVAILABLE"),
        "signal_day": candle_day,
        "candle_time_ist": str(snapshot.candle_time_ist or ""),
        "close": str(snapshot.close or ""),
        "vwap": str(snapshot.vwap or ""),
        "ema9": str(snapshot.ema9 or ""),
    }


def _gate12_trigger_signature_from_snapshot_record(symbol: str, snapshot_record: dict[str, Any]) -> dict[str, str]:
    strategy = snapshot_record.get("strategy") if isinstance(snapshot_record.get("strategy"), dict) else {}
    candle_time = str(snapshot_record.get("candle_time_ist") or "")
    return {
        "symbol": str(symbol or "").strip().upper(),
        "direction": str(strategy.get("direction") or "NEUTRAL").upper(),
        "pattern": str(strategy.get("pattern") or strategy.get("gate1_pattern") or "UNAVAILABLE"),
        "signal_day": candle_time[:10],
        "candle_time_ist": candle_time,
        "close": str(snapshot_record.get("close") or ""),
        "vwap": str(snapshot_record.get("vwap") or ""),
        "ema9": str(snapshot_record.get("ema9") or ""),
    }


def _get_credentials() -> tuple[str, str]:
    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token:
        logger.critical("Missing DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN in environment.")
        raise SystemExit(1)
    return client_id.strip(), access_token.strip()


def _preflight_dhan_token(
    client: "DhanRealtimeClient",
    symbol: str,
    market: str = "india",
    interval: str = "15m",
) -> None:
    try:
        probe_end = _previous_trading_close()
        client.fetch_equity_history(
            symbol,
            interval=interval,
            data_range="1d",
            market=market,
            end_ist=probe_end,
        )
        print("Dhan token valid.")
    except Exception as exc:
        text = str(exc)
        if "Invalid_Authentication" in text or "DH-901" in text or "401" in text:
            print("Dhan token expired or invalid.")
        else:
            print(f"Dhan token validation failed: {text}")
        raise SystemExit(1)


def _floor_to_15_minute(dt: datetime) -> datetime:
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def _previous_trading_close(now: datetime | None = None) -> datetime:
    now = now or datetime.now(IST)
    probe_day = now
    if probe_day.weekday() >= 5:
        probe_day = probe_day - timedelta(days=1)
    while probe_day.weekday() >= 5:
        probe_day -= timedelta(days=1)
    return probe_day.replace(
        hour=MARKET_CLOSE.hour,
        minute=MARKET_CLOSE.minute,
        second=0,
        microsecond=0,
    )


def _next_15_minute_boundary(dt: datetime) -> datetime:
    floored = _floor_to_15_minute(dt)
    if dt == floored:
        return floored + timedelta(minutes=15)
    return floored + timedelta(minutes=15)


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _market_day_bounds(dt: datetime) -> tuple[datetime, datetime]:
    open_dt = dt.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0)
    close_dt = dt.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0)
    return open_dt, close_dt


def _next_business_day_open(dt: datetime) -> datetime:
    next_day = dt + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return next_day.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0)


def _parse_as_of_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Invalid date value: {value!r}")
    return parsed.date()


def _normalize_symbol_token(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if text.endswith(".NS"):
        text = text[:-3]
    return text


def _load_recent_strategy_symbols(
    strategy_id: str,
    lookback_days: int = DEFAULT_SOURCE_STRATEGY_LOOKBACK_DAYS,
    strategies_dir: Path = STRATEGY_DIR,
) -> list[str]:
    today = datetime.now(IST).date()
    cutoff = today - timedelta(days=max(1, lookback_days) - 1)
    symbols: list[str] = []
    seen: set[str] = set()

    def _add_symbol(raw: object) -> None:
        symbol = _normalize_symbol_token(raw)
        if not symbol or symbol in seen:
            return
        seen.add(symbol)
        symbols.append(symbol)

    candidates: list[Path] = []
    current = strategies_dir / f"{strategy_id}.json"
    if current.exists():
        candidates.append(current)
    history_dir = strategies_dir / "history"
    if history_dir.exists():
        for path in sorted(history_dir.glob(f"{strategy_id}_*.json")):
            stem = path.stem
            suffix = stem[len(strategy_id) + 1 :]
            try:
                file_date = datetime.strptime(suffix, "%Y%m%d").date()
            except Exception:
                continue
            if (today - file_date).days <= max(1, lookback_days):
                candidates.append(path)

    for path in candidates:
        payload = _load_json_file(path)
        if not isinstance(payload, dict):
            continue
        strategy_generated_at = payload.get("generated_at")
        strategy_generated_date = None
        if strategy_generated_at:
            try:
                strategy_generated_date = pd.Timestamp(strategy_generated_at).tz_convert(IST).date()  # type: ignore[union-attr]
            except Exception:
                try:
                    strategy_generated_date = pd.Timestamp(strategy_generated_at).date()
                except Exception:
                    strategy_generated_date = None
        if strategy_generated_date is not None and strategy_generated_date < cutoff:
            continue
        items = payload.get("items") or payload.get("signals") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            _add_symbol(item.get("ticker") or item.get("name") or item.get("symbol"))
    return symbols


def _load_candidate_pool_symbols(strategy_id: str) -> list[str]:
    pool_path = STRATEGY_CANDIDATE_POOL_DIR / f"{str(strategy_id or '').strip() or 'unknown_strategy'}.json"
    if not pool_path.exists():
        return []
    payload = _load_json_file(pool_path, default=None)
    if not isinstance(payload, dict):
        return []
    symbols_raw = payload.get("symbols")
    symbols: list[str] = []
    seen: set[str] = set()
    if isinstance(symbols_raw, list):
        for raw in symbols_raw:
            symbol = _normalize_symbol_token(raw)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
    if symbols:
        return symbols
    items = payload.get("items") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = _normalize_symbol_token(item.get("ticker") or item.get("name") or item.get("symbol"))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _load_broad_india_universe_symbols() -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()

    def _add(raw: object) -> None:
        symbol = _normalize_symbol_token(raw)
        if not symbol or symbol in seen:
            return
        seen.add(symbol)
        symbols.append(symbol)

    for symbol in _load_fno_tickers():
        _add(symbol)
    for symbol in INDIA_INDEX_SCAN_SYMBOLS:
        _add(symbol)
    return symbols


def _load_fno_tickers(cache_path: Path = FNO_CACHE_PATH) -> list[str]:
    if not cache_path.exists():
        return []
    try:
        payload = json.loads(cache_path.read_text())
    except Exception:
        return []
    items = payload.get("constituents") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    tickers: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = _normalize_symbol_token(item.get("ticker") or item.get("symbol") or "")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        tickers.append(symbol)
    return tickers


@lru_cache(maxsize=1)
def _load_manual_security_map() -> dict[str, int]:
    if not MANUAL_DHAN_SECURITY_MAP_FILE.exists():
        return {}
    try:
        data = json.loads(MANUAL_DHAN_SECURITY_MAP_FILE.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in data.items():
        symbol = _normalize_symbol_token(key)
        try:
            out[symbol] = int(value)
        except Exception:
            continue
    return out


@lru_cache(maxsize=1)
def _load_cached_dhan_scrip_master() -> pd.DataFrame | None:
    if not LOCAL_DHAN_MASTER_CACHE.exists():
        return None
    try:
        return pd.read_csv(LOCAL_DHAN_MASTER_CACHE, low_memory=False)
    except Exception:
        return None


def _equity_history_cache_path(symbol: str, interval: str, market: str) -> Path:
    sym = _normalize_symbol_token(symbol)
    safe_interval = str(interval or "15m").strip().lower().replace("/", "_")
    safe_market = str(market or "india").strip().lower().replace("/", "_")
    return EQUITY_HISTORY_CACHE_DIR / safe_market / safe_interval / f"{sym}.csv"


def _load_equity_history_cache(cache_path: Path) -> pd.DataFrame | None:
    if not cache_path.exists():
        return None
    try:
        frame = pd.read_csv(cache_path, low_memory=False)
    except Exception:
        return None
    if frame is None or frame.empty or "dt_utc" not in frame.columns:
        return None
    frame["dt_utc"] = pd.to_datetime(frame["dt_utc"], utc=True, errors="coerce")
    if "dt_ist" in frame.columns:
        frame["dt_ist"] = pd.to_datetime(frame["dt_ist"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "oi"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["dt_utc", "close"]).sort_values("dt_utc").drop_duplicates(subset=["dt_utc"], keep="last")
    if frame.empty:
        return None
    return frame.set_index("dt_utc")


def _save_equity_history_cache(cache_path: Path, frame: pd.DataFrame | None) -> None:
    if frame is None or frame.empty:
        return
    work = frame.copy()
    if work.index.name == "dt_utc":
        work = work.reset_index()
    elif "dt_utc" not in work.columns:
        work = work.reset_index()
        if "index" in work.columns and "dt_utc" not in work.columns:
            work = work.rename(columns={"index": "dt_utc"})
    if "dt_utc" not in work.columns:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    work.to_csv(cache_path, index=False)


def _merge_equity_history_frames(
    cached: pd.DataFrame | None,
    fresh: pd.DataFrame | None,
    refresh_days: int,
    window_days: int,
    end_ist: datetime | None,
) -> pd.DataFrame | None:
    if fresh is None or fresh.empty:
        return cached
    if cached is None or cached.empty:
        combined = fresh.copy()
    else:
        work_cached = cached.copy()
        work_fresh = fresh.copy()
        if not isinstance(work_cached.index, pd.DatetimeIndex):
            work_cached.index = pd.to_datetime(work_cached.index, utc=True, errors="coerce")
        if not isinstance(work_fresh.index, pd.DatetimeIndex):
            work_fresh.index = pd.to_datetime(work_fresh.index, utc=True, errors="coerce")
        work_cached = work_cached[~work_cached.index.isna()]
        work_fresh = work_fresh[~work_fresh.index.isna()]
        effective_end = end_ist or datetime.now(timezone.utc).astimezone(IST)
        if effective_end.tzinfo is None:
            effective_end = effective_end.replace(tzinfo=IST)
        refresh_start_utc = pd.Timestamp(effective_end - timedelta(days=max(1, refresh_days))).tz_convert("UTC")
        keep_cached = work_cached.loc[work_cached.index < refresh_start_utc]
        combined = pd.concat([keep_cached, work_fresh], axis=0)
    if combined is None or combined.empty:
        return None
    if not isinstance(combined.index, pd.DatetimeIndex):
        combined.index = pd.to_datetime(combined.index, utc=True, errors="coerce")
    combined = combined[~combined.index.isna()].sort_index()
    combined = combined.loc[~combined.index.duplicated(keep="last")]
    effective_end = end_ist or datetime.now(timezone.utc).astimezone(IST)
    if effective_end.tzinfo is None:
        effective_end = effective_end.replace(tzinfo=IST)
    window_start_utc = pd.Timestamp(effective_end - timedelta(days=max(1, window_days))).tz_convert("UTC")
    combined = combined.loc[combined.index >= window_start_utc]
    return combined if not combined.empty else None


@lru_cache(maxsize=1)
def _load_dhan_scrip_master_frame() -> pd.DataFrame:
    cached = _load_cached_dhan_scrip_master()
    if cached is not None and not cached.empty:
        return cached
    return _fetch_dhan_scrip_master()


def _resolve_equity_contract_from_local_sources(symbol: str, market: str = "india") -> dict[str, Any] | None:
    raw = _normalize_symbol_token(symbol)
    manual = _load_manual_security_map()
    if raw in manual:
        security_id = manual[raw]
        if market.strip().lower() == "commodities":
            return None
        return {
            "security_id": str(security_id),
            "exchange_segment": "NSE_EQ",
            "instrument": "EQUITY",
            "trading_symbol": raw,
            "display_name": raw,
        }

    df = _load_cached_dhan_scrip_master()
    if df is None or df.empty:
        return None

    exch_col = _pick_col(df, ["EXCH_ID", "EXCHANGE_ID", "EXCHANGE"])
    seg_col = _pick_col(df, ["SEGMENT", "SEM_SEGMENT"])
    sid_col = _pick_col(df, ["SECURITY_ID", "SEM_SMST_SECURITY_ID", "SM_SECURITY_ID"])
    inst_col = _pick_col(df, ["INSTRUMENT", "SEM_INSTRUMENT_NAME", "INSTRUMENT_NAME"])
    sym_col = _pick_col(df, ["UNDERLYING_SYMBOL", "SYMBOL_NAME", "DISPLAY_NAME", "TRADING_SYMBOL", "SEM_TRADING_SYMBOL"])
    disp_col = _pick_col(df, ["DISPLAY_NAME", "SEM_CUSTOM_SYMBOL"])
    if any(col is None for col in [exch_col, seg_col, sid_col, inst_col, sym_col]):
        return None

    work = df.copy()
    work["_sym_text"] = ""
    for col in [sym_col, disp_col]:
        if col is not None and col in work.columns:
            work["_sym_text"] = work["_sym_text"] + " " + work[col].astype(str).str.upper()
    exact = work[
        work[exch_col].astype(str).str.upper().eq("NSE")
        & work[seg_col].astype(str).str.upper().eq("E")
        & work[inst_col].astype(str).str.upper().eq("EQUITY")
        & (
            work[sym_col].astype(str).str.upper().eq(raw)
            | work["_sym_text"].str.contains(raw, na=False)
        )
    ].copy()
    if exact.empty:
        return None
    exact = exact.sort_values([sid_col]).drop_duplicates(subset=[sid_col])
    row = exact.iloc[0]
    exchange_segment = _normalize_exchange_segment(row[exch_col], row[seg_col], row[inst_col])
    if not exchange_segment:
        exchange_segment = "NSE_EQ"
    return {
        "security_id": str(row[sid_col]).strip(),
        "exchange_segment": exchange_segment,
        "instrument": str(row[inst_col]).strip().upper(),
        "trading_symbol": str(row[sym_col]).strip(),
        "display_name": str(row[disp_col]).strip() if disp_col is not None and pd.notna(row.get(disp_col)) else None,
    }


NIFTY50_TICKERS = tuple(_normalize_symbol_token(symbol) for symbol in NIFTY50_TICKERS)
PATTERN_CATALOG = (
    "Bullish Engulfing",
    "Bearish Engulfing",
    "Hammer",
    "Shooting Star",
    "O=L / C=H",
    "O=H / C=L",
    "VWAP Reclaim",
    "VWAP Rejection",
    "Morning Star",
    "Evening Star",
    "Piercing Line",
    "Dark Cloud Cover",
    "Three White Soldiers",
    "Three Black Crows",
    "Double Bottom",
    "Double Top",
    "Inverse Head & Shoulders",
    "Head & Shoulders",
)
HISTORICAL_DEFAULT_PATTERNS = ("Bullish Engulfing", "Hammer")
VWAP_EMA_RETEST_TOLERANCE = 0.02
EXTREME_OPEN_CLOSE_TOLERANCE = 0.0001


def _as_of_end_datetime(as_of_date: date | None) -> datetime | None:
    if as_of_date is None:
        return None
    return datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, 999999, tzinfo=IST)


def _pattern_direction(pattern: str | None) -> str | None:
    text = str(pattern or "").strip()
    bullish = {
        "Bullish Engulfing",
        "Hammer",
        "O=L / C=H",
        "VWAP Reclaim",
        "Morning Star",
        "Piercing Line",
        "Three White Soldiers",
        "Double Bottom",
        "Inverse Head & Shoulders",
        "VWAP Pierce",
    }
    bearish = {
        "Bearish Engulfing",
        "Shooting Star",
        "O=H / C=L",
        "VWAP Rejection",
        "Evening Star",
        "Dark Cloud Cover",
        "Three Black Crows",
        "Double Top",
        "Head & Shoulders",
    }
    if text in bullish:
        return "BULLISH"
    if text in bearish:
        return "BEARISH"
    return None


def _pattern_tag(pattern: str | None) -> str:
    direction = _pattern_direction(pattern)
    if direction == "BULLISH":
        return "BULLISH_PATTERN"
    if direction == "BEARISH":
        return "BEARISH_PATTERN"
    return "UNCLASSIFIED_PATTERN"


def _price_context_confirmation(
    close: float | None,
    low: float | None,
    high: float | None,
    vwap: float | None = None,
    ema9: float | None = None,
    direction: str | None = None,
    tolerance_pct: float = VWAP_EMA_RETEST_TOLERANCE,
) -> dict[str, Any]:
    levels = [value for value in [vwap, ema9] if value is not None]
    if close is None or not levels:
        return {
            "pass": False,
            "strong": False,
            "close_any": False,
            "close_both": False,
            "retest_any": False,
            "retest_both": False,
            "levels": levels,
        }

    def _near_from_above(price: float | None, level: float) -> bool:
        if price is None:
            return False
        lower = level * (1.0 - tolerance_pct)
        upper = level * (1.0 + tolerance_pct)
        return lower <= price <= upper

    def _near_from_below(price: float | None, level: float) -> bool:
        if price is None:
            return False
        lower = level * (1.0 - tolerance_pct)
        upper = level * (1.0 + tolerance_pct)
        return lower <= price <= upper

    if direction == "BEARISH":
        close_hits = [close < level for level in levels]
        retest_hits = [_near_from_below(high, level) for level in levels]
    else:
        close_hits = [close > level for level in levels]
        retest_hits = [_near_from_above(low, level) for level in levels]

    close_any = any(close_hits)
    close_both = len(levels) >= 2 and all(close_hits)
    retest_any = any(retest_hits)
    retest_both = len(levels) >= 2 and all(retest_hits)
    return {
        "pass": bool(close_any and retest_any),
        "strong": bool(close_both and retest_both),
        "close_any": close_any,
        "close_both": close_both,
        "retest_any": retest_any,
        "retest_both": retest_both,
        "levels": levels,
    }


def _filter_frame_to_as_of_date(frame: pd.DataFrame, as_of_date: date | None) -> pd.DataFrame:
    if frame is None or frame.empty or as_of_date is None:
        return frame
    work = frame.copy()
    if "dt_ist" not in work.columns:
        return work
    dt_ist = pd.to_datetime(work["dt_ist"], utc=True, errors="coerce")
    end_dt = _as_of_end_datetime(as_of_date)
    if end_dt is None:
        return work
    work = work.loc[dt_ist <= pd.Timestamp(end_dt)].copy()
    if "dt_utc" in work.columns:
        work = work.sort_values("dt_utc")
    return work


def _is_market_open(dt: datetime, close_buffer_seconds: float = DEFAULT_BUFFER_SECONDS) -> bool:
    if not _is_weekday(dt):
        return False
    open_dt, close_dt = _market_day_bounds(dt)
    return open_dt <= dt <= (close_dt + timedelta(seconds=close_buffer_seconds))


def _seconds_until_next_scan(
    now: datetime | None = None,
    buffer_seconds: float = DEFAULT_BUFFER_SECONDS,
    last_run_boundary: datetime | None = None,
) -> float:
    now = now or datetime.now(IST)
    open_dt, close_dt = _market_day_bounds(now)
    if not _is_weekday(now):
        target = _next_business_day_open(now)
    elif now < open_dt:
        target = open_dt
    elif now > close_dt + timedelta(seconds=buffer_seconds):
        target = _next_business_day_open(now)
    else:
        current_boundary = _floor_to_15_minute(now)
        if last_run_boundary is not None and current_boundary <= last_run_boundary:
            target = _next_15_minute_boundary(now)
        elif now <= current_boundary + timedelta(seconds=buffer_seconds):
            target = current_boundary
        else:
            target = _next_15_minute_boundary(now)
        if target < open_dt:
            target = open_dt
    return max(0.0, (target + timedelta(seconds=buffer_seconds) - now).total_seconds())


def _sleep_until_next_scan(
    buffer_seconds: float = DEFAULT_BUFFER_SECONDS,
    last_run_boundary: datetime | None = None,
) -> None:
    seconds = _seconds_until_next_scan(buffer_seconds=buffer_seconds, last_run_boundary=last_run_boundary)
    if seconds > 0:
        time.sleep(seconds)


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _compute_vwap(frame: pd.DataFrame) -> pd.Series:
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    volume = _to_numeric(frame["Volume"]).fillna(0.0)
    cumulative_volume = volume.cumsum().replace(0, pd.NA)
    cumulative_tp_volume = (typical * volume).cumsum()
    return (cumulative_tp_volume / cumulative_volume).astype(float)


def _compute_rsi(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    close = _to_numeric(frame["Close"])
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(~avg_loss.eq(0), 100.0)
    return pd.to_numeric(rsi, errors="coerce")


def _compute_body_metrics(frame: pd.DataFrame, period: int = 40, multiplier: float | None = None) -> pd.DataFrame:
    work = frame.copy()
    if "open" in work.columns and "close" in work.columns:
        if multiplier is None:
            try:
                effective_multiplier = float(os.environ.get("DHAN_BODY_MULTIPLIER", str(DEFAULT_BODY_MULTIPLIER)))
            except Exception:
                effective_multiplier = DEFAULT_BODY_MULTIPLIER
        else:
            effective_multiplier = float(multiplier)
        work["body_size"] = (pd.to_numeric(work["close"], errors="coerce") - pd.to_numeric(work["open"], errors="coerce")).abs()
        work["body_avg_40"] = work["body_size"].rolling(window=period, min_periods=period).mean()
        work["body_avg_14"] = work["body_avg_40"]
        work["body_ratio"] = work["body_size"] / work["body_avg_40"].replace(0, np.nan)
        work["is_big_candle"] = work["body_size"] > (effective_multiplier * work["body_avg_40"])
    return work


def _normalize_interval_list(intervals: str | Iterable[str]) -> list[str]:
    if isinstance(intervals, str):
        raw = intervals.split(",")
    else:
        raw = list(intervals)
    out = [str(item).strip() for item in raw if str(item).strip()]
    return out or ["15m"]


def _safe_float(value: Any, digits: int = 4) -> float | None:
    try:
        num = float(value)
    except Exception:
        return None
    if math.isnan(num):
        return None
    return round(num, digits)


def _iso_dt(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    try:
        ts = pd.Timestamp(value)
        return ts.isoformat()
    except Exception:
        text = str(value).strip()
        return text or None


def _as_record_dict(frame: pd.DataFrame, limit: int = 5) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    work = frame.tail(limit).copy().reset_index()
    if "dt_utc" in work.columns:
        work["dt_utc"] = pd.to_datetime(work["dt_utc"], utc=True, errors="coerce")
    if "dt_ist" in work.columns:
        work["dt_ist"] = pd.to_datetime(work["dt_ist"], errors="coerce")
    records: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        records.append(
            {
                "dt_utc": _iso_dt(row.get("dt_utc")),
                "dt_ist": _iso_dt(row.get("dt_ist")),
                "open": _safe_float(row.get("open"), 4),
                "high": _safe_float(row.get("high"), 4),
                "low": _safe_float(row.get("low"), 4),
                "close": _safe_float(row.get("close"), 4),
                "volume": _safe_float(row.get("volume"), 0),
                "oi": _safe_float(row.get("oi"), 2) if "oi" in row else None,
            }
        )
    return records


def _bar_at_offset(bars: list[dict[str, Any]], offset: int) -> dict[str, Any] | None:
    if not bars:
        return None
    index = len(bars) - 1 + offset
    if index < 0 or index >= len(bars):
        return None
    return bars[index]


def _extract_chart_arrays(payload: dict[str, Any], include_oi: bool = False) -> pd.DataFrame | None:
    if not isinstance(payload, dict):
        return None
    ts = (
        payload.get("start_Time")
        or payload.get("startTime")
        or payload.get("timestamp")
        or payload.get("timestamps")
        or []
    )
    quote = payload.get("quote") or payload.get("indicators") or {}
    if isinstance(quote, dict):
        quote = (quote.get("quote") or [quote])[0] if quote else {}
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    op = payload.get("open") or quote.get("open") or []
    hi = payload.get("high") or quote.get("high") or []
    lo = payload.get("low") or quote.get("low") or []
    cl = payload.get("close") or quote.get("close") or []
    vo = payload.get("volume") or quote.get("volume") or []
    oi = (
        payload.get("oi")
        or payload.get("openInterest")
        or payload.get("open_interest")
        or quote.get("oi")
        or quote.get("openInterest")
        or quote.get("open_interest")
        or []
    )
    n = min(len(ts), len(op), len(hi), len(lo), len(cl))
    if n <= 0:
        return None
    if len(vo) < n:
        vo = list(vo) + [None] * (n - len(vo))
    if len(oi) < n:
        oi = list(oi) + [None] * (n - len(oi))
    frame = pd.DataFrame(
        {
            "timestamp": ts[:n],
            "open": op[:n],
            "high": hi[:n],
            "low": lo[:n],
            "close": cl[:n],
            "volume": vo[:n],
            "oi": oi[:n],
        }
    )
    for col in ["open", "high", "low", "close", "volume", "oi"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["dt_utc"] = _parse_epoch_to_utc(frame["timestamp"])
    frame["dt_ist"] = frame["dt_utc"].dt.tz_convert(IST)
    frame = frame.dropna(subset=["dt_utc", "close"])
    if frame.empty:
        return None
    cols = ["dt_utc", "dt_ist", "open", "high", "low", "close", "volume"]
    if include_oi:
        cols.append("oi")
    return frame[cols].reset_index(drop=True)


def _fetch_chart_history_by_contract(
    client: "DhanRealtimeClient",
    contract: dict[str, Any],
    interval: str = "15m",
    data_range: str = "60d",
    include_oi: bool = False,
    end_ist: datetime | None = None,
) -> pd.DataFrame:
    headers = client._headers()
    days = _range_to_days(data_range)
    now_ist = end_ist or datetime.now(timezone.utc).astimezone(IST)
    if now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    start_ist = now_ist - timedelta(days=days)
    chunk_days = int(os.environ.get("DHAN_INTRADAY_CHUNK_DAYS", "89"))
    chunk_days = max(1, min(chunk_days, 89))
    interval_num = _interval_to_dhan(interval)
    out_frames: list[pd.DataFrame] = []
    cursor = start_ist
    last_error: Exception | None = None

    while cursor < now_ist:
        chunk_end = min(cursor + timedelta(days=chunk_days), now_ist)
        payload = {
            "securityId": str(contract["security_id"]),
            "exchangeSegment": contract.get("exchange_segment"),
            "instrument": contract.get("instrument"),
            "interval": str(interval_num),
            "oi": bool(include_oi),
            "fromDate": cursor.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        data = None
        for attempt in range(DEFAULT_INTRADAY_RETRIES + 1):
            try:
                headers = client._headers()
                resp = _dhan_request(
                    "post",
                    f"{DHAN_BASE_URL}/charts/intraday",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                if resp.status_code == 429 and attempt < DEFAULT_INTRADAY_RETRIES:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after is not None:
                        try:
                            sleep_for = float(retry_after)
                        except Exception:
                            sleep_for = 0.0
                    else:
                        sleep_for = min(2.0 * (attempt + 1), 8.0)
                    time.sleep(max(0.5, sleep_for))
                    continue
                if resp.status_code >= 400:
                    detail = ""
                    try:
                        detail = resp.text.strip()
                    except Exception:
                        detail = ""
                    if detail:
                        detail = f" | {detail[:240]}"
                    last_error = RuntimeError(
                        f"Dhan intraday returned {resp.status_code} for {contract['trading_symbol']}{detail}"
                    )
                    break
                data = resp.json() if resp.content else {}
                break
            except Exception as exc:
                last_error = exc
                if attempt < DEFAULT_INTRADAY_RETRIES:
                    time.sleep(min(1.5 * (attempt + 1), 6.0))
                    continue
                break

        if data is None:
            break

        chunk = _extract_chart_arrays(data, include_oi=include_oi)
        if chunk is not None and not chunk.empty:
            out_frames.append(chunk)
        cursor = chunk_end + timedelta(minutes=1)

    if out_frames:
        frame = (
            pd.concat(out_frames, ignore_index=True)
            .drop_duplicates(subset=["dt_utc"])
            .sort_values("dt_utc")
            .reset_index(drop=True)
        )
        frame = frame.set_index("dt_utc")
        return frame

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"No Dhan intraday history returned for {contract['trading_symbol']}")


def _pick_latest_non_null(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def _match_text(value: Any, candidates: Iterable[str]) -> bool:
    text = str(value or "").strip().upper()
    if not text:
        return False
    for cand in candidates:
        cand = str(cand or "").strip().upper()
        if not cand:
            continue
        if text == cand or text.startswith(f"{cand} ") or cand in text:
            return True
    return False


def _is_bullish_engulfing(prev_bar: dict[str, Any] | None, curr_bar: dict[str, Any] | None) -> bool:
    if not prev_bar or not curr_bar:
        return False
    prev_open = _safe_float(prev_bar.get("open"))
    prev_close = _safe_float(prev_bar.get("close"))
    curr_open = _safe_float(curr_bar.get("open"))
    curr_close = _safe_float(curr_bar.get("close"))
    if None in [prev_open, prev_close, curr_open, curr_close]:
        return False
    prev_bearish = prev_close < prev_open
    curr_bullish = curr_close > curr_open
    body_engulf = curr_open <= prev_close and curr_close >= prev_open
    return prev_bearish and curr_bullish and body_engulf


def _is_bearish_engulfing(prev_bar: dict[str, Any] | None, curr_bar: dict[str, Any] | None) -> bool:
    if not prev_bar or not curr_bar:
        return False
    prev_open = _safe_float(prev_bar.get("open"))
    prev_close = _safe_float(prev_bar.get("close"))
    curr_open = _safe_float(curr_bar.get("open"))
    curr_close = _safe_float(curr_bar.get("close"))
    if None in [prev_open, prev_close, curr_open, curr_close]:
        return False
    prev_bullish = prev_close > prev_open
    curr_bearish = curr_close < curr_open
    body_engulf = curr_open >= prev_close and curr_close <= prev_open
    return prev_bullish and curr_bearish and body_engulf


def _is_open_low_close_high(curr_bar: dict[str, Any] | None, vwap: float | None = None, ema9: float | None = None) -> bool:
    if not curr_bar:
        return False
    open_price = _safe_float(curr_bar.get("open"))
    close_price = _safe_float(curr_bar.get("close"))
    high_price = _safe_float(curr_bar.get("high"))
    low_price = _safe_float(curr_bar.get("low"))
    if None in [open_price, close_price, high_price, low_price]:
        return False
    if high_price <= low_price:
        return False
    candle_range = max(high_price - low_price, 1e-9)
    open_at_low = abs(open_price - low_price) / max(low_price, 1e-9) <= EXTREME_OPEN_CLOSE_TOLERANCE
    close_near_high = abs(high_price - close_price) / max(high_price, 1e-9) <= EXTREME_OPEN_CLOSE_TOLERANCE
    context = _price_context_confirmation(close_price, low_price, high_price, vwap=vwap, ema9=ema9, direction="BULLISH")
    return open_at_low and close_near_high and context["pass"]


def _is_open_high_close_low(curr_bar: dict[str, Any] | None, vwap: float | None = None, ema9: float | None = None) -> bool:
    if not curr_bar:
        return False
    open_price = _safe_float(curr_bar.get("open"))
    close_price = _safe_float(curr_bar.get("close"))
    high_price = _safe_float(curr_bar.get("high"))
    low_price = _safe_float(curr_bar.get("low"))
    if None in [open_price, close_price, high_price, low_price]:
        return False
    if high_price <= low_price:
        return False
    candle_range = max(high_price - low_price, 1e-9)
    open_at_high = abs(open_price - high_price) / max(high_price, 1e-9) <= EXTREME_OPEN_CLOSE_TOLERANCE
    close_near_low = abs(close_price - low_price) / max(low_price, 1e-9) <= EXTREME_OPEN_CLOSE_TOLERANCE
    context = _price_context_confirmation(close_price, low_price, high_price, vwap=vwap, ema9=ema9, direction="BEARISH")
    return open_at_high and close_near_low and context["pass"]


def _is_hammer(curr_bar: dict[str, Any] | None, vwap: float | None = None, ema9: float | None = None) -> bool:
    if not curr_bar:
        return False
    open_price = _safe_float(curr_bar.get("open"))
    close_price = _safe_float(curr_bar.get("close"))
    high_price = _safe_float(curr_bar.get("high"))
    low_price = _safe_float(curr_bar.get("low"))
    if None in [open_price, close_price, high_price, low_price]:
        return False
    body = abs(close_price - open_price)
    candle_range = max(high_price - low_price, 1e-9)
    lower_wick = min(open_price, close_price) - low_price
    upper_wick = high_price - max(open_price, close_price)
    close_near_high = (high_price - close_price) / candle_range <= 0.3
    wick_ratio_ok = lower_wick >= max(body * 2.0, candle_range * 0.45)
    upper_wick_small = upper_wick <= max(body * 0.75, candle_range * 0.25)
    context = _price_context_confirmation(close_price, low_price, high_price, vwap=vwap, ema9=ema9, direction="BULLISH")
    return wick_ratio_ok and upper_wick_small and close_near_high and context["pass"]


def _is_shooting_star(curr_bar: dict[str, Any] | None, vwap: float | None = None, ema9: float | None = None) -> bool:
    if not curr_bar:
        return False
    open_price = _safe_float(curr_bar.get("open"))
    close_price = _safe_float(curr_bar.get("close"))
    high_price = _safe_float(curr_bar.get("high"))
    low_price = _safe_float(curr_bar.get("low"))
    if None in [open_price, close_price, high_price, low_price]:
        return False
    body = abs(close_price - open_price)
    candle_range = max(high_price - low_price, 1e-9)
    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price
    close_near_low = (close_price - low_price) / candle_range <= 0.3
    wick_ratio_ok = upper_wick >= max(body * 2.0, candle_range * 0.45)
    lower_wick_small = lower_wick <= max(body * 0.75, candle_range * 0.25)
    context = _price_context_confirmation(close_price, low_price, high_price, vwap=vwap, ema9=ema9, direction="BEARISH")
    return wick_ratio_ok and lower_wick_small and close_near_low and context["pass"]


def _is_vwap_pierce(
    prev_bar: dict[str, Any] | None,
    curr_bar: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev_bar or not curr_bar:
        return False
    prev_low = _safe_float(prev_bar.get("low"))
    prev_close = _safe_float(prev_bar.get("close"))
    curr_low = _safe_float(curr_bar.get("low"))
    curr_close = _safe_float(curr_bar.get("close"))
    curr_open = _safe_float(curr_bar.get("open"))
    if None in [prev_low, prev_close, curr_low, curr_close, curr_open]:
        return False
    context = _price_context_confirmation(curr_close, curr_low, _safe_float(curr_bar.get("high")), vwap=vwap, ema9=ema9, direction="BULLISH")
    if vwap is None:
        return False
    crossed_up = curr_low <= vwap <= curr_close
    prior_below = prev_close <= vwap or prev_low <= vwap
    bullish_finish = curr_close > curr_open and curr_close > vwap
    return crossed_up and prior_below and bullish_finish and context["pass"]


def _is_vwap_reclaim(
    prev_bar: dict[str, Any] | None,
    curr_bar: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev_bar or not curr_bar:
        return False
    prev_open = _safe_float(prev_bar.get("open"))
    prev_close = _safe_float(prev_bar.get("close"))
    curr_open = _safe_float(curr_bar.get("open"))
    curr_close = _safe_float(curr_bar.get("close"))
    curr_high = _safe_float(curr_bar.get("high"))
    curr_low = _safe_float(curr_bar.get("low"))
    if None in [prev_open, prev_close, curr_open, curr_close, curr_high, curr_low]:
        return False
    context = _price_context_confirmation(curr_close, curr_low, curr_high, vwap=vwap, ema9=ema9, direction="BULLISH")
    if vwap is None:
        return False
    prev_below = prev_close < vwap or prev_open < vwap
    reclaimed = curr_open <= vwap and curr_close > vwap
    close_upper = curr_close >= vwap and (curr_close - curr_low) / max(curr_high - curr_low, 1e-9) >= 0.6
    return prev_below and reclaimed and close_upper and context["pass"]


def _is_vwap_rejection(
    prev_bar: dict[str, Any] | None,
    curr_bar: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev_bar or not curr_bar:
        return False
    prev_open = _safe_float(prev_bar.get("open"))
    prev_close = _safe_float(prev_bar.get("close"))
    curr_open = _safe_float(curr_bar.get("open"))
    curr_close = _safe_float(curr_bar.get("close"))
    curr_high = _safe_float(curr_bar.get("high"))
    curr_low = _safe_float(curr_bar.get("low"))
    if None in [prev_open, prev_close, curr_open, curr_close, curr_high, curr_low]:
        return False
    context = _price_context_confirmation(curr_close, curr_low, curr_high, vwap=vwap, ema9=ema9, direction="BEARISH")
    if vwap is None:
        return False
    prev_above = prev_close > vwap or prev_open > vwap
    rejected = curr_open >= vwap and curr_close < vwap
    close_lower = curr_close <= vwap and (curr_high - curr_close) / max(curr_high - curr_low, 1e-9) >= 0.6
    return prev_above and rejected and close_lower and context["pass"]


def _body_top(bar: dict[str, Any] | None) -> float | None:
    if not bar:
        return None
    open_price = _safe_float(bar.get("open"))
    close_price = _safe_float(bar.get("close"))
    if open_price is None or close_price is None:
        return None
    return max(open_price, close_price)


def _body_bottom(bar: dict[str, Any] | None) -> float | None:
    if not bar:
        return None
    open_price = _safe_float(bar.get("open"))
    close_price = _safe_float(bar.get("close"))
    if open_price is None or close_price is None:
        return None
    return min(open_price, close_price)


def _body_size(bar: dict[str, Any] | None) -> float | None:
    if not bar:
        return None
    open_price = _safe_float(bar.get("open"))
    close_price = _safe_float(bar.get("close"))
    if open_price is None or close_price is None:
        return None
    return abs(close_price - open_price)


def _candle_range(bar: dict[str, Any] | None) -> float | None:
    if not bar:
        return None
    high_price = _safe_float(bar.get("high"))
    low_price = _safe_float(bar.get("low"))
    if high_price is None or low_price is None:
        return None
    return max(high_price - low_price, 1e-9)


def _is_morning_star(
    prev2: dict[str, Any] | None,
    prev1: dict[str, Any] | None,
    curr: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev2 or not prev1 or not curr:
        return False
    prev2_open = _safe_float(prev2.get("open"))
    prev2_close = _safe_float(prev2.get("close"))
    prev1_open = _safe_float(prev1.get("open"))
    prev1_close = _safe_float(prev1.get("close"))
    curr_open = _safe_float(curr.get("open"))
    curr_close = _safe_float(curr.get("close"))
    if None in [prev2_open, prev2_close, prev1_open, prev1_close, curr_open, curr_close]:
        return False
    prev2_bearish = prev2_close < prev2_open
    prev1_small = (_body_size(prev1) or 0.0) <= max((_body_size(prev2) or 0.0) * 0.6, (_candle_range(prev2) or 0.0) * 0.25)
    curr_bullish = curr_close > curr_open
    midpoint = (prev2_open + prev2_close) / 2.0
    reclaim = curr_close > midpoint
    gap_or_weak = prev1_close <= prev2_close or prev1_open <= prev2_close
    context = _price_context_confirmation(curr_close, _safe_float(curr.get("low")), _safe_float(curr.get("high")), vwap=vwap, ema9=ema9, direction="BULLISH")
    return prev2_bearish and prev1_small and curr_bullish and reclaim and gap_or_weak and context["pass"]


def _is_evening_star(
    prev2: dict[str, Any] | None,
    prev1: dict[str, Any] | None,
    curr: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev2 or not prev1 or not curr:
        return False
    prev2_open = _safe_float(prev2.get("open"))
    prev2_close = _safe_float(prev2.get("close"))
    prev1_open = _safe_float(prev1.get("open"))
    prev1_close = _safe_float(prev1.get("close"))
    curr_open = _safe_float(curr.get("open"))
    curr_close = _safe_float(curr.get("close"))
    if None in [prev2_open, prev2_close, prev1_open, prev1_close, curr_open, curr_close]:
        return False
    prev2_bullish = prev2_close > prev2_open
    prev1_small = (_body_size(prev1) or 0.0) <= max((_body_size(prev2) or 0.0) * 0.6, (_candle_range(prev2) or 0.0) * 0.25)
    curr_bearish = curr_close < curr_open
    midpoint = (prev2_open + prev2_close) / 2.0
    rejection = curr_close < midpoint
    gap_or_weak = prev1_close >= prev2_close or prev1_open >= prev2_close
    context = _price_context_confirmation(curr_close, _safe_float(curr.get("low")), _safe_float(curr.get("high")), vwap=vwap, ema9=ema9, direction="BEARISH")
    return prev2_bullish and prev1_small and curr_bearish and rejection and gap_or_weak and context["pass"]


def _is_piercing_line(
    prev_bar: dict[str, Any] | None,
    curr_bar: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev_bar or not curr_bar:
        return False
    prev_open = _safe_float(prev_bar.get("open"))
    prev_close = _safe_float(prev_bar.get("close"))
    curr_open = _safe_float(curr_bar.get("open"))
    curr_close = _safe_float(curr_bar.get("close"))
    prev_low = _safe_float(prev_bar.get("low"))
    if None in [prev_open, prev_close, curr_open, curr_close, prev_low]:
        return False
    prev_bearish = prev_close < prev_open
    curr_bullish = curr_close > curr_open
    midpoint = (prev_open + prev_close) / 2.0
    pierce = curr_open <= prev_low and curr_close > midpoint and curr_close < prev_open
    context = _price_context_confirmation(curr_close, _safe_float(curr_bar.get("low")), _safe_float(curr_bar.get("high")), vwap=vwap, ema9=ema9, direction="BULLISH")
    return prev_bearish and curr_bullish and pierce and context["pass"]


def _is_dark_cloud_cover(
    prev_bar: dict[str, Any] | None,
    curr_bar: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev_bar or not curr_bar:
        return False
    prev_open = _safe_float(prev_bar.get("open"))
    prev_close = _safe_float(prev_bar.get("close"))
    curr_open = _safe_float(curr_bar.get("open"))
    curr_close = _safe_float(curr_bar.get("close"))
    prev_high = _safe_float(prev_bar.get("high"))
    if None in [prev_open, prev_close, curr_open, curr_close, prev_high]:
        return False
    prev_bullish = prev_close > prev_open
    curr_bearish = curr_close < curr_open
    midpoint = (prev_open + prev_close) / 2.0
    cloud = curr_open >= prev_high and curr_close < midpoint and curr_close > prev_open
    context = _price_context_confirmation(curr_close, _safe_float(curr_bar.get("low")), _safe_float(curr_bar.get("high")), vwap=vwap, ema9=ema9, direction="BEARISH")
    return prev_bullish and curr_bearish and cloud and context["pass"]


def _is_three_white_soldiers(
    prev2: dict[str, Any] | None,
    prev1: dict[str, Any] | None,
    curr: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev2 or not prev1 or not curr:
        return False
    bars = [prev2, prev1, curr]
    opens = [_safe_float(bar.get("open")) for bar in bars]
    closes = [_safe_float(bar.get("close")) for bar in bars]
    highs = [_safe_float(bar.get("high")) for bar in bars]
    if any(v is None for v in opens + closes + highs):
        return False
    bullish = all(closes[i] > opens[i] for i in range(3))
    higher_closes = closes[0] < closes[1] < closes[2]
    within_bodies = opens[1] <= closes[0] and opens[1] >= opens[0] and opens[2] <= closes[1] and opens[2] >= opens[1]
    close_near_high = all((highs[i] - closes[i]) / max(highs[i] - min(_safe_float(bars[i].get("low")) or closes[i], closes[i]), 1e-9) <= 0.35 for i in range(3))
    context = _price_context_confirmation(closes[2], _safe_float(curr.get("low")), _safe_float(curr.get("high")), vwap=vwap, ema9=ema9, direction="BULLISH")
    return bullish and higher_closes and within_bodies and close_near_high and context["pass"]


def _is_three_black_crows(
    prev2: dict[str, Any] | None,
    prev1: dict[str, Any] | None,
    curr: dict[str, Any] | None,
    vwap: float | None = None,
    ema9: float | None = None,
) -> bool:
    if not prev2 or not prev1 or not curr:
        return False
    bars = [prev2, prev1, curr]
    opens = [_safe_float(bar.get("open")) for bar in bars]
    closes = [_safe_float(bar.get("close")) for bar in bars]
    lows = [_safe_float(bar.get("low")) for bar in bars]
    if any(v is None for v in opens + closes + lows):
        return False
    bearish = all(closes[i] < opens[i] for i in range(3))
    lower_closes = closes[0] > closes[1] > closes[2]
    within_bodies = opens[1] >= closes[0] and opens[1] <= opens[0] and opens[2] >= closes[1] and opens[2] <= opens[1]
    close_near_low = all((closes[i] - lows[i]) / max(_candle_range(bars[i]) or 1e-9, 1e-9) <= 0.35 for i in range(3))
    context = _price_context_confirmation(closes[2], _safe_float(curr.get("low")), _safe_float(curr.get("high")), vwap=vwap, ema9=ema9, direction="BEARISH")
    return bearish and lower_closes and within_bodies and close_near_low and context["pass"]


def _is_double_bottom(bars: list[dict[str, Any]], vwap: float | None = None, ema9: float | None = None) -> bool:
    if len(bars) < 5:
        return False
    closes = [_safe_float(bar.get("close")) for bar in bars[-5:]]
    lows = [_safe_float(bar.get("low")) for bar in bars[-5:]]
    highs = [_safe_float(bar.get("high")) for bar in bars[-5:]]
    if any(v is None for v in closes + lows + highs):
        return False
    first_low = min(lows[0], lows[1])
    second_low = min(lows[3], lows[4])
    valley = lows[2]
    peak_left = max(highs[1], closes[1])
    peak_right = max(highs[3], closes[3])
    neckline_break = closes[4] > max(peak_left, peak_right)
    bottoms_match = abs(first_low - second_low) / max(first_low, second_low, 1e-9) <= 0.02
    middle_higher = valley > first_low and valley > second_low
    context = _price_context_confirmation(closes[4], lows[4], highs[4], vwap=vwap, ema9=ema9, direction="BULLISH")
    return bottoms_match and middle_higher and neckline_break and context["pass"]


def _is_double_top(bars: list[dict[str, Any]], vwap: float | None = None, ema9: float | None = None) -> bool:
    if len(bars) < 5:
        return False
    closes = [_safe_float(bar.get("close")) for bar in bars[-5:]]
    highs = [_safe_float(bar.get("high")) for bar in bars[-5:]]
    lows = [_safe_float(bar.get("low")) for bar in bars[-5:]]
    if any(v is None for v in closes + highs + lows):
        return False
    first_high = max(highs[0], highs[1])
    second_high = max(highs[3], highs[4])
    valley = lows[2]
    neckline_break = closes[4] < valley
    tops_match = abs(first_high - second_high) / max(first_high, second_high, 1e-9) <= 0.02
    middle_lower = valley < first_high and valley < second_high
    context = _price_context_confirmation(closes[4], lows[4], highs[4], vwap=vwap, ema9=ema9, direction="BEARISH")
    return tops_match and middle_lower and neckline_break and context["pass"]


def _is_inverse_head_shoulders(bars: list[dict[str, Any]], vwap: float | None = None, ema9: float | None = None) -> bool:
    if len(bars) < 5:
        return False
    lows = [_safe_float(bar.get("low")) for bar in bars[-5:]]
    highs = [_safe_float(bar.get("high")) for bar in bars[-5:]]
    closes = [_safe_float(bar.get("close")) for bar in bars[-5:]]
    if any(v is None for v in lows + highs + closes):
        return False
    left_shoulder = lows[1]
    head = lows[2]
    right_shoulder = lows[3]
    neckline = max(highs[1], highs[3])
    breakout = closes[4] > neckline
    shoulders_match = abs(left_shoulder - right_shoulder) / max(left_shoulder, right_shoulder, 1e-9) <= 0.03
    head_lower = head < left_shoulder and head < right_shoulder
    context = _price_context_confirmation(closes[4], lows[4], highs[4], vwap=vwap, ema9=ema9, direction="BULLISH")
    return shoulders_match and head_lower and breakout and context["pass"]


def _is_head_shoulders(bars: list[dict[str, Any]], vwap: float | None = None, ema9: float | None = None) -> bool:
    if len(bars) < 5:
        return False
    highs = [_safe_float(bar.get("high")) for bar in bars[-5:]]
    lows = [_safe_float(bar.get("low")) for bar in bars[-5:]]
    closes = [_safe_float(bar.get("close")) for bar in bars[-5:]]
    if any(v is None for v in highs + lows + closes):
        return False
    left_shoulder = highs[1]
    head = highs[2]
    right_shoulder = highs[3]
    neckline = min(lows[1], lows[3])
    breakdown = closes[4] < neckline
    shoulders_match = abs(left_shoulder - right_shoulder) / max(left_shoulder, right_shoulder, 1e-9) <= 0.03
    head_higher = head > left_shoulder and head > right_shoulder
    context = _price_context_confirmation(closes[4], lows[4], highs[4], vwap=vwap, ema9=ema9, direction="BEARISH")
    return shoulders_match and head_higher and breakdown and context["pass"]


def _strategy_gate_summary(patterns: list[str], gate2: bool, gate3: bool, gate4: bool, direction: str | None = None) -> dict[str, Any]:
    return {
        "gate1_pattern": patterns[0] if patterns else None,
        "gate1_pass": bool(patterns),
        "gate2_pass": bool(gate2),
        "gate3_pass": bool(gate3),
        "gate4_pass": bool(gate4),
        "strategy_pass": bool(patterns) and gate2 and gate3 and gate4,
        "direction": direction,
    }


def _pattern_base_score(pattern: str) -> int:
    weights = {
        "Inverse Head & Shoulders": 98,
        "Head & Shoulders": 98,
        "Double Bottom": 95,
        "Double Top": 95,
        "Morning Star": 90,
        "Evening Star": 90,
        "Three White Soldiers": 88,
        "Three Black Crows": 88,
        "O=L / C=H": 84,
        "O=H / C=L": 84,
        "Bullish Engulfing": 80,
        "Bearish Engulfing": 80,
        "VWAP Reclaim": 75,
        "VWAP Rejection": 75,
        "Piercing Line": 70,
        "Dark Cloud Cover": 70,
        "Hammer": 60,
        "Shooting Star": 60,
        "VWAP Pierce": 55,
    }
    return weights.get(pattern, 50)


def _score_strategy(
    patterns: list[str],
    direction: str | None,
    gate2: bool,
    gate3: bool,
    gate4: bool,
    close: float | None,
    vwap: float | None,
    volume: float | None,
    volume_avg: float | None,
    put_velocity: float | None,
    call_velocity: float | None,
    pcr: float | None,
    pcr_prev: float | None,
    close_above_both: bool = False,
    retest_both: bool = False,
) -> int:
    if not patterns:
        return 0
    score = max(_pattern_base_score(pattern) for pattern in patterns)
    if gate2:
        score += 8
    if gate3:
        score += 8
    if gate4:
        score += 6
    if close is not None and vwap is not None:
        score += 4 if direction == "BULLISH" and close > vwap else 4 if direction == "BEARISH" and close < vwap else 0
    if close_above_both:
        score += 4
    if retest_both:
        score += 3
    if volume is not None and volume_avg is not None and volume > volume_avg:
        score += 4
    if put_velocity is not None and direction == "BULLISH" and put_velocity >= 5:
        score += 2
    if call_velocity is not None and direction == "BEARISH" and call_velocity >= 5:
        score += 2
    if direction == "BULLISH" and put_velocity is not None and call_velocity is not None:
        if put_velocity >= 5 and call_velocity <= -2:
            score += 8
    if direction == "BEARISH" and call_velocity is not None and put_velocity is not None:
        if call_velocity >= 5 and put_velocity <= -2:
            score += 8
    if pcr is not None and pcr_prev is not None:
        score += 2 if (direction == "BULLISH" and pcr > pcr_prev) or (direction == "BEARISH" and pcr < pcr_prev) else 0
    return score


def _gate3_metrics(option_chain: OptionChainSnapshot | None) -> dict[str, Any]:
    put_velocity = None
    call_velocity = None
    pcr = None
    pcr_prev = None
    bullish_gate3 = False
    bearish_gate3 = False
    if option_chain is not None:
        if option_chain.atm_put_oi is not None and option_chain.atm_put_oi_past not in [None, 0]:
            put_velocity = round(((option_chain.atm_put_oi - option_chain.atm_put_oi_past) / option_chain.atm_put_oi_past) * 100.0, 2)
        if option_chain.atm_call_oi is not None and option_chain.atm_call_oi_past not in [None, 0]:
            call_velocity = round(((option_chain.atm_call_oi - option_chain.atm_call_oi_past) / option_chain.atm_call_oi_past) * 100.0, 2)
        pcr = option_chain.pcr_intraday
        pcr_prev = option_chain.pcr_intraday_past
        bullish_gate3 = bool(
            put_velocity is not None
            and call_velocity is not None
            and 5.0 <= put_velocity <= 8.0
            and -5.0 <= call_velocity <= -3.0
        )
        bearish_gate3 = bool(
            call_velocity is not None
            and put_velocity is not None
            and 5.0 <= call_velocity <= 8.0
            and -5.0 <= put_velocity <= -3.0
        )

    return {
        "put_velocity": put_velocity,
        "call_velocity": call_velocity,
        "pcr": pcr,
        "pcr_prev": pcr_prev,
        "bullish_gate3": bullish_gate3,
        "bearish_gate3": bearish_gate3,
    }


def _gate4_metrics(option_chain: OptionChainSnapshot | None) -> dict[str, Any]:
    pcr = None
    pcr_prev = None
    bullish_gate4 = False
    bearish_gate4 = False
    reason = "PCR unavailable"
    if option_chain is not None:
        pcr = option_chain.pcr_intraday
        pcr_prev = option_chain.pcr_intraday_past
        bullish_gate4 = bool(pcr is not None and (pcr > 1.0 or (pcr_prev is not None and pcr > pcr_prev)))
        bearish_gate4 = bool(pcr is not None and (pcr < 1.0 or (pcr_prev is not None and pcr < pcr_prev)))
        if bullish_gate4 and pcr is not None and pcr_prev is not None and pcr > pcr_prev:
            reason = "PCR rising, bullish confirmed"
        elif bullish_gate4 and pcr is not None and pcr > 1.0:
            reason = "PCR above 1, bullish confirmed"
        elif bearish_gate4 and pcr is not None and pcr_prev is not None and pcr < pcr_prev:
            reason = "PCR falling, bearish confirmed"
        elif bearish_gate4 and pcr is not None and pcr < 1.0:
            reason = "PCR below 1, bearish confirmed"
        elif pcr is not None and pcr_prev is not None:
            if pcr > pcr_prev:
                reason = "PCR rising, bearish not confirmed"
            elif pcr < pcr_prev:
                reason = "PCR falling, bullish not confirmed"
            else:
                reason = "PCR flat, no directional confirmation"
        elif pcr is not None:
            reason = "PCR available, no prior PCR to compare"
    return {
        "pcr": pcr,
        "pcr_prev": pcr_prev,
        "bullish_gate4": bullish_gate4,
        "bearish_gate4": bearish_gate4,
        "reason": reason,
    }


def _rejection_reasons(snapshot: "SymbolSnapshot", strategy: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if strategy.get("gate1_pass") is not True:
        if not strategy.get("is_big_candle", True):
            reasons.append("Gate 1 failed: candle body not > 5.5x 40-period average")
        else:
            reasons.append("Gate 1 failed: no qualifying pattern")

    direction = strategy.get("direction")
    close = _safe_float(strategy.get("close"))
    vwap = _safe_float(snapshot.vwap)
    if strategy.get("gate2_pass") is not True:
        if direction == "BEARISH":
            reasons.append(
                f"Gate 2 failed: close {close} is not below VWAP/EMA9 {vwap}"
                if close is not None and vwap is not None
                else "Gate 2 failed: close/VWAP/EMA9 missing"
            )
        else:
            reasons.append(
                f"Gate 2 failed: close {close} is not above VWAP/EMA9 {vwap}"
                if close is not None and vwap is not None
                else "Gate 2 failed: close/VWAP/EMA9 missing"
            )

    if strategy.get("gate3_pass") is not True:
        put_velocity = strategy.get("put_oi_velocity_pct")
        call_velocity = strategy.get("call_oi_velocity_pct")
        if direction == "BEARISH":
            reasons.append(
                f"Gate 3 failed: call OI velocity {call_velocity}%, put OI velocity {put_velocity}%"
            )
        else:
            reasons.append(
                f"Gate 3 failed: put OI velocity {put_velocity}%, call OI velocity {call_velocity}%"
            )

    if strategy.get("gate4_pass") is not True:
        pcr = strategy.get("pcr")
        pcr_prev = strategy.get("pcr_prev")
        if direction == "BEARISH":
            reasons.append(f"Gate 4 failed: PCR {pcr} not below or falling vs {pcr_prev}")
        else:
            reasons.append(f"Gate 4 failed: PCR {pcr} not above or rising vs {pcr_prev}")

    return reasons


def _pre_option_rejection_reasons(snapshot: "SymbolSnapshot", strategy: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if strategy.get("gate1_pass") is not True:
        if not strategy.get("is_big_candle", True):
            reasons.append("Gate 1 failed: candle body not > 5.5x 40-period average")
        else:
            reasons.append("Gate 1 failed: no qualifying pattern")

    direction = strategy.get("direction")
    close = _safe_float(strategy.get("close"))
    vwap = _safe_float(snapshot.vwap)
    if strategy.get("gate2_pass") is not True:
        if direction == "BEARISH":
            reasons.append(
                f"Gate 2 failed: close {close} is not below VWAP/EMA9 {vwap}"
                if close is not None and vwap is not None
                else "Gate 2 failed: close/VWAP/EMA9 missing"
            )
        else:
            reasons.append(
                f"Gate 2 failed: close {close} is not above VWAP/EMA9 {vwap}"
                if close is not None and vwap is not None
                else "Gate 2 failed: close/VWAP/EMA9 missing"
            )
    return reasons


def _scan_history_occurrences(
    frame: pd.DataFrame,
    symbol: str,
    interval: str,
    pattern_filter: str | None = None,
) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []

    work = frame.copy().sort_values("dt_utc").reset_index(drop=True)
    work["ema9"] = _to_numeric(work["close"]).ewm(span=9, adjust=False).mean()
    work["vwap"] = _compute_vwap(work.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}))
    work["rsi14"] = _compute_rsi(work.rename(columns={"close": "Close"}))
    work = _compute_body_metrics(work)
    records = _as_record_dict(work, limit=len(work))
    allowed = {pattern_filter} if pattern_filter else set(HISTORICAL_DEFAULT_PATTERNS)

    hits: list[dict[str, Any]] = []
    def _add_hit(pattern: str, idx: int) -> None:
        curr = records[idx]
        hits.append(
            {
                "symbol": symbol,
                "interval": interval,
                "pattern": pattern,
                "bar_index": idx,
                "candle_time_ist": curr.get("dt_ist"),
                "open": curr.get("open"),
                "high": curr.get("high"),
                "low": curr.get("low"),
                "close": curr.get("close"),
                "volume": curr.get("volume"),
                "vwap": _safe_float(work.iloc[idx].get("vwap"), 4),
                "ema9": _safe_float(work.iloc[idx].get("ema9"), 4),
            }
        )

    for idx in range(len(records)):
        curr = records[idx]
        prev = records[idx - 1] if idx - 1 >= 0 else None
        prev2 = records[idx - 2] if idx - 2 >= 0 else None
        prev4_window = records[max(0, idx - 4) : idx + 1]
        curr_vwap = _safe_float(work.iloc[idx].get("vwap"), 4)
        curr_ema9 = _safe_float(work.iloc[idx].get("ema9"), 4)
        body_avg_value = work.iloc[idx].get("body_avg_40")
        if pd.notna(body_avg_value):
            big_candle_value = work.iloc[idx].get("is_big_candle")
            if pd.isna(big_candle_value) or not bool(big_candle_value):
                continue

        if "Bullish Engulfing" in allowed and prev is not None and _is_bullish_engulfing(prev, curr):
            _add_hit("Bullish Engulfing", idx)

        if "Bearish Engulfing" in allowed and prev is not None and _is_bearish_engulfing(prev, curr):
            _add_hit("Bearish Engulfing", idx)

        if "Hammer" in allowed and _is_hammer(curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Hammer", idx)

        if "Shooting Star" in allowed and _is_shooting_star(curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Shooting Star", idx)

        if "O=L / C=H" in allowed and _is_open_low_close_high(curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("O=L / C=H", idx)

        if "O=H / C=L" in allowed and _is_open_high_close_low(curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("O=H / C=L", idx)

        if "VWAP Reclaim" in allowed and prev is not None and _is_vwap_reclaim(prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("VWAP Reclaim", idx)

        if "VWAP Rejection" in allowed and prev is not None and _is_vwap_rejection(prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("VWAP Rejection", idx)

        if "Morning Star" in allowed and prev2 is not None and prev is not None and _is_morning_star(prev2, prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Morning Star", idx)

        if "Evening Star" in allowed and prev2 is not None and prev is not None and _is_evening_star(prev2, prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Evening Star", idx)

        if "Piercing Line" in allowed and prev is not None and _is_piercing_line(prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Piercing Line", idx)

        if "Dark Cloud Cover" in allowed and prev is not None and _is_dark_cloud_cover(prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Dark Cloud Cover", idx)

        if "Three White Soldiers" in allowed and idx >= 2 and _is_three_white_soldiers(records[idx - 2], prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Three White Soldiers", idx)

        if "Three Black Crows" in allowed and idx >= 2 and _is_three_black_crows(records[idx - 2], prev, curr, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Three Black Crows", idx)

        if "Double Bottom" in allowed and len(prev4_window) >= 5 and _is_double_bottom(prev4_window, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Double Bottom", idx)

        if "Double Top" in allowed and len(prev4_window) >= 5 and _is_double_top(prev4_window, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Double Top", idx)

        if "Inverse Head & Shoulders" in allowed and len(prev4_window) >= 5 and _is_inverse_head_shoulders(prev4_window, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Inverse Head & Shoulders", idx)

        if "Head & Shoulders" in allowed and len(prev4_window) >= 5 and _is_head_shoulders(prev4_window, vwap=curr_vwap, ema9=curr_ema9):
            _add_hit("Head & Shoulders", idx)

    hits.sort(key=lambda row: str(row.get("candle_time_ist") or ""))
    return hits


def _historical_snapshot_from_work(
    work: pd.DataFrame,
    idx: int,
    symbol: str,
    interval: str,
    option_chain: OptionChainSnapshot | None = None,
) -> SymbolSnapshot:
    latest = work.iloc[idx]
    candle_time_ist = _iso_dt(latest.get("dt_ist"))
    recent_bars = _as_record_dict(work.iloc[: idx + 1], limit=5)
    close_price = _safe_float(latest.get("close"), 4)
    vwap = _safe_float(latest.get("vwap"), 4)
    ema9 = _safe_float(latest.get("ema9"), 4)
    rsi14 = _safe_float(latest.get("rsi14"), 2)
    volume = _safe_float(latest.get("volume"), 0)
    body_size = _safe_float(latest.get("body_size"), 4)
    body_avg_40 = _safe_float(latest.get("body_avg_40") or latest.get("body_avg_14"), 4)
    is_big_candle = bool(latest.get("is_big_candle")) if not pd.isna(latest.get("is_big_candle")) else False
    return SymbolSnapshot(
        symbol=symbol,
        interval=interval,
        candle_time_ist=candle_time_ist,
        recent_bars=recent_bars,
        close=close_price,
        vwap=vwap,
        ema9=ema9,
        rsi14=rsi14,
        volume=volume,
        body_size=body_size,
        body_avg_14=body_avg_40,
        is_big_candle=is_big_candle,
        option_chain=option_chain,
    )


def _append_signal_csv(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["TIMESTAMP", "TICKER", "PATTERN", "ENTRY", "SL", "TARGET", "PCR"],
            extrasaction="ignore",
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _evaluate_strategy(snapshot: "SymbolSnapshot", allowed_patterns: set[str] | None = None) -> dict[str, Any]:
    bars = snapshot.recent_bars or []
    curr = _bar_at_offset(bars, 0)
    prev = _bar_at_offset(bars, -1)
    prev2 = _bar_at_offset(bars, -2)
    prev4 = _bar_at_offset(bars, -4)
    vwap = snapshot.vwap

    close = _safe_float(curr.get("close") if curr else None)
    low = _safe_float(curr.get("low") if curr else None)
    high = _safe_float(curr.get("high") if curr else None)
    volume = _safe_float(curr.get("volume") if curr else None, 0)
    ema9 = snapshot.ema9
    volume_avg = None
    if bars:
        past_volumes = [_safe_float(bar.get("volume"), 0) for bar in bars[:-1]]
        past_volumes = [val for val in past_volumes if val is not None]
        if past_volumes:
            volume_avg = round(sum(past_volumes) / len(past_volumes), 2)

    big_candle = bool(snapshot.is_big_candle)
    if snapshot.body_avg_14 is None:
        big_candle = True
    body_size = snapshot.body_size
    body_avg_40 = snapshot.body_avg_14
    body_avg_14 = body_avg_40

    bullish_patterns: list[str] = []
    if big_candle and _is_bullish_engulfing(prev, curr):
        bullish_patterns.append("Bullish Engulfing")
    if big_candle and _is_hammer(curr, vwap=vwap, ema9=ema9):
        bullish_patterns.append("Hammer")
    if big_candle and _is_open_low_close_high(curr, vwap=vwap, ema9=ema9):
        bullish_patterns.append("O=L / C=H")
    if big_candle and _is_vwap_reclaim(prev, curr, vwap=vwap, ema9=ema9):
        bullish_patterns.append("VWAP Reclaim")
    if big_candle and _is_morning_star(prev2, prev, curr, vwap=vwap, ema9=ema9):
        bullish_patterns.append("Morning Star")
    if big_candle and _is_piercing_line(prev, curr, vwap=vwap, ema9=ema9):
        bullish_patterns.append("Piercing Line")
    if big_candle and _is_three_white_soldiers(_bar_at_offset(bars, -2), prev, curr, vwap=vwap, ema9=ema9):
        bullish_patterns.append("Three White Soldiers")
    if big_candle and _is_double_bottom(bars, vwap=vwap, ema9=ema9):
        bullish_patterns.append("Double Bottom")
    if big_candle and _is_inverse_head_shoulders(bars, vwap=vwap, ema9=ema9):
        bullish_patterns.append("Inverse Head & Shoulders")

    bearish_patterns: list[str] = []
    if big_candle and _is_bearish_engulfing(prev, curr):
        bearish_patterns.append("Bearish Engulfing")
    if big_candle and _is_shooting_star(curr, vwap=vwap, ema9=ema9):
        bearish_patterns.append("Shooting Star")
    if big_candle and _is_open_high_close_low(curr, vwap=vwap, ema9=ema9):
        bearish_patterns.append("O=H / C=L")
    if big_candle and _is_vwap_rejection(prev, curr, vwap=vwap, ema9=ema9):
        bearish_patterns.append("VWAP Rejection")
    if big_candle and _is_evening_star(prev2, prev, curr, vwap=vwap, ema9=ema9):
        bearish_patterns.append("Evening Star")
    if big_candle and _is_dark_cloud_cover(prev, curr, vwap=vwap, ema9=ema9):
        bearish_patterns.append("Dark Cloud Cover")
    if big_candle and _is_three_black_crows(_bar_at_offset(bars, -2), prev, curr, vwap=vwap, ema9=ema9):
        bearish_patterns.append("Three Black Crows")
    if big_candle and _is_double_top(bars, vwap=vwap, ema9=ema9):
        bearish_patterns.append("Double Top")
    if big_candle and _is_head_shoulders(bars, vwap=vwap, ema9=ema9):
        bearish_patterns.append("Head & Shoulders")

    if allowed_patterns is not None:
        bullish_patterns = [pattern for pattern in bullish_patterns if pattern in allowed_patterns]
        bearish_patterns = [pattern for pattern in bearish_patterns if pattern in allowed_patterns]

    bullish_patterns = sorted(dict.fromkeys(bullish_patterns), key=_pattern_base_score, reverse=True)
    bearish_patterns = sorted(dict.fromkeys(bearish_patterns), key=_pattern_base_score, reverse=True)

    direction = None
    patterns: list[str] = []
    if bullish_patterns and not bearish_patterns:
        direction = "BULLISH"
        patterns = bullish_patterns
    elif bearish_patterns and not bullish_patterns:
        direction = "BEARISH"
        patterns = bearish_patterns
    elif bullish_patterns and bearish_patterns:
        direction = "BOTH"
        patterns = bullish_patterns + bearish_patterns

    bullish_context = _price_context_confirmation(close, low, high, vwap=vwap, ema9=ema9, direction="BULLISH")
    bearish_context = _price_context_confirmation(close, low, high, vwap=vwap, ema9=ema9, direction="BEARISH")
    gate2_bullish = bool(bullish_context["pass"])
    gate2_bearish = bool(bearish_context["pass"])

    option_chain = snapshot.option_chain
    gate3_info = _gate3_metrics(option_chain)
    put_velocity = gate3_info["put_velocity"]
    call_velocity = gate3_info["call_velocity"]
    pcr = gate3_info["pcr"]
    pcr_prev = gate3_info["pcr_prev"]
    gate3_bullish = gate3_info["bullish_gate3"]
    gate3_bearish = gate3_info["bearish_gate3"]
    gate4 = False
    if option_chain is not None:
        gate4_bullish = bool(pcr is not None and (pcr > 1.0 or (pcr_prev is not None and pcr > pcr_prev)))
        gate4_bearish = bool(pcr is not None and (pcr < 1.0 or (pcr_prev is not None and pcr < pcr_prev)))
        if direction == "BULLISH":
            gate4 = gate4_bullish
        elif direction == "BEARISH":
            gate4 = gate4_bearish
        elif direction == "BOTH":
            gate4 = gate4_bullish or gate4_bearish

    gate3 = False
    if direction == "BULLISH":
        gate3 = gate3_bullish
    elif direction == "BEARISH":
        gate3 = gate3_bearish
    elif direction == "BOTH":
        gate3 = gate3_bullish or gate3_bearish

    gate2 = gate2_bullish if direction != "BEARISH" else gate2_bearish
    if direction == "BOTH":
        gate2 = gate2_bullish or gate2_bearish

    decision = _strategy_gate_summary(patterns, gate2, gate3, gate4, direction=direction)
    entry = close
    stop_loss = low if direction != "BEARISH" else high
    target = None
    chosen_context = bullish_context if direction != "BEARISH" else bearish_context
    if direction == "BOTH":
        chosen_context = bullish_context if bullish_context["strong"] or bullish_context["pass"] else bearish_context
    score = _score_strategy(
        patterns=patterns,
        direction=direction,
        gate2=decision["gate2_pass"],
        gate3=decision["gate3_pass"],
        gate4=decision["gate4_pass"],
        close=close,
        vwap=vwap,
        volume=volume,
        volume_avg=volume_avg,
        put_velocity=put_velocity,
        call_velocity=call_velocity,
        pcr=pcr,
        pcr_prev=pcr_prev,
        close_above_both=bool(chosen_context.get("close_both")),
        retest_both=bool(chosen_context.get("retest_both")),
    )
    if decision["strategy_pass"] and entry is not None and stop_loss is not None:
        if direction == "BEARISH":
            target = round(entry - (1.5 * (stop_loss - entry)), 4)
        else:
            target = round(entry + (1.5 * (entry - stop_loss)), 4)

    return {
        "strategy_pass": decision["strategy_pass"],
        "pattern": decision["gate1_pattern"],
        "patterns": patterns,
        "gate1_pass": decision["gate1_pass"],
        "gate2_pass": decision["gate2_pass"],
        "gate3_pass": decision["gate3_pass"],
        "gate4_pass": decision["gate4_pass"],
        "entry": entry,
        "stop_loss": stop_loss,
        "target": target,
        "pcr": pcr,
        "pcr_prev": pcr_prev,
        "put_oi_velocity_pct": put_velocity,
        "call_oi_velocity_pct": call_velocity,
        "prev4_close": _safe_float(prev4.get("close")) if prev4 else None,
        "direction": direction,
        "close": close,
        "high": high,
        "volume_avg": volume_avg,
        "body_size": body_size,
        "body_avg_40": body_avg_40,
        "body_avg_14": body_avg_14,
        "is_big_candle": big_candle,
        "score": score,
    }


def _format_gate3_group_message(
    symbol: str,
    snapshot: "SymbolSnapshot",
    strategy: dict[str, Any],
    contract: dict | None,
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    source_note: str | None = None,
) -> str:
    direction = str(strategy.get("direction") or "NEUTRAL").upper()
    pattern = str(strategy.get("gate1_pattern") or strategy.get("pattern") or "UNAVAILABLE")
    call_velocity = strategy.get("call_oi_velocity_pct")
    put_velocity = strategy.get("put_oi_velocity_pct")
    pcr = strategy.get("pcr")
    pcr_prev = strategy.get("pcr_prev")
    compact_trade_line = (
        f"{strategy_name} | {symbol.upper()} | {direction} | Pattern {pattern} | "
        f"Call OI {call_velocity if call_velocity is not None else 'NA'}% | "
        f"Put OI {put_velocity if put_velocity is not None else 'NA'}% | "
        f"PCR {pcr if pcr is not None else 'NA'} vs {pcr_prev if pcr_prev is not None else 'NA'}"
    )
    if source_note:
        compact_trade_line = f"{compact_trade_line} | {source_note}"
    payload = {
        "ticker": symbol.upper(),
        "live_data": {
            "cmp": snapshot.close,
            "volume_vs_avg": "Dhan Gate 3 scanner",
            "rsi_14": snapshot.rsi14,
        },
        "indicators": {
            "9_ema": snapshot.ema9,
            "vwap": snapshot.vwap,
            "daily_candle_type": pattern,
        },
        "market_structure": {
            "historical_swing_peaks_multi_week": [],
            "recent_swing_lows": [],
            "weekly_candle_high": None,
            "weekly_candle_low": None,
            "weekly_candle_shape": "Gate 3 OI scanner",
            "completed_chart_patterns": pattern,
            "bearish_order_block_zone": "",
            "bullish_order_block_zone": "",
        },
        "derivatives": {
            "max_call_oi_strike": getattr(snapshot.option_chain, "highest_call_oi_strike", None) if snapshot.option_chain else None,
            "call_oi_contracts": getattr(snapshot.option_chain, "highest_call_oi", None) if snapshot.option_chain else None,
            "max_put_oi_strike": getattr(snapshot.option_chain, "highest_put_oi_strike", None) if snapshot.option_chain else None,
            "put_oi_contracts": getattr(snapshot.option_chain, "highest_put_oi", None) if snapshot.option_chain else None,
            "pcr": pcr,
            "intraday_oi_shift_data": (
                f"Call OI {call_velocity if call_velocity is not None else 'NA'}% | "
                f"Put OI {put_velocity if put_velocity is not None else 'NA'}% | "
                f"PCR {pcr if pcr is not None else 'NA'} vs {pcr_prev if pcr_prev is not None else 'NA'}"
            ),
        },
        "meta": {
            "source_key": symbol.upper(),
            "candle_time_ist": snapshot.candle_time_ist,
            "market": "INDIA",
        },
    }
    strategy_context = {
        "id": "dhan_pattern_oi_vwap_ema",
        "title": strategy_name,
        "mode": "INTRADAY",
        "market": "INDIA",
        "trade_type": "INTRADAY",
        "selection": "Master EMA9 symbols with Pattern + OI + VWAP/EMA",
        "freshness": "fresh Gate 3 trigger only",
        "filters": "Pattern + OI + VWAP/EMA alignment with Gate 3 OI velocity and PCR",
        "source": source_note or "Pattern-only universe",
    }
    terminal_result = {
        "input_payload": payload,
        "parsed_output": {
            "agent_1_directional_alpha_filter": {
                "ticker": symbol.upper(),
                "side": "BUY" if direction == "BULLISH" else "SELL" if direction == "BEARISH" else "NEUTRAL",
                "strategy_signal_validation": (
                    f"Gate 3 confirms {direction.lower()} OI migration with PCR {pcr:.4f} and call/put velocity divergence."
                    if pcr is not None and call_velocity is not None and put_velocity is not None
                    else "Gate 3 confirms intraday OI migration with PCR context."
                ),
            },
            "agent_2_liquidity_pool_sweep_quant": {
                "ticker": symbol.upper(),
                "upper_side_sweep_level": None,
                "lower_side_sweep_level": None,
                "sweep_trap_validation": "Gate 3 scanner focuses on OI relocation rather than sweep bands.",
            },
            "agent_3_combined_resistance_call_barrier": {
                "ticker": symbol.upper(),
                "strongest_u_turn_r": None,
                "r_max_oi_strike": str(getattr(snapshot.option_chain, "highest_call_oi_strike", "") or ""),
                "r_max_oi_volume": str(getattr(snapshot.option_chain, "highest_call_oi", "") or ""),
                "major_resistance_range_chart": "",
                "why_it_u_turns_r": "Gate 3 call-wall behavior is tracked directly from intraday OI migration.",
            },
            "agent_4_combined_support_order_flow": {
                "ticker": symbol.upper(),
                "strongest_u_turn_s": None,
                "s_max_oi_strike": str(getattr(snapshot.option_chain, "highest_put_oi_strike", "") or ""),
                "s_max_oi_volume": str(getattr(snapshot.option_chain, "highest_put_oi", "") or ""),
                "pcr_ratio": float(pcr) if pcr is not None else 0.0,
                "major_support_range_chart": "",
                "why_it_u_turns_s": "Gate 3 put-cushion behavior is tracked directly from intraday OI migration.",
                "oi_shifting_verdict": (
                    f"Call OI {call_velocity if call_velocity is not None else 'NA'}% vs Put OI {put_velocity if put_velocity is not None else 'NA'}%; PCR {pcr if pcr is not None else 'NA'}."
                ),
            },
            "agent_5_tactical_entry_range_architect": {
                "ticker": symbol.upper(),
                "order_type": "BUY LIMIT" if direction == "BULLISH" else "SELL LIMIT" if direction == "BEARISH" else "NO TRADE",
                "execution_entry_range_1_2_days": "",
                "terminal_entry_rationale": "Gate 3 scanner is an intraday OI confirmation alert.",
            },
            "agent_6_dhan_super_order_terminal_architect": {
                "ticker": symbol.upper(),
                "dhan_product_type": "SUPER_ORDER",
                "dhan_transaction_type": "BUY LIMIT" if direction == "BULLISH" else "SELL LIMIT" if direction == "BEARISH" else "NO TRADE",
                "limit_entry_price": snapshot.close,
                "stop_loss_price": snapshot.close,
                "stop_loss_percentage": "0.00%",
                "target_1_price": snapshot.close,
                "target_1_percentage": "0.00%",
                "target_2_price": snapshot.close,
                "target_2_percentage": "0.00%",
                "calculated_risk_reward_ratio": "1:1.00",
                "dhan_order_placement_rationale": "Gate 3 scanner does not place an execution order; it only raises an intraday confirmation alert.",
            },
        },
    }
    try:
        return format_single_agent_group_message(
            compact_trade_line,
            terminal_result,
            market="INDIA",
            strategy_context=strategy_context,
        )
    except Exception:
        lines = [
            f"Strategy: {strategy_name}",
            f"## {symbol.upper()} | Side: {direction}",
            f"Pattern Name: {pattern}",
            f"Source: {source_note or 'Pattern-only universe'}",
            f"PCR: {pcr if pcr is not None else 'NA'} | Prev PCR: {pcr_prev if pcr_prev is not None else 'NA'}",
            f"Call OI Velocity: {call_velocity if call_velocity is not None else 'NA'}%",
            f"Put OI Velocity: {put_velocity if put_velocity is not None else 'NA'}%",
            f"Call Wall: {getattr(snapshot.option_chain, 'highest_call_oi_strike', None) if snapshot.option_chain else 'NA'}",
            f"Put Floor: {getattr(snapshot.option_chain, 'highest_put_oi_strike', None) if snapshot.option_chain else 'NA'}",
            f"Signal: {compact_trade_line}",
        ]
        return "\n".join(lines)


def _format_gate12_group_message(
    symbol: str,
    snapshot: "SymbolSnapshot",
    strategy: dict[str, Any],
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    source_note: str | None = None,
) -> str:
    direction = str(strategy.get("direction") or "NEUTRAL").upper()
    pattern = str(strategy.get("gate1_pattern") or strategy.get("pattern") or "UNAVAILABLE")
    close = snapshot.close if snapshot.close is not None else "NA"
    vwap = snapshot.vwap if snapshot.vwap is not None else "NA"
    ema9 = snapshot.ema9 if snapshot.ema9 is not None else "NA"
    candle_time = snapshot.candle_time_ist or "NA"
    return "\n".join(
        [
            f"{strategy_name} SETUP | {symbol.upper()} | {direction}",
            f"Pattern: {pattern}",
            f"Source: {source_note or 'Pattern-only universe'}",
            f"Signals clear: Pattern + VWAP/EMA alignment",
            f"Close: {close} | VWAP: {vwap} | EMA9: {ema9}",
            f"Candle Time: {candle_time}",
        ]
    )


def _format_gate3_personal_message(
    symbol: str,
    snapshot: "SymbolSnapshot",
    strategy: dict[str, Any],
    contract: dict | None,
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    source_note: str | None = None,
) -> str:
    direction = str(strategy.get("direction") or "NEUTRAL").upper()
    pattern = str(strategy.get("gate1_pattern") or strategy.get("pattern") or "UNAVAILABLE")
    call_velocity = strategy.get("call_oi_velocity_pct")
    put_velocity = strategy.get("put_oi_velocity_pct")
    pcr = strategy.get("pcr")
    pcr_prev = strategy.get("pcr_prev")
    gate1_pass = "PASS" if strategy.get("gate1_pass") else "FAIL"
    gate2_pass = "PASS" if strategy.get("gate2_pass") else "FAIL"
    gate3_pass = "PASS" if strategy.get("gate3_pass") else "FAIL"
    gate4_pass = "PASS" if strategy.get("gate4_pass") else "FAIL"
    call_strike = getattr(snapshot.option_chain, "highest_call_oi_strike", None) if snapshot.option_chain else None
    call_oi_prev = getattr(snapshot.option_chain, "highest_call_oi_past", None) if snapshot.option_chain else None
    put_strike = getattr(snapshot.option_chain, "highest_put_oi_strike", None) if snapshot.option_chain else None
    put_oi_prev = getattr(snapshot.option_chain, "highest_put_oi_past", None) if snapshot.option_chain else None
    call_oi = getattr(snapshot.option_chain, "highest_call_oi", None) if snapshot.option_chain else None
    put_oi = getattr(snapshot.option_chain, "highest_put_oi", None) if snapshot.option_chain else None
    lines = [
        f"{strategy_name.upper()} | {symbol.upper()}",
        f"Source: {source_note or 'Pattern-only universe'}",
        f"Gate 1: {gate1_pass} | Pattern Name: {pattern} | Direction: {direction}",
        f"Gate 2: {gate2_pass} | Close: {snapshot.close if snapshot.close is not None else 'NA'} | VWAP: {snapshot.vwap if snapshot.vwap is not None else 'NA'} | EMA9: {snapshot.ema9 if snapshot.ema9 is not None else 'NA'}",
        f"Gate 3: {gate3_pass} | Call OI: {call_oi if call_oi is not None else 'NA'} @ {call_strike if call_strike is not None else 'NA'} vs {call_oi_prev if call_oi_prev is not None else 'NA'} | Put OI: {put_oi if put_oi is not None else 'NA'} @ {put_strike if put_strike is not None else 'NA'} vs {put_oi_prev if put_oi_prev is not None else 'NA'}",
        f"Gate 4: {gate4_pass} | PCR: {pcr if pcr is not None else 'NA'} | Prev PCR: {pcr_prev if pcr_prev is not None else 'NA'}",
        f"Candle Time: {snapshot.candle_time_ist or 'NA'}",
        f"Call OI Change: {call_velocity if call_velocity is not None else 'NA'}%",
        f"Put OI Change: {put_velocity if put_velocity is not None else 'NA'}%",
    ]
    if contract:
        lines.append(f"Contract: {contract.get('trading_symbol') or symbol.upper()} | {contract.get('exchange_segment') or 'Dhan'}")
    return "\n".join(lines)


def _format_gate12_personal_message(
    symbol: str,
    snapshot: "SymbolSnapshot",
    strategy: dict[str, Any],
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    source_note: str | None = None,
) -> str:
    direction = str(strategy.get("direction") or "NEUTRAL").upper()
    pattern = str(strategy.get("gate1_pattern") or strategy.get("pattern") or "UNAVAILABLE")
    gate1_pass = "PASS" if strategy.get("gate1_pass") else "FAIL"
    gate2_pass = "PASS" if strategy.get("gate2_pass") else "FAIL"
    lines = [
        f"{strategy_name.upper()} SETUP | {symbol.upper()}",
        f"Source: {source_note or 'Pattern-only universe'}",
        f"Gate 1: {gate1_pass} | Pattern Name: {pattern} | Direction: {direction}",
        f"Gate 2: {gate2_pass} | Close: {snapshot.close if snapshot.close is not None else 'NA'} | VWAP: {snapshot.vwap if snapshot.vwap is not None else 'NA'} | EMA9: {snapshot.ema9 if snapshot.ema9 is not None else 'NA'}",
        "Signal: Pattern and VWAP/EMA are clear.",
        f"Candle Time: {snapshot.candle_time_ist or 'NA'}",
    ]
    return "\n".join(lines)


def _repeat_pattern_sequence_label(direction: str | None, sequence_no: int) -> str:
    def _ordinal(n: int) -> str:
        if 10 <= n % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    direction_text = str(direction or "").upper()
    if direction_text == "BULLISH":
        return f"{_ordinal(sequence_no)} bullish today"
    if direction_text == "BEARISH":
        return f"{_ordinal(sequence_no)} bearish today"
    return f"{_ordinal(sequence_no)} repeat today"


def _repeat_pattern_message(symbol: str, snapshot: "SymbolSnapshot", strategy: dict[str, Any], sequence_no: int) -> str:
    direction = str(strategy.get("direction") or "NEUTRAL").upper()
    pattern = str(strategy.get("gate1_pattern") or strategy.get("pattern") or "UNAVAILABLE")
    pattern_tag = str(strategy.get("pattern_tag") or _pattern_tag(pattern)).strip() or "UNCLASSIFIED_PATTERN"
    candle_time = snapshot.candle_time_ist or "NA"
    close = snapshot.close if snapshot.close is not None else "NA"
    label = _repeat_pattern_sequence_label(direction, sequence_no)
    strategy_name = str(strategy.get("strategy_name") or DEFAULT_STRATEGY_NAME).strip() or DEFAULT_STRATEGY_NAME
    return "\n".join(
        [
            f"Strategy: {strategy_name}",
            "Tag: REPEAT_PATTERN",
            f"Pattern Tag: {pattern_tag}",
            f"Repeat Pattern | {symbol.upper()}",
            f"Sequence: {label}",
            f"Direction: {direction}",
            f"Pattern Name: {pattern}",
            f"Close: {close}",
            f"Candle Time: {candle_time}",
        ]
    )


def _replay_gate3_snapshot_file(
    snapshot_file: Path,
    gate12_alerts: bool = False,
    gate3_alerts: bool = False,
) -> dict[str, Any]:
    payload = _load_json_file(snapshot_file, default={})
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid snapshot file: {snapshot_file}")
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError(f"Snapshot file does not contain a snapshots list: {snapshot_file}")

    gate3_state = _load_gate3_state()
    last_gate3_meta_map = gate3_state.get("last_gate3_meta_map")
    if not isinstance(last_gate3_meta_map, dict):
        last_gate3_meta_map = {}
    last_gate12_meta_map = gate3_state.get("last_gate12_meta_map")
    if not isinstance(last_gate12_meta_map, dict):
        last_gate12_meta_map = {}

    setup_alert_candidates: list[dict[str, Any]] = []
    alert_candidates: list[dict[str, Any]] = []
    for item in snapshots:
        if not isinstance(item, dict):
            continue
        pre_strategy = item.get("pre_strategy") if isinstance(item.get("pre_strategy"), dict) else {}
        strategy = item.get("strategy") if isinstance(item.get("strategy"), dict) else {}
        if not strategy and not pre_strategy:
            continue

        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue

        snapshot_obj = SimpleNamespace(
            close=_safe_float(item.get("close")),
            rsi14=_safe_float(item.get("rsi14")),
            ema9=_safe_float(item.get("ema9")),
            vwap=_safe_float(item.get("vwap")),
            candle_time_ist=item.get("candle_time_ist"),
            option_chain=None,
        )
        if gate12_alerts and pre_strategy:
            direction12 = str(pre_strategy.get("direction") or "").strip().upper()
            gate12_pass = bool(pre_strategy.get("gate1_pass") and pre_strategy.get("gate2_pass"))
            if gate12_pass and direction12 in {"BULLISH", "BEARISH", "BOTH"}:
                signature12 = _gate12_trigger_signature_from_snapshot_record(symbol, item)
                previous_meta12 = last_gate12_meta_map.get(symbol)
                if not isinstance(previous_meta12, dict):
                    previous_meta12 = None
                if _is_fresh_gate12(previous_meta12, signature12):
                    setup_alert_candidates.append(
                        {
                            "symbol": symbol,
                            "signature": signature12,
                            "group_message": _format_gate12_group_message(symbol, snapshot_obj, pre_strategy, DEFAULT_STRATEGY_NAME),
                            "personal_message": _format_gate12_personal_message(symbol, snapshot_obj, pre_strategy, DEFAULT_STRATEGY_NAME),
                            "strategy": pre_strategy,
                        }
                    )

        if gate3_alerts and strategy:
            direction = str(strategy.get("direction") or "").strip().upper()
            gate3_pass = bool(strategy.get("gate1_pass") and strategy.get("gate2_pass") and strategy.get("gate3_pass"))
            if not gate3_pass or direction not in {"BULLISH", "BEARISH", "BOTH"}:
                continue
            signature = _gate3_trigger_signature_from_snapshot_record(symbol, item)
            previous_meta = last_gate3_meta_map.get(symbol)
            if not isinstance(previous_meta, dict):
                previous_meta = None
            if not _is_fresh_gate3(previous_meta, signature):
                continue
            alert_candidates.append(
                {
                    "symbol": symbol,
                    "signature": signature,
                    "group_message": _format_gate3_group_message(symbol, snapshot_obj, strategy, None, DEFAULT_STRATEGY_NAME),
                    "personal_message": _format_gate3_personal_message(symbol, snapshot_obj, strategy, None, DEFAULT_STRATEGY_NAME),
                    "strategy": strategy,
                }
            )

    if gate12_alerts and setup_alert_candidates:
        for alert in setup_alert_candidates:
            symbol = str(alert.get("symbol") or "").upper()
            signature = alert.get("signature")
            group_message = str(alert.get("group_message") or "").strip()
            personal_message = str(alert.get("personal_message") or "").strip()
            if group_message:
                sent_group = _send_telegram_to(os.getenv("TELEGRAM_TRADE_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID"), group_message)
                logger.info("Gate 1/2 replay group alert %s for %s", "sent" if sent_group else "not sent", symbol)
            if personal_message:
                personal_chat_id = (
                    os.getenv("TELEGRAM_PERSONAL_CHAT_ID")
                    or os.getenv("TELEGRAM_STATUS_CHAT_ID")
                    or os.getenv("TELEGRAM_CHAT_ID")
                )
                sent_personal = _send_telegram_to(personal_chat_id, personal_message)
                logger.info("Gate 1/2 replay personal alert %s for %s", "sent" if sent_personal else "not sent", symbol)
            if symbol and isinstance(signature, dict):
                last_gate12_meta_map[symbol] = signature

    if gate3_alerts and alert_candidates:
        for alert in alert_candidates:
            symbol = str(alert.get("symbol") or "").upper()
            signature = alert.get("signature")
            group_message = str(alert.get("group_message") or "").strip()
            personal_message = str(alert.get("personal_message") or "").strip()
            if group_message:
                sent_group = _send_telegram_to(os.getenv("TELEGRAM_TRADE_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID"), group_message)
                logger.info("Gate 3 replay group alert %s for %s", "sent" if sent_group else "not sent", symbol)
            if personal_message:
                personal_chat_id = (
                    os.getenv("TELEGRAM_PERSONAL_CHAT_ID")
                    or os.getenv("TELEGRAM_STATUS_CHAT_ID")
                    or os.getenv("TELEGRAM_CHAT_ID")
                )
                sent_personal = _send_telegram_to(personal_chat_id, personal_message)
                logger.info("Gate 3 replay personal alert %s for %s", "sent" if sent_personal else "not sent", symbol)
            if symbol and isinstance(signature, dict):
                last_gate3_meta_map[symbol] = signature
        _save_gate3_state(
            {
                "last_gate12_meta_map": last_gate12_meta_map,
                "last_gate3_meta_map": last_gate3_meta_map,
                "last_gate3_at": datetime.now(IST).isoformat(),
                "last_gate12_at": datetime.now(IST).isoformat(),
                "gate12_alert_count": len(setup_alert_candidates),
                "gate3_alert_count": len(alert_candidates),
                "replay_source": str(snapshot_file),
            }
        )
    elif gate12_alerts and setup_alert_candidates:
        _save_gate3_state(
            {
                "last_gate12_meta_map": last_gate12_meta_map,
                "last_gate3_meta_map": last_gate3_meta_map,
                "last_gate12_at": datetime.now(IST).isoformat(),
                "gate12_alert_count": len(setup_alert_candidates),
                "gate3_alert_count": len(alert_candidates),
                "replay_source": str(snapshot_file),
            }
        )

    output = {
        "source": str(snapshot_file),
        "snapshot_count": len(snapshots),
        "gate12_alerts_enabled": bool(gate12_alerts),
        "gate12_alerts": setup_alert_candidates,
        "gate3_alerts_enabled": bool(gate3_alerts),
        "gate3_alerts": alert_candidates,
    }
    GATE3_REPLAY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    GATE3_REPLAY_OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"Saved Gate 3 replay output to {GATE3_REPLAY_OUTPUT}")
    return output


def _resolve_option_contract_candidates(
    symbol: str,
    market: str = "india",
    expiry: date | None = None,
    reference_date: date | None = None,
) -> list[dict[str, Any]]:
    raw = str(symbol or "").strip().upper()
    if not raw:
        raise ValueError("symbol is required")

    df = _load_dhan_scrip_master_frame()
    if df.empty:
        raise RuntimeError("Could not load Dhan scrip master CSV")

    exch_col = _pick_col(df, ["EXCH_ID", "EXCHANGE_ID", "EXCHANGE"])
    seg_col = _pick_col(df, ["SEGMENT", "SEM_SEGMENT"])
    sid_col = _pick_col(df, ["SECURITY_ID", "SEM_SMST_SECURITY_ID", "SM_SECURITY_ID"])
    inst_col = _pick_col(df, ["INSTRUMENT", "SEM_INSTRUMENT_NAME", "INSTRUMENT_NAME"])
    sym_col = _pick_col(df, ["UNDERLYING_SYMBOL", "SYMBOL_NAME", "DISPLAY_NAME", "TRADING_SYMBOL", "SEM_TRADING_SYMBOL"])
    disp_col = _pick_col(df, ["DISPLAY_NAME", "SEM_CUSTOM_SYMBOL"])
    strike_col = _pick_col(df, ["STRIKE_PRICE", "SEM_STRIKE_PRICE", "STRIKE"])
    expiry_col = _pick_col(df, ["SM_EXPIRY_DATE", "EXPIRY_DATE", "EXPIRY"])
    right_col = _pick_col(df, ["OPTION_TYPE", "SEM_OPTION_TYPE", "CALL_PUT", "CP_TYPE", "OPTION_RIGHT"])

    if any(col is None for col in [exch_col, seg_col, sid_col, inst_col, sym_col, strike_col, expiry_col]):
        raise RuntimeError("Dhan scrip master is missing option-chain columns")

    work = df.copy()
    work["_sym_text"] = ""
    for col in [sym_col, disp_col]:
        if col is not None and col in work.columns:
            work["_sym_text"] = work["_sym_text"] + " " + work[col].astype(str).str.upper()

    work = work[
        work[exch_col].astype(str).str.upper().eq("NSE")
        & work[seg_col].astype(str).str.upper().eq("D")
        & work[inst_col].astype(str).str.upper().str.contains("OPT", na=False)
    ].copy()
    if work.empty:
        return []

    exact = work[
        work[sym_col].astype(str).str.upper().eq(raw)
        | work["_sym_text"].str.contains(raw, na=False)
    ].copy()
    if exact.empty:
        return []

    exact["_expiry_date"] = exact[expiry_col].apply(_parse_expiry_date)
    if expiry is not None:
        exact = exact[exact["_expiry_date"].eq(expiry)]
    else:
        today = reference_date or datetime.now(timezone.utc).astimezone(IST).date()
        future = exact[exact["_expiry_date"].notna() & (exact["_expiry_date"] >= today)]
        if not future.empty:
            exact = future

    def _option_right(row: pd.Series) -> str:
        values = [row.get(right_col), row.get(sym_col), row.get(disp_col), row.get("_sym_text")]
        for value in values:
            text = str(value or "").strip().upper()
            if "CE" in text or "CALL" in text:
                return "CE"
            if "PE" in text or "PUT" in text:
                return "PE"
        return ""

    exact["_option_right"] = exact.apply(_option_right, axis=1)
    exact["_strike"] = pd.to_numeric(exact[strike_col], errors="coerce")
    exact = exact[exact["_strike"].notna()].copy()
    exact["_priority"] = exact[seg_col].astype(str).str.upper().map({"D": 0}).fillna(9)
    exact = exact.sort_values(["_expiry_date", "_strike", "_option_right", "_priority", sid_col])
    exact = exact.drop_duplicates(subset=[exch_col, seg_col, sid_col])

    contracts: list[dict[str, Any]] = []
    for _, row in exact.iterrows():
        exchange_segment = _normalize_exchange_segment(row[exch_col], row[seg_col], row[inst_col])
        if not exchange_segment:
            continue
        contracts.append(
            {
                "security_id": str(row[sid_col]).strip(),
                "exchange_segment": exchange_segment,
                "instrument": str(row[inst_col]).strip().upper(),
                "trading_symbol": str(row[sym_col]).strip(),
                "display_name": str(row[disp_col]).strip() if disp_col is not None and pd.notna(row.get(disp_col)) else None,
                "strike": float(row["_strike"]),
                "expiry_date": row["_expiry_date"].isoformat() if pd.notna(row["_expiry_date"]) else None,
                "option_type": str(row["_option_right"]).strip().upper(),
            }
        )
    return contracts


def _select_relevant_option_contracts(
    contracts: list[dict[str, Any]],
    spot_close: float,
    fast_mode: bool = False,
    strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> list[dict[str, Any]]:
    if not fast_mode or not contracts:
        return contracts

    strikes = sorted({float(contract["strike"]) for contract in contracts if contract.get("strike") is not None})
    if len(strikes) <= 2 * max(0, strike_window) + 1:
        return contracts

    atm_index = min(range(len(strikes)), key=lambda idx: abs(strikes[idx] - float(spot_close)))
    lower_index = max(0, atm_index - max(0, strike_window))
    upper_index = min(len(strikes) - 1, atm_index + max(0, strike_window))
    relevant_strikes = set(strikes[lower_index : upper_index + 1])
    selected = [contract for contract in contracts if float(contract["strike"]) in relevant_strikes]
    return selected or contracts


@dataclass
class OptionChainSnapshot:
    underlying: str
    spot_close: float | None
    atm_strike: float | None
    atm_call_oi: float | None
    atm_call_oi_past: float | None
    atm_put_oi: float | None
    atm_put_oi_past: float | None
    total_call_oi: float | None
    total_call_oi_past: float | None
    total_put_oi: float | None
    total_put_oi_past: float | None
    pcr_intraday: float | None
    pcr_intraday_past: float | None
    highest_call_oi_strike: float | None
    highest_call_oi: float | None
    highest_call_oi_past: float | None = None
    highest_put_oi_strike: float | None = None
    highest_put_oi: float | None = None
    highest_put_oi_past: float | None = None
    contracts_scanned: int = 0
    expiry_date: str | None = None
    atm_call_oi_strike_used: float | None = None
    atm_put_oi_strike_used: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying,
            "spot_close": self.spot_close,
            "atm_strike": self.atm_strike,
            "atm_call_oi": self.atm_call_oi,
            "atm_call_oi_past": self.atm_call_oi_past,
            "atm_put_oi": self.atm_put_oi,
            "atm_put_oi_past": self.atm_put_oi_past,
            "total_call_oi": self.total_call_oi,
            "total_call_oi_past": self.total_call_oi_past,
            "total_put_oi": self.total_put_oi,
            "total_put_oi_past": self.total_put_oi_past,
            "pcr_intraday": self.pcr_intraday,
            "pcr_intraday_past": self.pcr_intraday_past,
            "highest_call_oi_strike": self.highest_call_oi_strike,
            "highest_call_oi": self.highest_call_oi,
            "highest_call_oi_past": self.highest_call_oi_past,
            "highest_put_oi_strike": self.highest_put_oi_strike,
            "highest_put_oi": self.highest_put_oi,
            "highest_put_oi_past": self.highest_put_oi_past,
            "contracts_scanned": self.contracts_scanned,
            "expiry_date": self.expiry_date,
            "atm_call_oi_strike_used": self.atm_call_oi_strike_used,
            "atm_put_oi_strike_used": self.atm_put_oi_strike_used,
        }


@dataclass
class SymbolSnapshot:
    symbol: str
    interval: str
    candle_time_ist: str | None
    recent_bars: list[dict[str, Any]]
    close: float | None
    vwap: float | None
    ema9: float | None
    rsi14: float | None
    volume: float | None
    body_size: float | None = None
    body_avg_14: float | None = None
    body_avg_40: float | None = None
    is_big_candle: bool = False
    option_chain: OptionChainSnapshot | None = None

    def as_dict(self, include_recent_bars: bool = True, include_option_chain: bool = True) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "interval": self.interval,
            "candle_time_ist": self.candle_time_ist,
            "close": self.close,
            "vwap": self.vwap,
            "ema9": self.ema9,
            "rsi14": self.rsi14,
            "volume": self.volume,
            "body_size": self.body_size,
            "body_avg_40": self.body_avg_40 if self.body_avg_40 is not None else self.body_avg_14,
            "body_avg_14": self.body_avg_14,
            "is_big_candle": self.is_big_candle,
        }
        if include_recent_bars:
            payload["recent_bars"] = self.recent_bars
        if include_option_chain and self.option_chain is not None:
            payload["option_chain"] = self.option_chain.as_dict()
        return payload


class DhanRealtimeClient:
    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id.strip()
        self.access_token = access_token.strip()

    def _headers(self) -> dict[str, str]:
        token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip() or self.access_token
        client_id = os.environ.get("DHAN_CLIENT_ID", "").strip() or self.client_id
        return {
            "access-token": token,
            "client-id": client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def fetch_contract_history(
        self,
        contract: dict[str, Any],
        interval: str = "15m",
        data_range: str = "60d",
        include_oi: bool = False,
        end_ist: datetime | None = None,
    ) -> pd.DataFrame:
        return _fetch_chart_history_by_contract(
            self,
            contract,
            interval=interval,
            data_range=data_range,
            include_oi=include_oi,
            end_ist=end_ist,
        )

    def fetch_equity_history(
        self,
        symbol: str,
        interval: str = "15m",
        data_range: str = "60d",
        market: str = "india",
        end_ist: datetime | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        local_contract = _resolve_equity_contract_from_local_sources(symbol, market=market)
        candidates = [local_contract] if local_contract is not None else resolve_contract_candidates(symbol, market=market)
        last_error: Exception | None = None
        for contract in candidates:
            try:
                frame = self.fetch_contract_history(
                    contract,
                    interval=interval,
                    data_range=data_range,
                    include_oi=False,
                    end_ist=end_ist,
                )
                return frame, contract
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"No Dhan intraday history returned for {symbol}")

    def resolve_option_contracts(
        self,
        symbol: str,
        market: str = "india",
        expiry: date | None = None,
        reference_date: date | None = None,
    ) -> list[dict[str, Any]]:
        return _resolve_option_contract_candidates(symbol, market=market, expiry=expiry, reference_date=reference_date)

    def fetch_option_chain_snapshot(
        self,
        symbol: str,
        spot_close: float,
        market: str = "india",
        include_expiry: date | None = None,
        data_range: str = "2d",
        max_workers: int = 8,
        end_ist: datetime | None = None,
        fast_mode: bool = False,
        fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
    ) -> OptionChainSnapshot:
        reference_date = end_ist.date() if end_ist is not None else None
        contracts = self.resolve_option_contracts(
            symbol,
            market=market,
            expiry=include_expiry,
            reference_date=reference_date,
        )
        if not contracts:
            return OptionChainSnapshot(
                underlying=symbol,
                spot_close=spot_close,
                atm_strike=None,
                atm_call_oi=None,
                atm_call_oi_past=None,
                atm_put_oi=None,
                atm_put_oi_past=None,
                total_call_oi=None,
                total_call_oi_past=None,
                total_put_oi=None,
                total_put_oi_past=None,
                pcr_intraday=None,
                pcr_intraday_past=None,
                highest_call_oi_strike=None,
                highest_call_oi=None,
                highest_call_oi_past=None,
                highest_put_oi_strike=None,
                highest_put_oi=None,
                highest_put_oi_past=None,
                contracts_scanned=0,
            )

        contracts = _select_relevant_option_contracts(
            contracts,
            spot_close=spot_close,
            fast_mode=fast_mode,
            strike_window=fast_strike_window,
        )
        strikes = sorted({float(c["strike"]) for c in contracts})
        atm_strike = min(strikes, key=lambda s: abs(float(s) - float(spot_close))) if strikes else None

        call_contracts = [c for c in contracts if c.get("option_type") == "CE"]
        put_contracts = [c for c in contracts if c.get("option_type") == "PE"]
        worker_cap = DEFAULT_OPTION_CHAIN_WORKERS if fast_mode else max_workers
        max_workers = max(1, min(max_workers, worker_cap, len(contracts)))

        def _latest_contract_oi(contract: dict[str, Any]) -> dict[str, Any]:
            frame = self.fetch_contract_history(
                contract,
                interval="15m",
                data_range=data_range,
                include_oi=True,
                end_ist=end_ist,
            )
            latest = frame.iloc[-1]
            past = frame.iloc[-4] if len(frame) >= 4 else latest
            return {
                "contract": contract,
                "close": _safe_float(latest.get("close"), 4),
                "oi_current": _safe_float(latest.get("oi"), 2),
                "oi_past": _safe_float(past.get("oi"), 2),
            }

        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_latest_contract_oi, contract) for contract in contracts]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.debug("Option contract fetch failed for %s: %s", symbol, exc)

        latest_by_key: dict[tuple[float, str], dict[str, Any]] = {}
        for item in results:
            contract = item["contract"]
            key = (float(contract["strike"]), str(contract.get("option_type") or ""))
            latest_by_key[key] = item

        def _nearest_oi(strike: float | None, right: str) -> tuple[float | None, float | None, float | None]:
            if strike is None:
                return None, None, None
            candidates = [
                item
                for item in results
                if item["contract"].get("option_type") == right and item.get("oi_current") is not None
            ]
            if not candidates:
                return None, None, None

            exact_key = (float(strike), right)
            exact_item = latest_by_key.get(exact_key)
            if exact_item and exact_item.get("oi_current") is not None:
                contract = exact_item["contract"]
                return exact_item.get("oi_current"), exact_item.get("oi_past"), float(contract["strike"])

            nearest_item = min(
                candidates,
                key=lambda item: (
                    abs(float(item["contract"]["strike"]) - float(strike)),
                    float(item["contract"]["strike"]),
                ),
            )
            contract = nearest_item["contract"]
            return (
                nearest_item.get("oi_current"),
                nearest_item.get("oi_past"),
                float(contract["strike"]),
            )

        def _highest_oi(right: str) -> tuple[float | None, float | None, float | None]:
            candidates = [
                item
                for item in results
                if item["contract"].get("option_type") == right and item.get("oi_current") is not None
            ]
            if not candidates:
                return None, None, None
            best_item = max(
                candidates,
                key=lambda item: (
                    float(item.get("oi_current") or 0.0),
                    -abs(float(item["contract"]["strike"]) - float(spot_close)),
                    -float(item["contract"]["strike"]),
                ),
            )
            contract = best_item["contract"]
            return (
                best_item.get("oi_current"),
                best_item.get("oi_past"),
                float(contract["strike"]),
            )

        call_ois = [item["oi_current"] for item in results if item["contract"].get("option_type") == "CE" and item.get("oi_current") is not None]
        put_ois = [item["oi_current"] for item in results if item["contract"].get("option_type") == "PE" and item.get("oi_current") is not None]
        call_ois_past = [item["oi_past"] for item in results if item["contract"].get("option_type") == "CE" and item.get("oi_past") is not None]
        put_ois_past = [item["oi_past"] for item in results if item["contract"].get("option_type") == "PE" and item.get("oi_past") is not None]
        total_call_oi = round(float(sum(call_ois)), 2) if call_ois else None
        total_call_oi_past = round(float(sum(call_ois_past)), 2) if call_ois_past else None
        total_put_oi = round(float(sum(put_ois)), 2) if put_ois else None
        total_put_oi_past = round(float(sum(put_ois_past)), 2) if put_ois_past else None
        pcr_intraday = None
        pcr_intraday_past = None
        if total_call_oi and total_call_oi > 0 and total_put_oi is not None:
            pcr_intraday = round(float(total_put_oi) / float(total_call_oi), 4)
        if total_call_oi_past and total_call_oi_past > 0 and total_put_oi_past is not None:
            pcr_intraday_past = round(float(total_put_oi_past) / float(total_call_oi_past), 4)

        highest_call_oi, highest_call_oi_past, highest_call_oi_strike = _highest_oi("CE")
        highest_put_oi, highest_put_oi_past, highest_put_oi_strike = _highest_oi("PE")

        expiry_date = None
        if contracts and contracts[0].get("expiry_date"):
            expiry_date = contracts[0]["expiry_date"]

        atm_call_oi, atm_call_oi_past, atm_call_oi_strike_used = _nearest_oi(atm_strike, "CE")
        atm_put_oi, atm_put_oi_past, atm_put_oi_strike_used = _nearest_oi(atm_strike, "PE")

        return OptionChainSnapshot(
            underlying=symbol,
            spot_close=spot_close,
            atm_strike=float(atm_strike) if atm_strike is not None else None,
            atm_call_oi=atm_call_oi,
            atm_call_oi_past=atm_call_oi_past,
            atm_put_oi=atm_put_oi,
            atm_put_oi_past=atm_put_oi_past,
            total_call_oi=total_call_oi,
            total_call_oi_past=total_call_oi_past,
            total_put_oi=total_put_oi,
            total_put_oi_past=total_put_oi_past,
            pcr_intraday=pcr_intraday,
            pcr_intraday_past=pcr_intraday_past,
            highest_call_oi_strike=highest_call_oi_strike,
            highest_call_oi=highest_call_oi,
            highest_call_oi_past=highest_call_oi_past,
            highest_put_oi_strike=highest_put_oi_strike,
            highest_put_oi=highest_put_oi,
            highest_put_oi_past=highest_put_oi_past,
            contracts_scanned=len(results),
            expiry_date=expiry_date,
            atm_call_oi_strike_used=atm_call_oi_strike_used,
            atm_put_oi_strike_used=atm_put_oi_strike_used,
        )

    def snapshot_symbol(
        self,
        symbol: str,
        interval: str = "15m",
        lookback_days: int = 60,
        market: str = "india",
        option_lookback_days: int = 2,
        as_of_date: date | None = None,
        fetch_option_chain: bool = True,
        fast_mode: bool = False,
        fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
        history_store: "MarketSnapshotStore" | None = None,
    ) -> SymbolSnapshot:
        end_ist = _as_of_end_datetime(as_of_date)
        use_history_cache = (
            as_of_date is None
            and os.environ.get("DHAN_DISABLE_EQUITY_HISTORY_CACHE", "").strip() != "1"
            and MarketSnapshotStore is not None
        )
        store = history_store if history_store is not None else (MarketSnapshotStore() if use_history_cache else None)
        contract: dict[str, Any] | None = None
        frame: pd.DataFrame | None = None
        max_history_bars = max(40, int(max(lookback_days, 1) * 26))
        if store is not None:
            retention_days = max(
                1,
                int(round(max(lookback_days, 1) * max(DEFAULT_CANDLE_HISTORY_RETENTION_MULTIPLIER, 1.0))),
            )
            fetch_days = max(1, min(DEFAULT_CANDLE_HISTORY_FETCH_DAYS, max(lookback_days, 1)))
            cached = store.read_candle_history(interval, symbol, market, interval)
            if cached is None or cached.empty:
                logger.info("Seeding candle history store for %s (%s, %s).", symbol, market, interval)
                frame, contract = self.fetch_equity_history(
                    symbol,
                    interval=interval,
                    data_range=f"{max(lookback_days, 1)}d",
                    market=market,
                    end_ist=end_ist,
                )
                if frame is not None and not frame.empty:
                    frame = frame.tail(max_history_bars)
                    store.write_candle_history(interval, symbol, market, interval, frame, retention_days=retention_days, now=end_ist)
            else:
                try:
                    fresh_days = fetch_days
                    cached = cached.tail(max_history_bars)
                    logger.info(
                        "Refreshing candle history store tail for %s (%s, %s) with last %s day(s).",
                        symbol,
                        market,
                        interval,
                        fresh_days,
                    )
                    fresh, contract = self.fetch_equity_history(
                        symbol,
                        interval=interval,
                        data_range=f"{fresh_days}d",
                        market=market,
                        end_ist=end_ist,
                    )
                    frame = _merge_equity_history_frames(
                        cached=cached,
                        fresh=fresh,
                        refresh_days=fresh_days,
                        window_days=retention_days,
                        end_ist=end_ist,
                    )
                    if frame is not None and not frame.empty:
                        frame = frame.tail(max_history_bars)
                except Exception as exc:
                    logger.warning("Falling back to stored candle history for %s: %s", symbol, exc)
                    frame = cached
        if frame is None or frame.empty:
            frame, contract = self.fetch_equity_history(
                symbol,
                interval=interval,
                data_range=f"{lookback_days}d",
                market=market,
                end_ist=end_ist,
            )
        if frame is not None and not frame.empty:
            frame = frame.tail(max_history_bars)
        frame = _filter_frame_to_as_of_date(frame, as_of_date)
        if frame is None or frame.empty:
            label = as_of_date.isoformat() if as_of_date else "latest"
            raise RuntimeError(f"No OHLCV history available for {symbol} on or before {label}")
        work = frame.copy()
        work["ema9"] = _to_numeric(work["close"]).ewm(span=9, adjust=False).mean()
        work["vwap"] = _compute_vwap(work.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}))
        work["rsi14"] = _compute_rsi(work.rename(columns={"close": "Close"}))
        work = _compute_body_metrics(work)
        latest = work.iloc[-1]
        candle_time = latest.name
        candle_time_ist = candle_time.tz_convert(IST).isoformat() if hasattr(candle_time, "tz_convert") else None
        recent_bars = _as_record_dict(work, limit=5)
        close_price = _safe_float(latest.get("close"), 4)
        vwap = _safe_float(latest.get("vwap"), 4)
        ema9 = _safe_float(latest.get("ema9"), 4)
        rsi14 = _safe_float(latest.get("rsi14"), 2)
        volume = _safe_float(latest.get("volume"), 0)
        body_size = _safe_float(latest.get("body_size"), 4)
        body_avg_40 = _safe_float(latest.get("body_avg_40") or latest.get("body_avg_14"), 4)
        is_big_candle = bool(latest.get("is_big_candle")) if not pd.isna(latest.get("is_big_candle")) else False
        spot_ref = close_price if close_price is not None else _safe_float(latest.get("close"), 4)
        option_chain = None
        if fetch_option_chain:
            option_chain = self.fetch_option_chain_snapshot(
                symbol=symbol,
                spot_close=float(spot_ref) if spot_ref is not None else 0.0,
                market=market,
                data_range=f"{option_lookback_days}d",
                end_ist=end_ist,
                fast_mode=fast_mode,
                fast_strike_window=fast_strike_window,
            )
        return SymbolSnapshot(
            symbol=symbol,
            interval=interval,
            candle_time_ist=candle_time_ist,
            recent_bars=recent_bars,
            close=close_price,
            vwap=vwap,
            ema9=ema9,
            rsi14=rsi14,
            volume=volume,
            body_size=body_size,
            body_avg_14=body_avg_40,
            body_avg_40=body_avg_40,
            is_big_candle=is_big_candle,
            option_chain=option_chain,
        )


def dhanhq(client_id: str, access_token: str) -> DhanRealtimeClient:
    return DhanRealtimeClient(client_id, access_token)


def _render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))

    def fmt(row: dict[str, Any]) -> str:
        return " | ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)

    header = fmt({col: col for col in columns})
    sep = "-+-".join("-" * widths[col] for col in columns)
    body = [fmt(row) for row in rows]
    return "\n".join([header, sep, *body]) if body else "\n".join([header, sep])


def run_once(
    client: DhanRealtimeClient,
    watchlist: dict[str, int | None],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    option_lookback_days: int = 2,
    as_of_date: date | None = None,
    debug_rejections: bool = False,
    pattern_filter: str | None = None,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
    gate3_alerts: bool = False,
    personal_alerts_only: bool = False,
    setup_alerts: bool = DEFAULT_SETUP_ALERTS,
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    source_strategy_id: str | None = None,
    source_pool_symbols: set[str] | None = None,
    store_timeframe: str = DEFAULT_STORE_TIMEFRAME,
) -> dict[str, Any]:
    symbols = list(watchlist.keys())
    source_pool_symbols = {str(sym).strip().upper() for sym in (source_pool_symbols or set()) if str(sym).strip()}
    snapshots: list[dict[str, Any]] = []
    triggered_signals: list[dict[str, Any]] = []
    gate12_alert_candidates: list[dict[str, Any]] = []
    gate3_alert_candidates: list[dict[str, Any]] = []
    total = len(symbols)
    batch_size = max(1, int(os.environ.get("DHAN_SCAN_BATCH_SIZE", "15")))
    allowed_patterns = {pattern_filter} if pattern_filter else None
    gate3_state = _load_gate3_state()
    last_gate3_meta_map = gate3_state.get("last_gate3_meta_map")
    if not isinstance(last_gate3_meta_map, dict):
        last_gate3_meta_map = {}
    last_gate12_meta_map = gate3_state.get("last_gate12_meta_map")
    if not isinstance(last_gate12_meta_map, dict):
        last_gate12_meta_map = {}
    history_store = MarketSnapshotStore() if MarketSnapshotStore is not None else None
    if not SCANNED_SIGNALS_CSV.exists():
        SCANNED_SIGNALS_CSV.parent.mkdir(parents=True, exist_ok=True)
        with SCANNED_SIGNALS_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["TIMESTAMP", "TICKER", "PATTERN", "ENTRY", "SL", "TARGET", "PCR", "SCORE"])

    def _process_symbol(symbol: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
        try:
            snapshot = client.snapshot_symbol(
                symbol=symbol,
                interval=interval,
                lookback_days=lookback_days,
                market=market,
                option_lookback_days=option_lookback_days,
                as_of_date=as_of_date,
                fetch_option_chain=False,
                history_store=history_store,
            )
        except Exception as exc:
            logger.warning("Skipping %s: %s", symbol, exc)
            return None, None, None, None

        payload = snapshot.as_dict(include_recent_bars=False, include_option_chain=False)
        pre_strategy = _evaluate_strategy(snapshot, allowed_patterns=allowed_patterns)
        payload["pre_strategy"] = pre_strategy
        if not (pre_strategy["gate1_pass"] and pre_strategy["gate2_pass"]):
            if debug_rejections:
                reasons = _pre_option_rejection_reasons(snapshot, pre_strategy)
                logger.info("%s rejected: %s", symbol, " | ".join(reasons) if reasons else "No qualifying setup")
            payload["strategy"] = pre_strategy
            return payload, None, None, None

        gate12_alert = None
        if setup_alerts:
            direction = str(pre_strategy.get("direction") or "").strip().upper()
            gate12_pass = bool(pre_strategy.get("gate1_pass") and pre_strategy.get("gate2_pass"))
            if gate12_pass and direction in {"BULLISH", "BEARISH", "BOTH"}:
                source_note = (
                    "From 9 EMA strategy pool"
                    if symbol.upper() in source_pool_symbols
                    else "Pattern-only universe"
                )
                signature = _gate12_trigger_signature(symbol, snapshot, pre_strategy)
                previous_meta = last_gate12_meta_map.get(symbol.upper())
                if not isinstance(previous_meta, dict):
                    previous_meta = None
                if _is_fresh_gate12(previous_meta, signature):
                    gate12_alert = {
                        "symbol": symbol.upper(),
                        "signature": signature,
                        "group_message": _format_gate12_group_message(symbol, snapshot, pre_strategy, strategy_name, source_note=source_note),
                        "personal_message": _format_gate12_personal_message(symbol, snapshot, pre_strategy, strategy_name, source_note=source_note),
                        "strategy": pre_strategy,
                        "source_note": source_note,
                    }

        try:
            logger.info("Fetching option-chain confirmation for %s...", symbol)
            snapshot.option_chain = client.fetch_option_chain_snapshot(
                symbol=symbol,
                spot_close=float(snapshot.close or 0.0),
                market=market,
                data_range=f"{option_lookback_days}d",
                end_ist=_as_of_end_datetime(as_of_date),
                fast_mode=fast_mode,
                fast_strike_window=fast_strike_window,
            )
        except Exception as exc:
            logger.warning("Skipping %s option-chain fetch: %s", symbol, exc)
            payload["strategy"] = pre_strategy
            return payload, None, gate12_alert, None

        strategy = _evaluate_strategy(snapshot, allowed_patterns=allowed_patterns)
        logger.info(
            "Gate 3 %s for %s (%s)",
            "pass" if strategy.get("gate3_pass") else "fail",
            symbol,
            strategy.get("direction") or "UNKNOWN",
        )
        logger.info(
            "Gate 4 %s for %s (%s)",
            "pass" if strategy.get("gate4_pass") else "fail",
            symbol,
            strategy.get("direction") or "UNKNOWN",
        )
        payload["strategy"] = strategy
        signal_row = None
        if strategy["strategy_pass"]:
            signal_row = {
                "TIMESTAMP": snapshot.candle_time_ist or datetime.now(IST).isoformat(),
                "TICKER": symbol,
                "PATTERN": strategy["pattern"] or "Strategy Match",
                "ENTRY": strategy["entry"],
                "SL": strategy["stop_loss"],
                "TARGET": strategy["target"],
                "PCR": strategy["pcr"],
                "SCORE": strategy["score"],
            }
        elif debug_rejections:
            reasons = _rejection_reasons(snapshot, strategy)
            logger.info("%s rejected: %s", symbol, " | ".join(reasons) if reasons else "No qualifying setup")
        gate3_alert = None
        if gate3_alerts:
            direction = str(strategy.get("direction") or "").strip().upper()
            gate3_pass = bool(strategy.get("gate1_pass") and strategy.get("gate2_pass") and strategy.get("gate3_pass"))
            if gate3_pass and direction in {"BULLISH", "BEARISH", "BOTH"}:
                source_note = (
                    "From 9 EMA strategy pool"
                    if symbol.upper() in source_pool_symbols
                    else "Pattern-only universe"
                )
                signature = _gate3_trigger_signature(symbol, snapshot, strategy)
                previous_meta = last_gate3_meta_map.get(symbol.upper())
                if not isinstance(previous_meta, dict):
                    previous_meta = None
                if _is_fresh_gate3(previous_meta, signature):
                    gate3_alert = {
                        "symbol": symbol.upper(),
                        "signature": signature,
                        "group_message": _format_gate3_group_message(symbol, snapshot, strategy, payload.get("contract"), strategy_name, source_note=source_note),
                        "personal_message": _format_gate3_personal_message(symbol, snapshot, strategy, payload.get("contract"), strategy_name, source_note=source_note),
                        "strategy": strategy,
                        "source_note": source_note,
                    }
        return payload, signal_row, gate12_alert, gate3_alert

    max_workers = min(DEFAULT_SCAN_WORKERS, max(1, total))
    logger.info(
        "Firing concurrent scan across %s symbols using %s workers (batch size %s).",
        total,
        max_workers,
        batch_size,
    )
    completed = 0
    for batch_start in range(0, total, batch_size):
        batch_symbols = symbols[batch_start : batch_start + batch_size]
        if not batch_symbols:
            continue
        logger.info("Scanning batch %s-%s of %s symbols.", batch_start + 1, batch_start + len(batch_symbols), total)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(batch_symbols))) as executor:
            future_to_symbol = {executor.submit(_process_symbol, symbol): symbol for symbol in batch_symbols}
            for future in concurrent.futures.as_completed(future_to_symbol):
                completed += 1
                symbol = future_to_symbol[future]
                logger.info("Scanning progress %s/%s: %s", completed, total, symbol)
                payload, signal_row, gate12_alert, gate3_alert = future.result()
                if payload is not None:
                    snapshots.append(payload)
                if signal_row is not None:
                    triggered_signals.append(signal_row)
                if gate12_alert is not None:
                    gate12_alert_candidates.append(gate12_alert)
                if gate3_alert is not None:
                    gate3_alert_candidates.append(gate3_alert)
                del payload, signal_row, gate12_alert, gate3_alert
        gc.collect()

    for signal_row in triggered_signals:
        _append_signal_csv(SCANNED_SIGNALS_CSV, signal_row)

    if gate12_alert_candidates:
        for alert in gate12_alert_candidates:
            symbol = str(alert.get("symbol") or "").upper()
            signature = alert.get("signature")
            group_message = str(alert.get("group_message") or "").strip()
            personal_message = str(alert.get("personal_message") or "").strip()
            if group_message:
                sent_group = _send_telegram_to(os.getenv("TELEGRAM_TRADE_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID"), group_message)
                logger.info("Gate 1/2 setup group alert %s for %s", "sent" if sent_group else "not sent", symbol)
            if personal_message:
                personal_chat_id = (
                    os.getenv("TELEGRAM_PERSONAL_CHAT_ID")
                    or os.getenv("TELEGRAM_STATUS_CHAT_ID")
                    or os.getenv("TELEGRAM_CHAT_ID")
                )
                sent_personal = _send_telegram_to(personal_chat_id, personal_message)
                logger.info("Gate 1/2 setup personal alert %s for %s", "sent" if sent_personal else "not sent", symbol)
            if symbol and isinstance(signature, dict):
                last_gate12_meta_map[symbol] = signature

    if gate3_alerts and gate3_alert_candidates:
        for alert in gate3_alert_candidates:
            symbol = str(alert.get("symbol") or "").upper()
            signature = alert.get("signature")
            group_message = str(alert.get("group_message") or "").strip()
            personal_message = str(alert.get("personal_message") or "").strip()
            if group_message and not personal_alerts_only:
                sent_group = _send_telegram_to(os.getenv("TELEGRAM_TRADE_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID"), group_message)
                logger.info("Gate 3 group alert %s for %s", "sent" if sent_group else "not sent", symbol)
            if personal_message:
                personal_chat_id = (
                    os.getenv("TELEGRAM_PERSONAL_CHAT_ID")
                    or os.getenv("TELEGRAM_STATUS_CHAT_ID")
                    or os.getenv("TELEGRAM_CHAT_ID")
                )
                sent_personal = _send_telegram_to(personal_chat_id, personal_message)
                logger.info("Gate 3 personal alert %s for %s", "sent" if sent_personal else "not sent", symbol)
            if symbol and isinstance(signature, dict):
                last_gate3_meta_map[symbol] = signature
        _save_gate3_state(
            {
                "last_gate12_meta_map": last_gate12_meta_map,
                "last_gate3_meta_map": last_gate3_meta_map,
                "last_gate3_at": datetime.now(IST).isoformat(),
                "last_gate12_at": datetime.now(IST).isoformat(),
                "gate12_alert_count": len(gate12_alert_candidates),
                "gate3_alert_count": len(gate3_alert_candidates),
            }
        )
    elif gate12_alert_candidates:
        _save_gate3_state(
            {
                "last_gate12_meta_map": last_gate12_meta_map,
                "last_gate3_meta_map": last_gate3_meta_map,
                "last_gate12_at": datetime.now(IST).isoformat(),
                "gate12_alert_count": len(gate12_alert_candidates),
                "gate3_alert_count": len(gate3_alert_candidates),
            }
        )

    triggered_signals.sort(key=lambda row: (-float(row.get("SCORE") or 0), str(row.get("TIMESTAMP") or "")))
    rows = [
        {
            "Stock": signal["TICKER"],
            "Timeframe": interval,
            "Candle Time IST": next(
                (
                    snap.get("candle_time_ist")
                    for snap in snapshots
                    if snap.get("symbol") == signal["TICKER"]
                ),
                "",
            ),
            "Gate 1": "Pass" if next(
                (
                    snap.get("strategy", {}).get("gate1_pass")
                    for snap in snapshots
                    if snap.get("symbol") == signal["TICKER"]
                ),
                False,
            ) else "Fail",
            "Gate 2": "Pass" if next(
                (
                    snap.get("strategy", {}).get("gate2_pass")
                    for snap in snapshots
                    if snap.get("symbol") == signal["TICKER"]
                ),
                False,
            ) else "Fail",
            "Direction": next(
                (
                    snap.get("strategy", {}).get("direction")
                    for snap in snapshots
                    if snap.get("symbol") == signal["TICKER"]
                ),
                "",
            ),
            "Pattern": signal["PATTERN"],
            "Score": signal.get("SCORE", ""),
            "Entry": signal["ENTRY"] if signal["ENTRY"] is not None else "",
            "SL": signal["SL"] if signal["SL"] is not None else "",
            "Target": signal["TARGET"] if signal["TARGET"] is not None else "",
            "PCR": signal["PCR"] if signal["PCR"] is not None else "",
        }
        for signal in triggered_signals
    ]
    columns = [
        "Stock",
        "Timeframe",
        "Candle Time IST",
        "Gate 1",
        "Gate 2",
        "Direction",
        "Pattern",
        "Score",
        "Entry",
        "SL",
        "Target",
        "PCR",
    ]
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_name": strategy_name,
        "source_strategy_id": source_strategy_id or "",
        "interval": interval,
        "lookback_days": lookback_days,
        "market": market,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "fast_mode": bool(fast_mode),
        "fast_strike_window": fast_strike_window,
        "watchlist": list(watchlist.keys()),
        "snapshots": snapshots,
        "triggered_signals": triggered_signals,
        "gate12_alerts_enabled": bool(setup_alerts),
        "gate12_alerts": gate12_alert_candidates,
        "gate3_alerts_enabled": bool(gate3_alerts),
        "gate3_alerts": gate3_alert_candidates,
    }
    out_path = OUTPUT_DIR / "dhan_realtime_snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    _publish_market_snapshot_store(
        {
            "generated_at": output["generated_at"],
            "strategy_name": strategy_name,
            "source_strategy_id": source_strategy_id or "",
            "interval": interval,
            "lookback_days": lookback_days,
            "market": market,
            "fast_mode": bool(fast_mode),
            "fast_strike_window": fast_strike_window,
            "watchlist": list(watchlist.keys()),
            "snapshots": snapshots,
            "triggered_signals": triggered_signals,
            "gate12_alerts_enabled": bool(setup_alerts),
            "gate12_alerts": gate12_alert_candidates,
            "gate3_alerts_enabled": bool(gate3_alerts),
            "gate3_alerts": gate3_alert_candidates,
        },
        timeframe=store_timeframe,
    )

    if not rows:
        print("No triggered signals found.")
    else:
        print(_render_table(rows, columns))
    print(f"\nSaved snapshot to {out_path}")
    return output


def run_repeat_pattern_once(
    client: DhanRealtimeClient,
    watchlist: dict[str, int | None],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    buffer_seconds: float = DEFAULT_BUFFER_SECONDS,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> dict[str, Any]:
    symbols = list(watchlist.keys())
    total = len(symbols)
    state = _load_repeat_pattern_state()
    last_meta_map = state.get("last_repeat_pattern_meta_map")
    if not isinstance(last_meta_map, dict):
        last_meta_map = {}

    alert_rows: list[dict[str, Any]] = []
    max_workers = min(DEFAULT_SCAN_WORKERS, max(1, total))
    logger.info("Repeat-pattern scan across %s symbols using %s workers.", total, max_workers)

    def _process_symbol(symbol: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        try:
            snapshot = client.snapshot_symbol(
                symbol=symbol,
                interval=interval,
                lookback_days=lookback_days,
                market=market,
                fetch_option_chain=False,
                fast_mode=fast_mode,
                fast_strike_window=fast_strike_window,
            )
        except Exception as exc:
            logger.warning("Skipping %s: %s", symbol, exc)
            return None, None

        payload = snapshot.as_dict()
        strategy = _evaluate_strategy(snapshot)
        pattern = str(strategy.get("gate1_pattern") or strategy.get("pattern") or "")
        strategy["pattern_tag"] = _pattern_tag(pattern)
        payload["strategy"] = strategy
        direction = str(strategy.get("direction") or "").upper()
        gate1_pass = bool(strategy.get("gate1_pass"))
        if not gate1_pass or direction not in {"BULLISH", "BEARISH"}:
            return payload, None

        candle_time = str(snapshot.candle_time_ist or "")
        day_key = candle_time[:10] if candle_time else datetime.now(IST).date().isoformat()
        symbol_key = symbol.upper()
        symbol_state = last_meta_map.get(symbol_key)
        if not isinstance(symbol_state, dict) or symbol_state.get("day_key") != day_key:
            symbol_state = {
                "day_key": day_key,
                "bullish": {"sequence_no": 0, "last_candle_time_ist": None},
                "bearish": {"sequence_no": 0, "last_candle_time_ist": None},
            }

        pattern_tag = str(strategy.get("pattern_tag") or _pattern_tag(pattern))
        slot_key = "bullish" if pattern_tag == "BULLISH_PATTERN" else "bearish"
        slot = symbol_state.get(slot_key)
        if not isinstance(slot, dict):
            slot = {"sequence_no": 0, "last_candle_time_ist": None}

        if str(slot.get("last_candle_time_ist") or "") == candle_time:
            last_meta_map[symbol_key] = symbol_state
            return payload, None

        sequence_no = int(slot.get("sequence_no") or 0) + 1
        slot["sequence_no"] = sequence_no
        slot["last_candle_time_ist"] = candle_time
        slot["last_pattern"] = str(strategy.get("gate1_pattern") or strategy.get("pattern") or "UNAVAILABLE")
        slot["pattern_tag"] = pattern_tag
        symbol_state[slot_key] = slot
        symbol_state["day_key"] = day_key
        last_meta_map[symbol_key] = symbol_state

        if sequence_no < 2:
            return payload, None

        signature = {
            "symbol": symbol_key,
            "day_key": day_key,
            "direction": direction,
            "pattern_tag": pattern_tag,
            "sequence_no": sequence_no,
            "candle_time_ist": candle_time,
            "pattern": str(strategy.get("gate1_pattern") or strategy.get("pattern") or "UNAVAILABLE"),
        }
        previous_meta = slot.get("last_repeat_signature")
        if isinstance(previous_meta, dict):
            if (
                str(previous_meta.get("candle_time_ist") or "") == candle_time
                and int(previous_meta.get("sequence_no") or 0) == sequence_no
            ):
                return payload, None

        slot["last_repeat_signature"] = signature
        alert = {
            "symbol": symbol_key,
            "sequence_no": sequence_no,
            "signature": signature,
            "group_message": _repeat_pattern_message(symbol, snapshot, strategy, sequence_no),
            "personal_message": _repeat_pattern_message(symbol, snapshot, strategy, sequence_no),
            "strategy": strategy,
        }
        return payload, alert

    snapshots: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {executor.submit(_process_symbol, symbol): symbol for symbol in symbols}
        completed = 0
        for future in concurrent.futures.as_completed(future_to_symbol):
            completed += 1
            symbol = future_to_symbol[future]
            logger.info("Repeat-pattern progress %s/%s: %s", completed, total, symbol)
            payload, alert = future.result()
            if payload is not None:
                snapshots.append(payload)
            if alert is not None:
                alert_rows.append(alert)

    if alert_rows:
        for alert in alert_rows:
            symbol = str(alert.get("symbol") or "").upper()
            group_message = str(alert.get("group_message") or "").strip()
            personal_message = str(alert.get("personal_message") or "").strip()
            if group_message:
                sent_group = _send_telegram_to(os.getenv("TELEGRAM_TRADE_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID"), group_message)
                logger.info("Repeat-pattern group alert %s for %s", "sent" if sent_group else "not sent", symbol)
            if personal_message:
                personal_chat_id = (
                    os.getenv("TELEGRAM_PERSONAL_CHAT_ID")
                    or os.getenv("TELEGRAM_STATUS_CHAT_ID")
                    or os.getenv("TELEGRAM_CHAT_ID")
                )
                sent_personal = _send_telegram_to(personal_chat_id, personal_message)
                logger.info("Repeat-pattern personal alert %s for %s", "sent" if sent_personal else "not sent", symbol)
        _save_repeat_pattern_state(
            {
                "last_repeat_pattern_meta_map": last_meta_map,
                "last_repeat_pattern_at": datetime.now(IST).isoformat(),
                "repeat_pattern_alert_count": len(alert_rows),
            }
        )

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interval": interval,
        "lookback_days": lookback_days,
        "market": market,
        "watchlist": list(watchlist.keys()),
        "snapshots": snapshots,
        "repeat_pattern_alerts": alert_rows,
    }
    out_path = OUTPUT_DIR / "repeat_pattern_alerts.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"Saved repeat-pattern scan to {out_path}")
    return output


def run_repeat_pattern_forever(
    client: DhanRealtimeClient,
    watchlist: dict[str, int | None],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    buffer_seconds: float = DEFAULT_BUFFER_SECONDS,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> None:
    while True:
        now = datetime.now(IST)
        if _is_market_open(now, close_buffer_seconds=buffer_seconds):
            run_repeat_pattern_once(
                client=client,
                watchlist=watchlist,
                interval=interval,
                lookback_days=lookback_days,
                market=market,
                buffer_seconds=buffer_seconds,
                fast_mode=fast_mode,
                fast_strike_window=fast_strike_window,
            )
        else:
            logger.info("Market closed; waiting for the next open window.")
        sleep_seconds = _seconds_until_next_scan(buffer_seconds=buffer_seconds)
        logger.info("Sleeping %.1f seconds until next repeat-pattern scan.", sleep_seconds)
        time.sleep(sleep_seconds)


def run_history_scan(
    client: DhanRealtimeClient,
    symbols: list[str],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    pattern_filter: str | None = None,
    scan_all_patterns: bool = False,
    as_of_date: date | None = None,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("at least one symbol is required for historical scan")

    end_ist = _as_of_end_datetime(as_of_date)
    max_workers = min(DEFAULT_SCAN_WORKERS, max(1, len(symbols)))
    logger.info("Historical sweep across %s symbols using %s workers.", len(symbols), max_workers)

    def _scan_symbol(symbol: str) -> dict[str, Any]:
        frame, contract = client.fetch_equity_history(
            symbol,
            interval=interval,
            data_range=f"{lookback_days}d",
            market=market,
            end_ist=end_ist,
        )
        frame = _filter_frame_to_as_of_date(frame, as_of_date)
        if frame is None or frame.empty:
            return {
                "symbol": symbol,
                "contract": contract,
                "frame": frame,
                "work": frame,
                "records": [],
            }
        work = frame.copy().sort_values("dt_utc").reset_index(drop=True)
        work["ema9"] = _to_numeric(work["close"]).ewm(span=9, adjust=False).mean()
        work["vwap"] = _compute_vwap(work.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}))
        work["rsi14"] = _compute_rsi(work.rename(columns={"close": "Close"}))
        work = _compute_body_metrics(work)
        return {
            "symbol": symbol,
            "contract": contract,
            "frame": frame,
            "work": work,
            "records": _as_record_dict(work, limit=len(work)),
        }

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {executor.submit(_scan_symbol, symbol): symbol for symbol in symbols}
        completed = 0
        total = len(symbols)
        for future in concurrent.futures.as_completed(future_to_symbol):
            completed += 1
            symbol = future_to_symbol[future]
            logger.info("Historical progress %s/%s: %s", completed, total, symbol)
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning("Skipping %s: %s", symbol, exc)

    if pattern_filter:
        patterns_to_scan = [pattern_filter]
    else:
        patterns_to_scan = list(PATTERN_CATALOG)

    pattern_runs: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    option_chain_cache: dict[tuple[str, str], OptionChainSnapshot | None] = {}
    for idx, pattern in enumerate(patterns_to_scan, start=1):
        logger.info("Historical pattern %s/%s: %s", idx, len(patterns_to_scan), pattern)
        pattern_hits: list[dict[str, Any]] = []
        for result in results:
            symbol = result["symbol"]
            frame = result.get("frame")
            work = result.get("work")
            if frame is None or frame.empty or work is None or work.empty:
                continue
            logger.info("Scanning %s for %s...", pattern, symbol)
            candidate_hits = _scan_history_occurrences(frame, symbol=symbol, interval=interval, pattern_filter=pattern)
            logger.info("Found %s candidate bars for %s on %s", len(candidate_hits), pattern, symbol)
            validated_count = 0
            logger.info("Historical pattern %s candidate matches for %s: %s", pattern, symbol, len(candidate_hits))
            for candidate in candidate_hits:
                bar_index = candidate.get("bar_index")
                if bar_index is None:
                    continue
                snapshot = _historical_snapshot_from_work(work, int(bar_index), symbol=symbol, interval=interval)
                pre_strategy = _evaluate_strategy(snapshot, allowed_patterns={pattern})
                if not (pre_strategy["gate1_pass"] and pre_strategy["gate2_pass"]):
                    continue
                candle_key = (symbol, str(candidate.get("candle_time_ist") or ""))
                if candle_key not in option_chain_cache:
                    try:
                        candle_dt = pd.Timestamp(candidate.get("candle_time_ist")).to_pydatetime()
                        if getattr(candle_dt, "tzinfo", None) is None:
                            candle_dt = candle_dt.replace(tzinfo=IST)
                        logger.info("Fetching option-chain confirmation for %s at %s...", symbol, candle_key[1])
                        option_chain_cache[candle_key] = client.fetch_option_chain_snapshot(
                            symbol=symbol,
                            spot_close=float(snapshot.close or 0.0),
                            market=market,
                            data_range=f"{lookback_days}d",
                            end_ist=candle_dt,
                            fast_mode=fast_mode,
                            fast_strike_window=fast_strike_window,
                        )
                    except Exception as exc:
                        logger.debug("Skipping option-chain fetch for %s at %s: %s", symbol, candle_key[1], exc)
                        option_chain_cache[candle_key] = None
                snapshot.option_chain = option_chain_cache[candle_key]
                strategy = _evaluate_strategy(snapshot, allowed_patterns={pattern})
                logger.info(
                    "Gate 3 %s for %s on %s",
                    "pass" if strategy.get("gate3_pass") else "fail",
                    symbol,
                    pattern,
                )
                logger.info(
                    "Gate 4 %s for %s on %s",
                    "pass" if strategy.get("gate4_pass") else "fail",
                    symbol,
                    pattern,
                )
                if not strategy["strategy_pass"]:
                    continue
                validated_count += 1
                pattern_hits.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "pattern": strategy["pattern"],
                        "candle_time_ist": candidate.get("candle_time_ist"),
                        "gate1_pass": strategy["gate1_pass"],
                        "gate2_pass": strategy["gate2_pass"],
                        "gate3_pass": strategy["gate3_pass"],
                        "gate4_pass": strategy["gate4_pass"],
                        "direction": strategy.get("direction"),
                        "score": strategy.get("score"),
                        "entry": strategy.get("entry"),
                        "stop_loss": strategy.get("stop_loss"),
                        "target": strategy.get("target"),
                        "pcr": strategy.get("pcr"),
                        "open": candidate.get("open"),
                        "high": candidate.get("high"),
                        "low": candidate.get("low"),
                        "close": candidate.get("close"),
                        "volume": candidate.get("volume"),
                        "vwap": candidate.get("vwap"),
                        "ema9": candidate.get("ema9"),
                    }
                )
            logger.info("Validated %s hit(s) for %s on %s", validated_count, pattern, symbol)
        pattern_hits.sort(key=lambda row: (str(row.get("symbol") or ""), str(row.get("candle_time_ist") or "")))
        pattern_runs.append({"pattern": pattern, "hits": pattern_hits})
        hits.extend(pattern_hits)

    hits.sort(key=lambda row: (str(row.get("pattern") or ""), str(row.get("symbol") or ""), str(row.get("candle_time_ist") or "")))

    out_path = OUTPUT_DIR / "historical_pattern_hits.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_results: list[dict[str, Any]] = []
    for result in results:
        frame = result.get("frame")
        serialized_results.append(
            {
                "symbol": result.get("symbol"),
                "contract": result.get("contract"),
                "rows": int(len(frame)) if frame is not None else 0,
                "candle_start": _iso_dt(frame.iloc[0]["dt_ist"]) if frame is not None and not frame.empty else None,
                "candle_end": _iso_dt(frame.iloc[-1]["dt_ist"]) if frame is not None and not frame.empty else None,
            }
        )

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "interval": interval,
        "lookback_days": lookback_days,
        "market": market,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "fast_mode": bool(fast_mode),
        "fast_strike_window": fast_strike_window,
        "pattern_filter": pattern_filter or None,
        "scan_all_patterns": bool(scan_all_patterns),
        "patterns_scanned": patterns_to_scan,
        "results": serialized_results,
        "pattern_runs": pattern_runs,
        "hits": hits,
    }
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    if not hits:
        print(f"No historical {', '.join(patterns_to_scan)} hits found.")
    else:
        for run in pattern_runs:
            print(f"\n### {run['pattern']}")
            pattern_hits = run["hits"]
            if not pattern_hits:
                print("No hits.")
                continue
            print(
                _render_table(
                    pattern_hits,
                    [
                        "symbol",
                        "interval",
                        "pattern",
                        "candle_time_ist",
                        "gate1_pass",
                        "gate2_pass",
                        "gate3_pass",
                        "gate4_pass",
                        "direction",
                        "score",
                        "entry",
                        "stop_loss",
                        "target",
                        "pcr",
                        "open",
                        "high",
                        "low",
                        "close",
                        "vwap",
                        "ema9",
                        "volume",
                    ],
                )
            )
    print(f"\nSaved historical hits to {out_path}")
    return output


def run_gate3_history_scan(
    client: DhanRealtimeClient,
    symbols: list[str],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    as_of_date: date | None = None,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("at least one symbol is required for historical gate 3 scan")

    end_ist = _as_of_end_datetime(as_of_date)
    max_workers = min(DEFAULT_SCAN_WORKERS, max(1, len(symbols)))
    logger.info("Historical Gate 3 sweep across %s symbols using %s workers.", len(symbols), max_workers)

    def _scan_symbol(symbol: str) -> dict[str, Any]:
        frame, contract = client.fetch_equity_history(
            symbol,
            interval=interval,
            data_range=f"{lookback_days}d",
            market=market,
            end_ist=end_ist,
        )
        frame = _filter_frame_to_as_of_date(frame, as_of_date)
        if frame is None or frame.empty:
            return {
                "symbol": symbol,
                "contract": contract,
                "frame": frame,
                "work": frame,
                "rows": [],
            }
        work = frame.copy().sort_values("dt_utc").reset_index(drop=True)
        work["ema9"] = _to_numeric(work["close"]).ewm(span=9, adjust=False).mean()
        work["vwap"] = _compute_vwap(work.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}))
        work["rsi14"] = _compute_rsi(work.rename(columns={"close": "Close"}))
        work = _compute_body_metrics(work)
        return {
            "symbol": symbol,
            "contract": contract,
            "frame": frame,
            "work": work,
            "rows": [],
        }

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {executor.submit(_scan_symbol, symbol): symbol for symbol in symbols}
        completed = 0
        total = len(symbols)
        for future in concurrent.futures.as_completed(future_to_symbol):
            completed += 1
            symbol = future_to_symbol[future]
            logger.info("Historical Gate 3 progress %s/%s: %s", completed, total, symbol)
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning("Skipping %s: %s", symbol, exc)

    rows: list[dict[str, Any]] = []
    option_chain_cache: dict[tuple[str, str], OptionChainSnapshot | None] = {}
    for result in results:
        symbol = result["symbol"]
        work = result.get("work")
        if work is None or work.empty:
            continue
        logger.info("Scanning Gate 3 history for %s...", symbol)
        hits_for_symbol = 0
        for idx in range(len(work)):
            snapshot = _historical_snapshot_from_work(work, idx, symbol=symbol, interval=interval)
            candle_key = (symbol, str(snapshot.candle_time_ist or ""))
            if candle_key not in option_chain_cache:
                try:
                    candle_dt = pd.Timestamp(snapshot.candle_time_ist).to_pydatetime()
                    if getattr(candle_dt, "tzinfo", None) is None:
                        candle_dt = candle_dt.replace(tzinfo=IST)
                    logger.info("Fetching option-chain confirmation for %s at %s...", symbol, candle_key[1])
                    option_chain_cache[candle_key] = client.fetch_option_chain_snapshot(
                        symbol=symbol,
                        spot_close=float(snapshot.close or 0.0),
                        market=market,
                        data_range=f"{lookback_days}d",
                        end_ist=candle_dt,
                        fast_mode=fast_mode,
                        fast_strike_window=fast_strike_window,
                    )
                except Exception as exc:
                    logger.debug("Skipping option-chain fetch for %s at %s: %s", symbol, candle_key[1], exc)
                    option_chain_cache[candle_key] = None

            snapshot.option_chain = option_chain_cache[candle_key]
            gate3 = _gate3_metrics(snapshot.option_chain)
            bullish_pass = gate3["bullish_gate3"]
            bearish_pass = gate3["bearish_gate3"]
            if not (bullish_pass or bearish_pass):
                continue

            hits_for_symbol += 1
            rows.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "candle_time_ist": snapshot.candle_time_ist,
                    "close": snapshot.close,
                    "atm_call_oi": snapshot.option_chain.atm_call_oi if snapshot.option_chain else None,
                    "atm_call_oi_past": snapshot.option_chain.atm_call_oi_past if snapshot.option_chain else None,
                    "atm_call_oi_strike_used": snapshot.option_chain.atm_call_oi_strike_used if snapshot.option_chain else None,
                    "atm_put_oi": snapshot.option_chain.atm_put_oi if snapshot.option_chain else None,
                    "atm_put_oi_past": snapshot.option_chain.atm_put_oi_past if snapshot.option_chain else None,
                    "atm_put_oi_strike_used": snapshot.option_chain.atm_put_oi_strike_used if snapshot.option_chain else None,
                    "call_oi_velocity_pct": gate3["call_velocity"],
                    "put_oi_velocity_pct": gate3["put_velocity"],
                    "pcr": gate3["pcr"],
                    "pcr_prev": gate3["pcr_prev"],
                    "gate3_bullish": "Pass" if bullish_pass else "Fail",
                    "gate3_bearish": "Pass" if bearish_pass else "Fail",
                }
            )
        logger.info("Validated %s Gate 3 hit(s) for %s", hits_for_symbol, symbol)

    rows.sort(key=lambda row: (str(row.get("symbol") or ""), str(row.get("candle_time_ist") or "")))
    out_path = OUTPUT_DIR / "historical_gate3_hits.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "interval": interval,
        "lookback_days": lookback_days,
        "market": market,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "fast_mode": bool(fast_mode),
        "fast_strike_window": fast_strike_window,
        "rows": rows,
    }
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    if not rows:
        print("No historical Gate 3 hits found.")
    else:
        print(
            _render_table(
                rows,
                [
                    "symbol",
                    "interval",
                    "candle_time_ist",
                    "close",
                    "atm_call_oi_strike_used",
                    "atm_call_oi",
                    "atm_call_oi_past",
                    "atm_put_oi_strike_used",
                    "atm_put_oi",
                    "atm_put_oi_past",
                    "call_oi_velocity_pct",
                    "put_oi_velocity_pct",
                    "pcr",
                    "pcr_prev",
                    "gate3_bullish",
                    "gate3_bearish",
                ],
            )
        )
    print(f"\nSaved historical Gate 3 hits to {out_path}")
    return output


def run_gate4_history_scan(
    client: DhanRealtimeClient,
    symbols: list[str],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    as_of_date: date | None = None,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("at least one symbol is required for historical gate 4 scan")

    end_ist = _as_of_end_datetime(as_of_date)
    max_workers = min(DEFAULT_SCAN_WORKERS, max(1, len(symbols)))
    logger.info("Historical Gate 4 sweep across %s symbols using %s workers.", len(symbols), max_workers)

    def _scan_symbol(symbol: str) -> dict[str, Any]:
        frame, contract = client.fetch_equity_history(
            symbol,
            interval=interval,
            data_range=f"{lookback_days}d",
            market=market,
            end_ist=end_ist,
        )
        frame = _filter_frame_to_as_of_date(frame, as_of_date)
        if frame is None or frame.empty:
            return {"symbol": symbol, "contract": contract, "frame": frame, "work": frame, "rows": []}
        work = frame.copy().sort_values("dt_utc").reset_index(drop=True)
        work["ema9"] = _to_numeric(work["close"]).ewm(span=9, adjust=False).mean()
        work["vwap"] = _compute_vwap(work.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}))
        work["rsi14"] = _compute_rsi(work.rename(columns={"close": "Close"}))
        work = _compute_body_metrics(work)
        return {"symbol": symbol, "contract": contract, "frame": frame, "work": work, "rows": []}

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {executor.submit(_scan_symbol, symbol): symbol for symbol in symbols}
        completed = 0
        total = len(symbols)
        for future in concurrent.futures.as_completed(future_to_symbol):
            completed += 1
            symbol = future_to_symbol[future]
            logger.info("Historical Gate 4 progress %s/%s: %s", completed, total, symbol)
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning("Skipping %s: %s", symbol, exc)

    rows: list[dict[str, Any]] = []
    option_chain_cache: dict[tuple[str, str], OptionChainSnapshot | None] = {}
    for result in results:
        symbol = result["symbol"]
        work = result.get("work")
        if work is None or work.empty:
            continue
        logger.info("Scanning Gate 4 history for %s...", symbol)
        hits_for_symbol = 0
        for idx in range(len(work)):
            snapshot = _historical_snapshot_from_work(work, idx, symbol=symbol, interval=interval)
            candle_key = (symbol, str(snapshot.candle_time_ist or ""))
            if candle_key not in option_chain_cache:
                try:
                    candle_dt = pd.Timestamp(snapshot.candle_time_ist).to_pydatetime()
                    if getattr(candle_dt, "tzinfo", None) is None:
                        candle_dt = candle_dt.replace(tzinfo=IST)
                    logger.info("Fetching option-chain confirmation for %s at %s...", symbol, candle_key[1])
                    option_chain_cache[candle_key] = client.fetch_option_chain_snapshot(
                        symbol=symbol,
                        spot_close=float(snapshot.close or 0.0),
                        market=market,
                        data_range=f"{lookback_days}d",
                        end_ist=candle_dt,
                        fast_mode=fast_mode,
                        fast_strike_window=fast_strike_window,
                    )
                except Exception as exc:
                    logger.debug("Skipping option-chain fetch for %s at %s: %s", symbol, candle_key[1], exc)
                    option_chain_cache[candle_key] = None

            snapshot.option_chain = option_chain_cache[candle_key]
            gate4 = _gate4_metrics(snapshot.option_chain)
            bullish_pass = gate4["bullish_gate4"]
            bearish_pass = gate4["bearish_gate4"]
            if not (bullish_pass or bearish_pass):
                continue

            hits_for_symbol += 1
            rows.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "candle_time_ist": snapshot.candle_time_ist,
                    "close": snapshot.close,
                    "pcr": gate4["pcr"],
                    "pcr_prev": gate4["pcr_prev"],
                    "gate4_reason": gate4["reason"],
                    "gate4_bullish": "Pass" if bullish_pass else "Fail",
                    "gate4_bearish": "Pass" if bearish_pass else "Fail",
                }
            )
        logger.info("Validated %s Gate 4 hit(s) for %s", hits_for_symbol, symbol)

    rows.sort(key=lambda row: (str(row.get("symbol") or ""), str(row.get("candle_time_ist") or "")))
    out_path = OUTPUT_DIR / "historical_gate4_hits.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "interval": interval,
        "lookback_days": lookback_days,
        "market": market,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "fast_mode": bool(fast_mode),
        "fast_strike_window": fast_strike_window,
        "rows": rows,
    }
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    if not rows:
        print("No historical Gate 4 hits found.")
    else:
        print(
            _render_table(
                rows,
                [
                    "symbol",
                    "interval",
                    "candle_time_ist",
                    "close",
                    "pcr",
                    "pcr_prev",
                    "gate4_reason",
                    "gate4_bullish",
                    "gate4_bearish",
                ],
            )
        )
    print(f"\nSaved historical Gate 4 hits to {out_path}")
    return output


def run_gate3_debug(
    client: DhanRealtimeClient,
    symbols: list[str],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    option_lookback_days: int = 2,
    as_of_date: date | None = None,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("at least one symbol is required for gate 3 debug")

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        logger.info("Gate 3 debug: loading %s...", symbol)
        snapshot = client.snapshot_symbol(
            symbol=symbol,
            interval=interval,
            lookback_days=lookback_days,
            market=market,
            option_lookback_days=option_lookback_days,
            as_of_date=as_of_date,
            fetch_option_chain=False,
        )
        logger.info("Fetching option-chain confirmation for %s...", symbol)
        snapshot.option_chain = client.fetch_option_chain_snapshot(
            symbol=symbol,
            spot_close=float(snapshot.close or 0.0),
            market=market,
            data_range=f"{option_lookback_days}d",
            end_ist=_as_of_end_datetime(as_of_date),
            fast_mode=fast_mode,
            fast_strike_window=fast_strike_window,
        )
        option_chain = snapshot.option_chain
        gate3 = _gate3_metrics(option_chain)
        put_velocity = gate3["put_velocity"]
        call_velocity = gate3["call_velocity"]
        bullish_gate3 = gate3["bullish_gate3"]
        bearish_gate3 = gate3["bearish_gate3"]
        row = {
            "Stock": symbol,
            "Candle Time IST": snapshot.candle_time_ist or "",
            "Close": snapshot.close if snapshot.close is not None else "",
            "ATM Call OI": option_chain.atm_call_oi if option_chain else "",
            "ATM Call OI Prev": option_chain.atm_call_oi_past if option_chain else "",
            "ATM Put OI": option_chain.atm_put_oi if option_chain else "",
            "ATM Put OI Prev": option_chain.atm_put_oi_past if option_chain else "",
            "PCR": option_chain.pcr_intraday if option_chain else "",
            "PCR Prev": option_chain.pcr_intraday_past if option_chain else "",
            "ATM Call Strike Used": option_chain.atm_call_oi_strike_used if option_chain else "",
            "ATM Put Strike Used": option_chain.atm_put_oi_strike_used if option_chain else "",
            "Call OI Velocity %": call_velocity if call_velocity is not None else "",
            "Put OI Velocity %": put_velocity if put_velocity is not None else "",
            "Gate 3 Bullish": "Pass" if bullish_gate3 else "Fail",
            "Gate 3 Bearish": "Pass" if bearish_gate3 else "Fail",
        }
        rows.append(row)

    columns = [
        "Stock",
        "Candle Time IST",
        "Close",
        "ATM Call OI",
        "ATM Call OI Prev",
        "ATM Put OI",
        "ATM Put OI Prev",
        "Call OI Velocity %",
        "Put OI Velocity %",
        "PCR",
        "PCR Prev",
        "ATM Call Strike Used",
        "ATM Put Strike Used",
        "Gate 3 Bullish",
        "Gate 3 Bearish",
    ]
    print(_render_table(rows, columns))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "interval": interval,
        "lookback_days": lookback_days,
        "market": market,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "fast_mode": bool(fast_mode),
        "fast_strike_window": fast_strike_window,
        "rows": rows,
    }


def run_gate4_debug(
    client: DhanRealtimeClient,
    symbols: list[str],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    option_lookback_days: int = 2,
    as_of_date: date | None = None,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("at least one symbol is required for gate 4 debug")

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        logger.info("Gate 4 debug: loading %s...", symbol)
        snapshot = client.snapshot_symbol(
            symbol=symbol,
            interval=interval,
            lookback_days=lookback_days,
            market=market,
            option_lookback_days=option_lookback_days,
            as_of_date=as_of_date,
            fetch_option_chain=False,
        )
        logger.info("Fetching option-chain confirmation for %s...", symbol)
        snapshot.option_chain = client.fetch_option_chain_snapshot(
            symbol=symbol,
            spot_close=float(snapshot.close or 0.0),
            market=market,
            data_range=f"{option_lookback_days}d",
            end_ist=_as_of_end_datetime(as_of_date),
            fast_mode=fast_mode,
            fast_strike_window=fast_strike_window,
        )
        gate4 = _gate4_metrics(snapshot.option_chain)
        row = {
            "Stock": symbol,
            "Candle Time IST": snapshot.candle_time_ist or "",
            "Close": snapshot.close if snapshot.close is not None else "",
            "PCR": gate4["pcr"] if gate4["pcr"] is not None else "",
            "PCR Prev": gate4["pcr_prev"] if gate4["pcr_prev"] is not None else "",
            "Gate 4 Reason": gate4["reason"],
            "Gate 4 Bullish": "Pass" if gate4["bullish_gate4"] else "Fail",
            "Gate 4 Bearish": "Pass" if gate4["bearish_gate4"] else "Fail",
        }
        rows.append(row)

    columns = ["Stock", "Candle Time IST", "Close", "PCR", "PCR Prev", "Gate 4 Reason", "Gate 4 Bullish", "Gate 4 Bearish"]
    print(_render_table(rows, columns))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "interval": interval,
        "lookback_days": lookback_days,
        "market": market,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "fast_mode": bool(fast_mode),
        "fast_strike_window": fast_strike_window,
        "rows": rows,
    }


def run_forever(
    client: DhanRealtimeClient,
    watchlist: dict[str, int | None],
    interval: str = "15m",
    lookback_days: int = 60,
    market: str = "india",
    option_lookback_days: int = 2,
    as_of_date: date | None = None,
    debug_rejections: bool = False,
    pattern_filter: str | None = None,
    buffer_seconds: float = DEFAULT_BUFFER_SECONDS,
    fast_mode: bool = False,
    fast_strike_window: int = DEFAULT_FAST_STRIKE_WINDOW,
    setup_alerts: bool = DEFAULT_SETUP_ALERTS,
    gate3_alerts: bool = False,
    personal_alerts_only: bool = False,
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    source_strategy_id: str | None = None,
    source_pool_symbols: set[str] | None = None,
    store_timeframe: str = DEFAULT_STORE_TIMEFRAME,
) -> None:
    if as_of_date is not None:
        logger.info("Historical date mode enabled for %s; running once and exiting.", as_of_date.isoformat())
        run_once(
            client=client,
            watchlist=watchlist,
            interval=interval,
            lookback_days=lookback_days,
            market=market,
            option_lookback_days=option_lookback_days,
            as_of_date=as_of_date,
            debug_rejections=debug_rejections,
            pattern_filter=pattern_filter,
            fast_mode=fast_mode,
            fast_strike_window=fast_strike_window,
            setup_alerts=setup_alerts,
            gate3_alerts=gate3_alerts,
            personal_alerts_only=personal_alerts_only,
            strategy_name=strategy_name,
            source_strategy_id=source_strategy_id,
            source_pool_symbols=source_pool_symbols,
            store_timeframe=store_timeframe,
        )
        return
    last_run_boundary: datetime | None = None
    while True:
        now = datetime.now(IST)
        if _is_market_open(now, close_buffer_seconds=buffer_seconds):
            run_once(
                client=client,
                watchlist=watchlist,
                interval=interval,
                lookback_days=lookback_days,
                market=market,
                option_lookback_days=option_lookback_days,
                as_of_date=None,
                debug_rejections=debug_rejections,
                pattern_filter=pattern_filter,
                fast_mode=fast_mode,
                fast_strike_window=fast_strike_window,
                setup_alerts=setup_alerts,
                gate3_alerts=gate3_alerts,
                personal_alerts_only=personal_alerts_only,
                strategy_name=strategy_name,
                source_strategy_id=source_strategy_id,
                source_pool_symbols=source_pool_symbols,
                store_timeframe=store_timeframe,
            )
            last_run_boundary = _floor_to_15_minute(now)
        else:
            logger.info("Market closed; waiting for the next open window.")
        sleep_seconds = _seconds_until_next_scan(buffer_seconds=buffer_seconds, last_run_boundary=last_run_boundary)
        logger.info("Sleeping %.1f seconds until next scan.", sleep_seconds)
        time.sleep(sleep_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dhan realtime ingestion and option-chain snapshot loop.")
    parser.add_argument("--interval", default="15m", help="Candle interval, e.g. 15m, 5m, 1h.")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LIVE_LOOKBACK_DAYS, help="Days of OHLCV history to load for indicators.")
    parser.add_argument("--option-lookback-days", type=int, default=DEFAULT_LIVE_OPTION_LOOKBACK_DAYS, help="Days of option contract history to load for OI.")
    parser.add_argument("--market", default="india", help="Market label used by the Dhan contract resolver.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument("--buffer-seconds", type=float, default=DEFAULT_BUFFER_SECONDS, help="Buffer after each 15m boundary.")
    parser.add_argument("--as-of-date", default="", help="Historical date to scan in YYYY-MM-DD format. Implies a one-time run.")
    parser.add_argument("--strategy-name", default=DEFAULT_STRATEGY_NAME, help="Label used in alerts for this fresh strategy.")
    parser.add_argument("--strategy-id", default=DEFAULT_SOURCE_STRATEGY_ID, help="Source strategy id to read watchlist symbols from when --symbols is not provided.")
    parser.add_argument("--strategy-lookback-days", type=int, default=DEFAULT_SOURCE_STRATEGY_LOOKBACK_DAYS, help="Recent source-strategy days to scan for watchlist symbols.")
    parser.add_argument("--nifty50", action="store_true", help="Scan the NIFTY 50 universe instead of the default watchlist.")
    parser.add_argument("--debug-rejections", action="store_true", help="Print why a stock was rejected.")
    parser.add_argument("--pattern", default="", choices=("",) + PATTERN_CATALOG, help="Scan only one pattern at a time.")
    parser.add_argument("--list-patterns", action="store_true", help="Print supported patterns and exit.")
    parser.add_argument("--scan-history", action="store_true", help="Scan the full lookback window and print pattern hits; date-based history defaults to all patterns.")
    parser.add_argument("--scan-all-patterns", action="store_true", help="Historical scan mode: force all 16 patterns one by one.")
    parser.add_argument("--scan-gate3-history", action="store_true", help="Historical scan mode: sweep the full lookback window for Gate 3 OI confirmations.")
    parser.add_argument("--scan-gate4-history", action="store_true", help="Historical scan mode: sweep the full lookback window for Gate 4 PCR confirmations.")
    parser.add_argument("--gate3-only", action="store_true", help="Run only the option-chain Gate 3 debug view for the selected symbols.")
    parser.add_argument("--gate4-only", action="store_true", help="Run only the PCR Gate 4 debug view for the selected symbols.")
    parser.add_argument("--replay-snapshot-file", default="", help="Replay a stored snapshot JSON locally instead of hitting live Dhan.")
    parser.add_argument("--latest-only", action="store_true", help="Run only the latest candle snapshot and exit, skipping historical sweep.")
    parser.add_argument("--fast-mode", action="store_true", help="Limit option-chain fetches to strikes near ATM for faster scans.")
    parser.add_argument("--fast-strike-window", type=int, default=DEFAULT_FAST_STRIKE_WINDOW, help="Number of strikes to keep on each side of ATM in fast mode.")
    parser.add_argument("--body-multiplier", type=float, default=DEFAULT_BODY_MULTIPLIER, help="Big-candle body threshold as a multiplier of the 40-period average body.")
    parser.add_argument("--repeat-pattern-alerts", action="store_true", help="Enable isolated repeat-pattern Telegram alerts for 2nd/3rd bullish or bearish occurrences.")
    parser.add_argument("--no-repeat-pattern-alerts", dest="repeat_pattern_alerts", action="store_false", help="Disable the repeat-pattern Telegram alerts path.")
    parser.add_argument("--setup-alerts", dest="setup_alerts", action="store_true", help="Send setup alerts when Gate 1 and Gate 2 first confirm.")
    parser.add_argument("--no-setup-alerts", dest="setup_alerts", action="store_false", help="Disable Gate 1/2 setup alerts.")
    parser.set_defaults(repeat_pattern_alerts=DEFAULT_REPEAT_PATTERN_ALERTS)
    parser.set_defaults(setup_alerts=DEFAULT_SETUP_ALERTS)
    parser.add_argument("--skip-dhan-preflight", action="store_true", help="Skip the live Dhan token probe before running.")
    parser.add_argument("--gate3-alerts", action="store_true", help="Send group and personal Telegram alerts when Gate 3 confirms a fresh OI shift.")
    parser.add_argument("--personal-alerts-only", action="store_true", help="Only send Gate 3 alerts to the personal Telegram chat.")
    parser.add_argument("--nifty-futures", action="store_true", help="Scan the cached 207-stock Nifty F&O universe.")
    parser.add_argument("--store-timeframe", default=DEFAULT_STORE_TIMEFRAME, choices=("minute", "1m", "5m", "15m", "15_min", "dashboard", "daily"), help="Central snapshot store timeframe to publish for this run.")
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated list of watchlist symbols to override the default dictionary keys.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    os.environ["DHAN_BODY_MULTIPLIER"] = str(args.body_multiplier)
    _load_env_files()
    client_id, access_token = _get_credentials()
    client = dhanhq(client_id, access_token)

    if args.list_patterns:
        print("\n".join(PATTERN_CATALOG))
        return 0

    as_of_date = _parse_as_of_date(args.as_of_date) if str(args.as_of_date or "").strip() else None

    replay_snapshot_path = Path(args.replay_snapshot_file).expanduser() if str(args.replay_snapshot_file or "").strip() else None
    if replay_snapshot_path is not None:
        _replay_gate3_snapshot_file(
            replay_snapshot_path,
            gate12_alerts=args.setup_alerts,
            gate3_alerts=args.gate3_alerts,
        )
        return 0

    if args.repeat_pattern_alerts:
        source_strategy_id = str(args.strategy_id or DEFAULT_SOURCE_STRATEGY_ID).strip() or DEFAULT_SOURCE_STRATEGY_ID
        watchlist = dict(WATCHLIST)
        if args.symbols.strip():
            selected = [_normalize_symbol_token(part) for part in args.symbols.split(",") if part.strip()]
            watchlist = {symbol: watchlist.get(symbol) for symbol in selected}
        elif args.nifty_futures or DEFAULT_SCAN_UNIVERSE == "all":
            universe_symbols = _load_broad_india_universe_symbols()
            if universe_symbols:
                watchlist = {symbol: None for symbol in universe_symbols}
            else:
                logger.warning("F&O cache missing; falling back to NIFTY50 tickers.")
                watchlist = {_normalize_symbol_token(symbol): None for symbol in NIFTY50_TICKERS}
        elif args.nifty50:
            watchlist = {_normalize_symbol_token(symbol): None for symbol in NIFTY50_TICKERS}
        elif DEFAULT_SCAN_UNIVERSE == "source_pool":
            source_pool_symbols = set(_load_candidate_pool_symbols(source_strategy_id))
            if not source_pool_symbols:
                source_pool_symbols = {
                    _normalize_symbol_token(symbol)
                    for symbol in _load_recent_strategy_symbols(
                        source_strategy_id,
                        lookback_days=max(1, int(args.strategy_lookback_days)),
                    )
                }
            strategy_symbols = sorted(source_pool_symbols)
            if strategy_symbols:
                watchlist = {symbol: None for symbol in strategy_symbols}
            else:
                logger.warning("No symbols found in source strategy %s; using built-in watchlist fallback.", source_strategy_id)
        else:
            universe_symbols = _load_broad_india_universe_symbols()
            if universe_symbols:
                watchlist = {symbol: None for symbol in universe_symbols}
            else:
                logger.warning("Broad universe missing; using NIFTY50 fallback.")
                watchlist = {_normalize_symbol_token(symbol): None for symbol in NIFTY50_TICKERS}

        run_repeat_pattern_forever(
            client=client,
            watchlist=watchlist,
            interval=args.interval,
            lookback_days=args.lookback_days,
            market=args.market,
            buffer_seconds=args.buffer_seconds,
            fast_mode=args.fast_mode,
            fast_strike_window=args.fast_strike_window,
        )
        return 0

    strategy_name = str(args.strategy_name or DEFAULT_STRATEGY_NAME).strip() or DEFAULT_STRATEGY_NAME
    source_strategy_id = str(args.strategy_id or DEFAULT_SOURCE_STRATEGY_ID).strip() or DEFAULT_SOURCE_STRATEGY_ID
    source_pool_symbols = set(_load_candidate_pool_symbols(source_strategy_id))
    if not source_pool_symbols:
        source_pool_symbols = {
            _normalize_symbol_token(symbol)
            for symbol in _load_recent_strategy_symbols(
                source_strategy_id,
                lookback_days=max(1, int(args.strategy_lookback_days)),
            )
        }
    watchlist = dict(WATCHLIST)
    if args.symbols.strip():
        selected = [_normalize_symbol_token(part) for part in args.symbols.split(",") if part.strip()]
        watchlist = {symbol: watchlist.get(symbol) for symbol in selected}
    elif args.nifty_futures or DEFAULT_SCAN_UNIVERSE == "all":
        universe_symbols = _load_broad_india_universe_symbols()
        if universe_symbols:
            watchlist = {symbol: None for symbol in universe_symbols}
        else:
            logger.warning("F&O cache missing; falling back to NIFTY50 tickers.")
            watchlist = {_normalize_symbol_token(symbol): None for symbol in NIFTY50_TICKERS}
    elif args.nifty50:
        watchlist = {_normalize_symbol_token(symbol): None for symbol in NIFTY50_TICKERS}
    elif DEFAULT_SCAN_UNIVERSE == "source_pool":
        strategy_symbols = sorted(source_pool_symbols)
        if strategy_symbols:
            watchlist = {symbol: None for symbol in strategy_symbols}
        else:
            logger.warning("No symbols found in source strategy %s; using built-in watchlist fallback.", source_strategy_id)
    else:
        universe_symbols = _load_broad_india_universe_symbols()
        if universe_symbols:
            watchlist = {symbol: None for symbol in universe_symbols}
        else:
            logger.warning("Broad universe missing; using NIFTY50 fallback.")
            watchlist = {_normalize_symbol_token(symbol): None for symbol in NIFTY50_TICKERS}

    probe_symbol = next(iter(watchlist.keys()), None)
    skip_preflight = bool(args.skip_dhan_preflight) or replay_snapshot_path is not None or os.getenv("DHAN_SKIP_PREFLIGHT", "").strip() == "1"
    if not skip_preflight and probe_symbol is not None and hasattr(client, "fetch_equity_history"):
        _preflight_dhan_token(client=client, symbol=probe_symbol, market=args.market, interval=args.interval)

    if args.gate3_only:
        run_gate3_debug(
            client=client,
            symbols=list(watchlist.keys()),
            interval=args.interval,
            lookback_days=args.lookback_days,
            market=args.market,
            option_lookback_days=args.option_lookback_days,
            as_of_date=as_of_date,
            fast_mode=args.fast_mode,
            fast_strike_window=args.fast_strike_window,
        )
        return 0

    if args.gate4_only:
        run_gate4_debug(
            client=client,
            symbols=list(watchlist.keys()),
            interval=args.interval,
            lookback_days=args.lookback_days,
            market=args.market,
            option_lookback_days=args.option_lookback_days,
            as_of_date=as_of_date,
            fast_mode=args.fast_mode,
            fast_strike_window=args.fast_strike_window,
        )
        return 0

    # Historical modes remain available for symbol-specific validation and scans.
    if args.scan_gate3_history:
        run_gate3_history_scan(
            client=client,
            symbols=list(watchlist.keys()),
            interval=args.interval,
            lookback_days=args.lookback_days,
            market=args.market,
            as_of_date=as_of_date,
            fast_mode=args.fast_mode,
            fast_strike_window=args.fast_strike_window,
        )
        return 0

    if args.scan_gate4_history:
        run_gate4_history_scan(
            client=client,
            symbols=list(watchlist.keys()),
            interval=args.interval,
            lookback_days=args.lookback_days,
            market=args.market,
            as_of_date=as_of_date,
            fast_mode=args.fast_mode,
            fast_strike_window=args.fast_strike_window,
        )
        return 0

    symbol_specific_history = bool(args.symbols.strip()) and not args.once and not args.scan_history and not args.latest_only
    if args.scan_history or symbol_specific_history:
        run_history_scan(
            client=client,
            symbols=list(watchlist.keys()),
            interval=args.interval,
            lookback_days=args.lookback_days,
            market=args.market,
            pattern_filter=args.pattern or None,
            scan_all_patterns=args.scan_all_patterns or symbol_specific_history,
            as_of_date=as_of_date,
            fast_mode=args.fast_mode,
            fast_strike_window=args.fast_strike_window,
        )
        return 0

    # Historical execution is intentionally disabled in the default flow.
    if args.once or args.latest_only:
        run_once(
            client=client,
            watchlist=watchlist,
            interval=args.interval,
            lookback_days=args.lookback_days,
            market=args.market,
            option_lookback_days=args.option_lookback_days,
            as_of_date=None,
            debug_rejections=args.debug_rejections,
            pattern_filter=args.pattern or None,
            fast_mode=args.fast_mode,
            fast_strike_window=args.fast_strike_window,
            setup_alerts=args.setup_alerts,
            gate3_alerts=args.gate3_alerts,
            personal_alerts_only=args.personal_alerts_only,
            strategy_name=strategy_name,
            source_strategy_id=source_strategy_id,
            source_pool_symbols=source_pool_symbols,
            store_timeframe=args.store_timeframe,
        )
        return 0

    run_forever(
        client=client,
        watchlist=watchlist,
        interval=args.interval,
        lookback_days=args.lookback_days,
        market=args.market,
        option_lookback_days=args.option_lookback_days,
        as_of_date=None,
        debug_rejections=args.debug_rejections,
        pattern_filter=args.pattern or None,
        buffer_seconds=args.buffer_seconds,
        fast_mode=args.fast_mode,
        fast_strike_window=args.fast_strike_window,
        setup_alerts=args.setup_alerts,
        gate3_alerts=args.gate3_alerts,
        personal_alerts_only=args.personal_alerts_only,
        strategy_name=strategy_name,
        source_strategy_id=source_strategy_id,
        source_pool_symbols=source_pool_symbols,
        store_timeframe=args.store_timeframe,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
