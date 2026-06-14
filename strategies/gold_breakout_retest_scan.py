#!/usr/bin/env python3
import contextlib
import io
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


IST = ZoneInfo("Asia/Kolkata")

INTERVAL = os.getenv("COMMODITY_BR_INTERVAL", "60m")
DATA_RANGE = os.getenv("COMMODITY_BR_RANGE", "180d")
DAILY_INTERVAL = "1d"
DAILY_RANGE = os.getenv("COMMODITY_BR_DAILY_RANGE", "400d")
DHAN_RANGE = os.getenv("COMMODITY_BR_DHAN_RANGE", "60d")
MOMENTUM_LOOKBACK = int(os.getenv("COMMODITY_BR_LOOKBACK", "30"))
VOLUME_MULT = float(os.getenv("COMMODITY_BR_VOLUME_MULT", "3.0"))
MIN_RR = float(os.getenv("COMMODITY_BR_MIN_RR", "1.5"))
TARGET_ATR_MULT = float(os.getenv("COMMODITY_BR_TARGET_ATR_MULT", "2.5"))
ATR_PERIOD = int(os.getenv("COMMODITY_BR_ATR_PERIOD", "14"))
ADX_PERIOD = int(os.getenv("COMMODITY_BR_ADX_PERIOD", "14"))
ADX_MIN = float(os.getenv("COMMODITY_BR_ADX_MIN", "20"))
WINDOW_START = int(os.getenv("COMMODITY_BR_WINDOW_START", "5"))
WINDOW_END = int(os.getenv("COMMODITY_BR_WINDOW_END", "6"))
COMMODITY_CACHE_MAX_AGE_MIN = int(os.getenv("COMMODITY_BR_CACHE_MAX_AGE_MIN", "30"))
COMMODITY_CACHE_TIMEFRAME_PREFIX = "commodities_15m"

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HISTORY_DIR = BASE_DIR / "history"
LEGACY_GOLD_OUT = BASE_DIR / "gold_breakout_retest_on.json"


def _title_case_asset(asset):
    labels = {
        "GOLD": "Gold",
        "SILVER": "Silver",
        "CRUDEOIL": "Crude Oil",
        "BRENT": "Brent",
        "NATGAS": "Natural Gas",
        "COPPER": "Copper",
        "PLATINUM": "Platinum",
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "SOL": "Solana",
        "BNB": "BNB",
        "XRP": "XRP",
    }
    return labels.get(asset, asset.replace("_", " ").title())


def _range_to_days(value):
    txt = str(value or "").strip().lower()
    if txt.endswith("d") and txt[:-1].isdigit():
        return int(txt[:-1])
    if txt.endswith("mo") and txt[:-2].isdigit():
        return int(txt[:-2]) * 30
    if txt.endswith("y") and txt[:-1].isdigit():
        return int(txt[:-1]) * 365
    return 180


def _interval_to_resample_rule(interval):
    text = str(interval or "").strip().lower()
    if text.endswith("m") and text[:-1].isdigit():
        return f"{int(text[:-1])}min"
    if text.endswith("h") and text[:-1].isdigit():
        return f"{int(text[:-1])}H"
    if text in {"1d", "d", "day"}:
        return "1D"
    return "1H"


def _build_df_from_result(result):
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "open": quote.get("open") or [],
            "high": quote.get("high") or [],
            "low": quote.get("low") or [],
            "close": quote.get("close") or [],
            "volume": quote.get("volume") or [],
        }
    )
    if df.empty:
        return pd.DataFrame()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    if df.empty:
        return pd.DataFrame()
    df["dt_utc"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df["dt_ist"] = df["dt_utc"].dt.tz_convert(IST)
    return df


def _fetch_yahoo_chart(symbol, interval=INTERVAL, data_range=DATA_RANGE):
    import requests

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    intraday = {"1m", "2m", "5m", "15m", "30m", "60m", "90m"}
    days = _range_to_days(data_range)

    def _one(params):
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return pd.DataFrame()
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            return pd.DataFrame()
        return _build_df_from_result(result)

    if str(interval).lower() in intraday and days > 60:
        now_utc = datetime.now(timezone.utc)
        start_utc = now_utc - timedelta(days=days)
        chunk_dfs = []
        cursor = start_utc
        while cursor < now_utc:
            chunk_end = min(cursor + timedelta(days=59), now_utc)
            part = _one(
                {
                    "period1": int(cursor.timestamp()),
                    "period2": int(chunk_end.timestamp()),
                    "interval": interval,
                    "includePrePost": "false",
                    "events": "div,splits",
                }
            )
            if not part.empty:
                chunk_dfs.append(part)
            cursor = chunk_end + timedelta(minutes=1)
        if not chunk_dfs:
            return pd.DataFrame()
        return (
            pd.concat(chunk_dfs, ignore_index=True)
            .drop_duplicates(subset=["timestamp"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    return _one(
        {
            "range": data_range,
            "interval": interval,
            "includePrePost": "false",
            "events": "div,splits",
        }
    )


def _fetch_yfinance_chart(symbol, interval=INTERVAL, data_range=DATA_RANGE):
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("yfinance").disabled = True
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = yf.download(
                tickers=symbol,
                period=data_range,
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
            )
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    col_map = {c.lower(): c for c in df.columns}
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(col_map.keys())):
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "open": pd.to_numeric(df[col_map["open"]], errors="coerce"),
            "high": pd.to_numeric(df[col_map["high"]], errors="coerce"),
            "low": pd.to_numeric(df[col_map["low"]], errors="coerce"),
            "close": pd.to_numeric(df[col_map["close"]], errors="coerce"),
            "volume": pd.to_numeric(df[col_map["volume"]], errors="coerce"),
        }
    ).dropna(subset=["close"])
    if out.empty:
        return pd.DataFrame()
    idx = out.index
    if getattr(idx, "tz", None) is None:
        out["dt_utc"] = pd.to_datetime(idx, utc=True)
    else:
        out["dt_utc"] = pd.to_datetime(idx).tz_convert("UTC")
    out["dt_ist"] = out["dt_utc"].dt.tz_convert(IST)
    return out.reset_index(drop=True)


def _commodity_cache_timeframe(asset):
    return f"{COMMODITY_CACHE_TIMEFRAME_PREFIX}_{str(asset or '').strip().upper()}"


def _commodity_cache_path(asset):
    try:
        from backend.market_snapshot_store import MarketSnapshotStore

        store = MarketSnapshotStore()
        return store.latest_path(_commodity_cache_timeframe(asset))
    except Exception:
        return None


def _load_cached_commodity_frame(asset):
    path = _commodity_cache_path(asset)
    if not path or not path.exists():
        return pd.DataFrame(), False
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame(), False
    if frame is None or frame.empty:
        return pd.DataFrame(), False
    key = str(asset or "").strip().upper()
    if "symbol" in frame.columns:
        frame = frame[frame["symbol"].astype(str).str.upper() == key].copy()
    elif "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == key].copy()
    if frame.empty:
        return pd.DataFrame(), False
    fresh = False
    try:
        age_min = (datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds() / 60.0
        fresh = age_min <= COMMODITY_CACHE_MAX_AGE_MIN
    except Exception:
        fresh = False
    return frame, fresh


def _store_commodity_frame(asset, frame, meta=None):
    if frame is None or frame.empty:
        return None
    try:
        from backend.market_snapshot_store import MarketSnapshotStore

        store = MarketSnapshotStore()
        payload = frame.copy()
        payload["asset"] = str(asset or "").strip().upper()
        payload["market"] = "commodities"
        payload["interval"] = "15m"
        payload["price_source"] = str((meta or {}).get("source") or (meta or {}).get("price_source") or "DHAN_INTRADAY")
        store.write_payload(
            payload,
            timeframe=_commodity_cache_timeframe(asset),
            metadata={
                "asset": str(asset or "").strip().upper(),
                "market": "commodities",
                "interval": "15m",
                "price_source": str((meta or {}).get("source") or (meta or {}).get("price_source") or "DHAN_INTRADAY"),
            },
        )
    except Exception:
        return None
    return True


def _fetch_dhan_commodity_raw_frame(asset):
    asset = str(asset or "").strip().upper()
    if not asset:
        return pd.DataFrame(), {}
    try:
        from backend.dhan_intraday import fetch_intraday_history
        from backend.dhan_strategy_schema import standardize_dhan_history_frame
    except Exception:
        return pd.DataFrame(), {}
    try:
        raw, meta = fetch_intraday_history(asset, interval="15m", data_range=DHAN_RANGE, market="commodities")
    except Exception:
        return pd.DataFrame(), {}
    try:
        rows, row_meta = standardize_dhan_history_frame(
            raw,
            symbol=asset,
            market="commodities",
            interval="15m",
            contract=((meta or {}).get("contract") if isinstance(meta, dict) else None),
            price_source=str((meta or {}).get("source") or (meta or {}).get("price_source") or "DHAN_INTRADAY"),
        )
    except Exception:
        return pd.DataFrame(), {}
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(), {}
    _store_commodity_frame(asset, frame, meta or row_meta)
    return frame, (meta or row_meta or {})


def _load_or_refresh_commodity_raw_frame(asset):
    cached, fresh = _load_cached_commodity_frame(asset)
    if not cached.empty and fresh:
        return cached
    fetched, meta = _fetch_dhan_commodity_raw_frame(asset)
    if not fetched.empty:
        return fetched
    return cached


def _resample_commodity_frame(frame, rule):
    if frame is None or frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    ts_col = None
    for candidate in ("timestamp", "dt_ist", "dt_utc"):
        if candidate in work.columns:
            ts_col = candidate
            break
    if ts_col is None:
        return pd.DataFrame()
    work["dt_ist"] = pd.to_datetime(work[ts_col], errors="coerce", utc=True)
    work = work.dropna(subset=["dt_ist", "close"]).sort_values("dt_ist")
    if work.empty:
        return pd.DataFrame()
    try:
        work["dt_ist"] = work["dt_ist"].dt.tz_convert(IST)
    except Exception:
        try:
            work["dt_ist"] = work["dt_ist"].dt.tz_localize(IST)
        except Exception:
            return pd.DataFrame()
    work["dt_utc"] = work["dt_ist"].dt.tz_convert("UTC")
    work = work.set_index("dt_ist")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in work.columns:
        agg["volume"] = "sum"
    resampled = work.resample(rule, label="right", closed="right").agg(agg)
    resampled = resampled.dropna(subset=["close"])
    if resampled.empty:
        return pd.DataFrame()
    resampled = resampled.reset_index()
    resampled["dt_utc"] = resampled["dt_ist"].dt.tz_convert("UTC")
    resampled["date"] = resampled["dt_ist"].dt.date
    return resampled

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("yfinance").disabled = True
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = yf.download(
                tickers=symbol,
                period=data_range,
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
            )
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    col_map = {c.lower(): c for c in df.columns}
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(col_map.keys())):
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "open": pd.to_numeric(df[col_map["open"]], errors="coerce"),
            "high": pd.to_numeric(df[col_map["high"]], errors="coerce"),
            "low": pd.to_numeric(df[col_map["low"]], errors="coerce"),
            "close": pd.to_numeric(df[col_map["close"]], errors="coerce"),
            "volume": pd.to_numeric(df[col_map["volume"]], errors="coerce"),
        }
    ).dropna(subset=["close"])
    if out.empty:
        return pd.DataFrame()
    idx = out.index
    if getattr(idx, "tz", None) is None:
        out["dt_utc"] = pd.to_datetime(idx, utc=True)
    else:
        out["dt_utc"] = pd.to_datetime(idx).tz_convert("UTC")
    out["dt_ist"] = out["dt_utc"].dt.tz_convert(IST)
    return out.reset_index(drop=True)


def _fetch_first_available(candidates):
    attempted = []
    for symbol in candidates:
        s = str(symbol).strip()
        if not s:
            continue
        attempted.append(s)
        df = _fetch_yahoo_chart(s)
        if df.empty:
            df = _fetch_yfinance_chart(s)
        if not df.empty:
            return s, attempted, df
    return "", attempted, pd.DataFrame()


def _fetch_daily_ema9(symbol):
    if not symbol:
        return pd.DataFrame(columns=["dt_utc", "daily_ema9"])
    df = _fetch_yahoo_chart(symbol, interval=DAILY_INTERVAL, data_range=DAILY_RANGE)
    if df.empty:
        df = _fetch_yfinance_chart(symbol, interval=DAILY_INTERVAL, data_range=DAILY_RANGE)
    if df.empty:
        return pd.DataFrame(columns=["dt_utc", "daily_ema9"])
    out = df[["dt_utc", "close"]].copy().dropna(subset=["dt_utc", "close"]).sort_values("dt_utc")
    if out.empty:
        return pd.DataFrame(columns=["dt_utc", "daily_ema9"])
    out["daily_ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    return out[["dt_utc", "daily_ema9"]].reset_index(drop=True)


def _attach_daily_ema9(df, daily_ema_df):
    out = df.copy().sort_values("dt_utc").reset_index(drop=True)
    if daily_ema_df is None or daily_ema_df.empty:
        out["daily_ema9"] = np.nan
        return out
    d = (
        daily_ema_df[["dt_utc", "daily_ema9"]]
        .dropna(subset=["dt_utc"])
        .sort_values("dt_utc")
        .reset_index(drop=True)
    )
    if d.empty:
        out["daily_ema9"] = np.nan
        return out
    return pd.merge_asof(out, d, on="dt_utc", direction="backward")


def _add_indicators(df, daily_ema_df=None):
    out = _attach_daily_ema9(df, daily_ema_df)
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["daily_ema9"] = pd.to_numeric(out.get("daily_ema9"), errors="coerce")

    out["ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    date_key = out["dt_ist"].dt.date
    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    out["pv"] = typical * out["volume"].fillna(0)
    out["cum_pv"] = out.groupby(date_key)["pv"].cumsum()
    out["cum_vol"] = out.groupby(date_key)["volume"].cumsum()
    out["vwap"] = out["cum_pv"] / out["cum_vol"].where(out["cum_vol"] != 0, np.nan)

    out["avg_vol_30_prev"] = out["volume"].rolling(MOMENTUM_LOOKBACK).mean().shift(1)
    out["vol_mult"] = out["volume"] / out["avg_vol_30_prev"]

    prev_close = out["close"].shift(1)
    prev_high = out["high"].shift(1)
    prev_low = out["low"].shift(1)
    tr = pd.concat(
        [
            (out["high"] - out["low"]).abs(),
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.rolling(ATR_PERIOD).mean()

    up_move = out["high"] - prev_high
    down_move = prev_low - out["low"]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_smooth = tr.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()
    plus_dm_smooth = pd.Series(plus_dm, index=out.index).ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()
    minus_dm_smooth = pd.Series(minus_dm, index=out.index).ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()
    out["plus_di"] = (100 * plus_dm_smooth / tr_smooth.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    out["minus_di"] = (100 * minus_dm_smooth / tr_smooth.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    di_sum = (out["plus_di"] + out["minus_di"]).replace(0, np.nan)
    dx = (100 * (out["plus_di"] - out["minus_di"]).abs() / di_sum).replace([np.inf, -np.inf], np.nan)
    out["adx14"] = dx.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()

    volume_ok = out["volume"] > (VOLUME_MULT * out["avg_vol_30_prev"])
    adx_ok = out["adx14"] > ADX_MIN
    out["buy_breakout"] = (
        volume_ok
        & adx_ok
        & (out["close"] > out["daily_ema9"])
        & (out["close"] > out["ema9"])
        & (out["close"] > out["vwap"])
    ).fillna(False)
    out["sell_breakout"] = (
        volume_ok
        & adx_ok
        & (out["close"] < out["daily_ema9"])
        & (out["close"] < out["ema9"])
        & (out["close"] < out["vwap"])
    ).fillna(False)
    return out


def _evaluate_setup(df, i, side):
    b = df.iloc[i]
    last_idx = len(df) - 1
    max_j = min(i + WINDOW_END, last_idx)
    had_touch = False
    had_confirmed = False
    best_rr = None
    invalidate = None

    for j in range(i + 1, max_j + 1):
        row = df.iloc[j]
        close = row["close"]
        ema9 = row["ema9"]
        vwap = row["vwap"]
        if side == "BUY":
            wrong_side = pd.notna(close) and pd.notna(ema9) and pd.notna(vwap) and close < min(ema9, vwap)
        else:
            wrong_side = pd.notna(close) and pd.notna(ema9) and pd.notna(vwap) and close > max(ema9, vwap)
        if wrong_side:
            invalidate = row
            break
        if j < i + WINDOW_START:
            continue

        if side == "BUY":
            touched_ema = bool(pd.notna(row["low"]) and pd.notna(ema9) and row["low"] <= ema9)
            touched_vwap = bool(pd.notna(row["low"]) and pd.notna(vwap) and row["low"] <= vwap)
        else:
            touched_ema = bool(pd.notna(row["high"]) and pd.notna(ema9) and row["high"] >= ema9)
            touched_vwap = bool(pd.notna(row["high"]) and pd.notna(vwap) and row["high"] >= vwap)
        if not (touched_ema or touched_vwap):
            continue
        had_touch = True

        if side == "BUY":
            confirm = bool(
                pd.notna(row["open"])
                and pd.notna(row["close"])
                and pd.notna(ema9)
                and pd.notna(vwap)
                and row["close"] > row["open"]
                and row["close"] > ema9
                and row["close"] > vwap
            )
        else:
            confirm = bool(
                pd.notna(row["open"])
                and pd.notna(row["close"])
                and pd.notna(ema9)
                and pd.notna(vwap)
                and row["open"] > row["close"]
                and row["close"] < ema9
                and row["close"] < vwap
            )
        if not confirm:
            continue
        had_confirmed = True

        entry = float(row["close"])
        atr = float(row["atr14"]) if pd.notna(row["atr14"]) else None
        if side == "BUY":
            stop = float(min(row["low"], ema9, vwap))
            risk = entry - stop
            target = entry + (TARGET_ATR_MULT * atr) if atr is not None else None
        else:
            stop = float(max(row["high"], ema9, vwap))
            risk = stop - entry
            target = entry - (TARGET_ATR_MULT * atr) if atr is not None else None
        rr = None
        if target is not None and risk > 0:
            rr = abs(target - entry) / risk
            best_rr = rr if best_rr is None else max(best_rr, rr)

        if rr is None or rr < MIN_RR:
            continue

        touched = []
        if touched_ema:
            touched.append("EMA9")
        if touched_vwap:
            touched.append("VWAP")
        return {
            "status": "TRIGGERED",
            "side": side,
            "breakout_time_utc": str(b["dt_utc"]),
            "breakout_time_ist": str(b["dt_ist"]),
            "trigger_time_utc": str(row["dt_utc"]),
            "trigger_time_ist": str(row["dt_ist"]),
            "entry_price": round(entry, 2),
            "stop_price": round(stop, 2),
            "target_price": round(float(target), 2),
            "rr_ratio": round(float(rr), 2),
            "touched": touched,
            "vol_mult": round(float(b["vol_mult"]), 2) if pd.notna(b["vol_mult"]) else None,
        }

    if invalidate is not None:
        return {"status": "INVALIDATED_STRUCTURE"}
    if last_idx < i + WINDOW_START:
        return {"status": "WAITING_COOLDOWN"}
    if had_confirmed:
        return {"status": "SKIPPED_RR", "best_rr_seen": round(float(best_rr), 2) if best_rr is not None else None}
    if had_touch:
        return {"status": "EXPIRED_NO_CONFIRM"}
    return {"status": "EXPIRED_TIMEOUT"}


def _evaluate(df):
    setups = []
    for i, row in df.iterrows():
        if bool(row.get("buy_breakout")):
            setups.append(_evaluate_setup(df, i, "BUY"))
        if bool(row.get("sell_breakout")):
            setups.append(_evaluate_setup(df, i, "SELL"))
    return setups


def _symbol_to_india_key(symbol):
    s = str(symbol or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    return s


def _load_market_configs():
    commodities = {
        "GOLD": ["GOLD"],
        "SILVER": ["SILVER"],
        "CRUDEOIL": ["CRUDEOIL"],
        "BRENT": ["BRENT"],
        "NATGAS": ["NATGAS"],
        "COPPER": ["COPPER"],
        "PLATINUM": ["PLATINUM"],
    }

    try:
        from strategies.apollo_ema9_strategy import NIFTY50_TICKERS
        india = {_symbol_to_india_key(sym): [sym] for sym in NIFTY50_TICKERS}
    except Exception:
        india = {}

    try:
        from backend.data_fetcher import GLOBAL_STOCKS, CRYPTO
        global_assets = {k: [v] for k, v in GLOBAL_STOCKS.items()}
        crypto_assets = {k: [v] for k, v in CRYPTO.items()}
    except Exception:
        global_assets = {
            "AAPL": ["AAPL"],
            "MSFT": ["MSFT"],
            "NVDA": ["NVDA"],
            "AMZN": ["AMZN"],
            "GOOGL": ["GOOGL"],
        }
        crypto_assets = {
            "BTC": ["BTC-USD"],
            "ETH": ["ETH-USD"],
            "SOL": ["SOL-USD"],
            "BNB": ["BNB-USD"],
            "XRP": ["XRP-USD"],
        }

    return [
        {
            "strategy_id": "india_breakout_retest_on",
            "title": "India 1H Breakout Retest — Entry Valid",
            "market": "india",
            "assets": india,
        },
        {
            "strategy_id": "global_breakout_retest_on",
            "title": "Global 1H Breakout Retest — Entry Valid",
            "market": "global",
            "assets": global_assets,
        },
        {
            "strategy_id": "crypto_breakout_retest_on",
            "title": "Crypto 1H Breakout Retest — Entry Valid",
            "market": "crypto",
            "assets": crypto_assets,
        },
        {
            "strategy_id": "commodities_breakout_retest_on",
            "title": "Commodities 1H Breakout Retest — Entry Valid",
            "market": "commodities",
            "assets": commodities,
        },
    ]


def _to_item(asset, symbol, setup):
    touched = ", ".join(setup.get("touched") or [])
    side = str(setup.get("side") or "BUY").upper()
    return {
        "ticker": asset,
        "name": _title_case_asset(asset),
        "symbol": symbol,
        "side": side,
        "notify_key": f"{asset}|{symbol}|{side}|{setup.get('breakout_time_utc')}|{setup.get('trigger_time_utc')}|{setup.get('entry_price')}",
        "signal_time": setup.get("breakout_time_ist"),
        "entry_time": setup.get("trigger_time_ist"),
        "entry_price": setup.get("entry_price"),
        "stop_price": setup.get("stop_price"),
        "target_price": setup.get("target_price"),
        "rr_ratio": setup.get("rr_ratio"),
        "vol_mult": setup.get("vol_mult"),
        "lines": [
            f"{side} | {_title_case_asset(asset)} breakout {setup.get('breakout_time_ist')} | entry {setup.get('trigger_time_ist')} (candle {WINDOW_START}-{WINDOW_END})",
            f"Entry {setup.get('entry_price'):.2f} | SL {setup.get('stop_price'):.2f} | Target {setup.get('target_price'):.2f} | RR {setup.get('rr_ratio'):.2f} | Touch {touched}",
        ],
    }


def _write_payload(payload):
    strategy_id = payload.get("strategy_id") or "unknown_strategy"
    out_path = BASE_DIR / f"{strategy_id}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    day_key = datetime.now(IST).strftime("%Y%m%d")
    (HISTORY_DIR / f"{strategy_id}_{day_key}.json").write_text(json.dumps(payload, indent=2))


def _scan_single_asset(asset, candidates):
    market = None
    candidate_list = candidates
    if isinstance(candidates, dict):
        market = candidates.get("market")
        candidate_list = candidates.get("candidates")
    if str(market or "").strip().lower() == "commodities":
        raw = _load_or_refresh_commodity_raw_frame(asset)
        attempted = [f"DHAN:{asset}"]
        symbol = asset
        if raw.empty:
            return {
                "asset": asset,
                "symbol": None,
                "attempted": attempted,
                "bars": 0,
                "breakouts": 0,
                "triggered": 0,
                "items": [],
            }
        hourly_rule = _interval_to_resample_rule(INTERVAL)
        daily_raw = _resample_commodity_frame(raw, "1D")
        raw = _resample_commodity_frame(raw, hourly_rule)
        daily_ema = pd.DataFrame(columns=["dt_utc", "daily_ema9"])
        if not daily_raw.empty and "close" in daily_raw.columns:
            daily_ema = daily_raw[["dt_utc", "close"]].copy().dropna(subset=["dt_utc", "close"])
            if not daily_ema.empty:
                daily_ema = daily_ema.sort_values("dt_utc").reset_index(drop=True)
                daily_ema["daily_ema9"] = daily_ema["close"].ewm(span=9, adjust=False).mean()
                daily_ema = daily_ema[["dt_utc", "daily_ema9"]]
    else:
        symbol, attempted, raw = _fetch_first_available(candidate_list or [])
        if raw.empty:
            return {
                "asset": asset,
                "symbol": None,
                "attempted": attempted,
                "bars": 0,
                "breakouts": 0,
                "triggered": 0,
                "items": [],
            }
        daily_ema = _fetch_daily_ema9(symbol)
    if str(market or "").strip().lower() != "commodities" and raw.empty:
        return {
            "asset": asset,
            "symbol": None,
            "attempted": attempted,
            "bars": 0,
            "breakouts": 0,
            "triggered": 0,
            "items": [],
        }
    df = _add_indicators(raw, daily_ema_df=daily_ema)
    setups = _evaluate(df)
    triggered = [s for s in setups if s.get("status") == "TRIGGERED"]
    items = [_to_item(asset, symbol, s) for s in triggered]
    return {
        "asset": asset,
        "symbol": symbol,
        "attempted": attempted,
        "bars": int(len(df)),
        "breakouts": int(df["buy_breakout"].sum() + df["sell_breakout"].sum()),
        "triggered": int(len(triggered)),
        "items": items,
    }


def _scan_market(cfg):
    strategy_id = cfg["strategy_id"]
    title = cfg["title"]
    market = cfg["market"]
    assets = cfg["assets"] or {}
    out_path = BASE_DIR / f"{strategy_id}.json"

    if not assets:
        print(f"[UNIVERSAL_BR] {strategy_id}: empty universe, skipped")
        return

    results = []
    for asset, candidates in assets.items():
        try:
            results.append(_scan_single_asset(asset, {"market": market, "candidates": candidates}))
        except Exception:
            results.append(
                {
                    "asset": asset,
                    "symbol": None,
                    "attempted": [],
                    "bars": 0,
                    "breakouts": 0,
                    "triggered": 0,
                    "items": [],
                }
            )

    assets_with_data = sum(1 for r in results if (r.get("bars") or 0) > 0)
    if assets_with_data == 0 and out_path.exists():
        print(f"[UNIVERSAL_BR] {strategy_id}: no fresh data, keeping previous file")
        return

    all_items = []
    summary = {}
    notes = []
    total_breakouts = 0
    for r in sorted(results, key=lambda x: str(x.get("asset", ""))):
        asset = str(r.get("asset"))
        summary[asset] = {
            "symbol": r.get("symbol"),
            "bars": int(r.get("bars") or 0),
            "breakouts": int(r.get("breakouts") or 0),
            "triggered": int(r.get("triggered") or 0),
        }
        total_breakouts += int(r.get("breakouts") or 0)
        all_items.extend(r.get("items") or [])
        if int(r.get("bars") or 0) == 0:
            notes.append(f"{_title_case_asset(asset)}: data unavailable")
        elif int(r.get("triggered") or 0) == 0:
            notes.append(f"{_title_case_asset(asset)}: no valid entries this run")

    def _sort_key(item):
        t = pd.to_datetime(item.get("entry_time") or item.get("signal_time"), errors="coerce", utc=True)
        if pd.isna(t):
            return -1
        return int(t.value)

    all_items = sorted(all_items, key=_sort_key, reverse=True)

    payload = {
        "strategy_id": strategy_id,
        "title": title,
        "owner": "HARSHIT",
        "trade_type": "INTRADAY",
        "market": market,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interval": INTERVAL,
        "rules": {
            "volume_multiple": VOLUME_MULT,
            "lookback_volume": MOMENTUM_LOOKBACK,
            "adx_min": ADX_MIN,
            "breakout_condition": "BUY: close > EMA9 > VWAP and > Daily EMA9 | SELL: close < EMA9 < VWAP and < Daily EMA9",
            "daily_ema_filter": "daily close must align with trade side vs Daily EMA9",
            "entry_window_candles": f"{WINDOW_START}-{WINDOW_END}",
            "entry_trigger": "retest touch + side-confirm candle (green above EMA9/VWAP for BUY, red below EMA9/VWAP for SELL)",
            "min_rr": MIN_RR,
            "target_atr_multiple": TARGET_ATR_MULT,
        },
        "counts": {
            "assets": len(assets),
            "assets_with_data": assets_with_data,
            "breakouts": total_breakouts,
            "triggered": len(all_items),
        },
        "asset_summary": summary,
        "notes": [
            "1H breakout-retest strategy applied to this page universe.",
            "Entry allowed only on candle 5-6 after breakout.",
            "Telegram alert is sent by backend daily notifier for new valid entries.",
            *notes[:10],
        ],
        "items": all_items,
    }
    _write_payload(payload)
    print(
        f"[UNIVERSAL_BR] {strategy_id}: assets={len(assets)} "
        f"with_data={assets_with_data} breakouts={total_breakouts} triggered={len(all_items)}"
    )


def main():
    if LEGACY_GOLD_OUT.exists():
        try:
            LEGACY_GOLD_OUT.unlink()
        except Exception:
            pass

    for cfg in _load_market_configs():
        _scan_market(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
