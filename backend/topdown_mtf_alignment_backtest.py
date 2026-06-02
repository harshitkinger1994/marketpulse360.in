#!/usr/bin/env python3
import argparse
import csv
import json
import math
import re
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "market.db"
REPORTS_DIR = ROOT / "backend" / "reports"
CACHE_DIR = REPORTS_DIR / "cache" / "topdown_mtf_alignment"
INITIAL_CAPITAL = 100000.0
LOOKBACK_BUFFER_DAYS = 400
INTRADAY_BUFFER_DAYS = 60
DEFAULT_MAX_HOLD_DAYS = 5
DEFAULT_STOP_ATR_MULTIPLE = 1.8
DEFAULT_TARGET_ATR_MULTIPLE = 3.6
DEFAULT_PROFILE_NAME = "topdown_weekly_daily_intraday_v1"
YAHOO_INTRADAY_MAX_LOOKBACK_DAYS = 729

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.equity_gainer_indicator_study import (  # noqa: E402
    _adx,
    _atr,
    _bollinger_pos,
    _compute_indicators,
    _load_equity_universe,
    _resample_ohlcv,
    _rsi,
    _stochastic,
)
from backend.topdown_mtf_alignment_scan import (  # noqa: E402
    PROFILE,
    _evaluate_timeframe,
)


def _safe_date_five_years_ago(today):
    try:
        return today.replace(year=today.year - 5)
    except ValueError:
        return today - timedelta(days=365 * 5)


def _utc_day_start(day):
    return datetime.combine(day, dtime(0, 0), tzinfo=timezone.utc)


def _utc_day_end(day):
    return datetime.combine(day, dtime(23, 59, 59), tzinfo=timezone.utc)


def _safe_float(value, digits=4):
    try:
        value = float(value)
    except Exception:
        return None
    if math.isfinite(value):
        return round(value, digits)
    return None


def _safe_bool(value):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return bool(value)


def _pct_diff(a, b):
    try:
        a = float(a)
        b = float(b)
    except Exception:
        return None
    if not math.isfinite(a) or not math.isfinite(b) or b == 0.0:
        return None
    return ((a / b) - 1.0) * 100.0


def _sanitize_token(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())


def _yahoo_symbol_candidates(yf_symbol):
    raw = str(yf_symbol or "").strip()
    candidates = []
    seen = set()

    def _add(value):
        token = str(value or "").strip()
        if not token or token in seen:
            return
        seen.add(token)
        candidates.append(token)

    _add(raw)
    if raw.upper().endswith(".NS"):
        stem = raw[:-3]
        _add(f"{stem.replace('_', '-')}.NS")
    if raw.upper().endswith(".BO"):
        stem = raw[:-3]
        _add(f"{stem.replace('_', '-')}.BO")
    return candidates


class PriceStore:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(str(db_path))
        self.cache = {}

    def get_rows(self, key):
        lookup = str(key or "").strip().upper()
        if not lookup:
            return []
        if lookup in self.cache:
            return self.cache[lookup]
        rows = self.conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices WHERE index_name=? ORDER BY date",
            (lookup,),
        ).fetchall()
        out = []
        for d, o, h, l, c, v in rows:
            try:
                out.append(
                    {
                        "date": date.fromisoformat(str(d)),
                        "open": float(o) if o is not None else None,
                        "high": float(h) if h is not None else None,
                        "low": float(l) if l is not None else None,
                        "close": float(c) if c is not None else None,
                        "volume": float(v) if v is not None else None,
                    }
                )
            except Exception:
                continue
        self.cache[lookup] = out
        return out

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def _normalize_chart_result(result):
    if not result:
        return pd.DataFrame()
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "Open": quote.get("open") or [],
            "High": quote.get("high") or [],
            "Low": quote.get("low") or [],
            "Close": quote.get("close") or [],
            "Volume": quote.get("volume") or [],
        }
    )
    if frame.empty:
        return pd.DataFrame()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["Close"])
    if frame.empty:
        return pd.DataFrame()
    frame["Datetime"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    frame = (
        frame.drop_duplicates(subset=["Datetime"])
        .sort_values("Datetime")
        .set_index("Datetime")[["Open", "High", "Low", "Close", "Volume"]]
    )
    return frame


def _fetch_chart_range(symbol, interval, start_dt, end_dt, retries=2):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "period1": int(start_dt.timestamp()),
        "period2": int(end_dt.timestamp()),
        "interval": interval,
        "includePrePost": "false",
        "events": "div,splits",
    }
    last_err = None
    for _ in range(retries + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            return _normalize_chart_result(result)
        except Exception as exc:
            last_err = exc
            time.sleep(0.2)
    raise RuntimeError(f"Yahoo chart fetch failed for {symbol} {interval}: {last_err!r}")


def _load_intraday_hourly(yf_symbol, start_date, end_date, chunk_days=59):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    earliest_allowed = end_date - timedelta(days=YAHOO_INTRADAY_MAX_LOOKBACK_DAYS)
    effective_start_date = max(start_date, earliest_allowed)
    last_err = None

    for candidate in _yahoo_symbol_candidates(yf_symbol):
        cache_name = (
            f"{_sanitize_token(candidate)}_{effective_start_date.isoformat()}_{end_date.isoformat()}_60m.csv"
        )
        cache_path = CACHE_DIR / cache_name
        if cache_path.exists():
            cached = pd.read_csv(cache_path, parse_dates=["Datetime"])
            if not cached.empty:
                cached["Datetime"] = pd.to_datetime(cached["Datetime"], utc=True)
                return (
                    cached.set_index("Datetime")[["Open", "High", "Low", "Close", "Volume"]],
                    effective_start_date,
                )

        start_dt = _utc_day_start(effective_start_date)
        end_dt = _utc_day_start(end_date + timedelta(days=1))
        frames = []
        cursor = start_dt
        try:
            while cursor < end_dt:
                chunk_end = min(cursor + timedelta(days=chunk_days), end_dt)
                part = _fetch_chart_range(candidate, "60m", cursor, chunk_end)
                if not part.empty:
                    frames.append(part)
                cursor = chunk_end + timedelta(minutes=1)
        except Exception as exc:
            last_err = exc
            continue

        if not frames:
            continue

        merged = (
            pd.concat(frames)
            .sort_index()
            .loc[~pd.concat(frames).index.duplicated(keep="last")]
            .copy()
        )
        to_write = merged.reset_index().rename(columns={"index": "Datetime"})
        to_write.to_csv(cache_path, index=False)
        return merged, effective_start_date

    if last_err is not None:
        raise last_err
    return pd.DataFrame(), effective_start_date


def _fetch_daily_rows_yahoo(yf_symbol, start_date, end_date):
    start_dt = _utc_day_start(start_date)
    end_dt = _utc_day_start(end_date + timedelta(days=1))
    for candidate in _yahoo_symbol_candidates(yf_symbol):
        try:
            frame = _fetch_chart_range(candidate, "1d", start_dt, end_dt)
        except Exception:
            continue
        if frame.empty:
            continue
        out = []
        for ts, row in frame.iterrows():
            try:
                out.append(
                    {
                        "date": ts.date(),
                        "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                        "high": float(row["High"]) if pd.notna(row["High"]) else None,
                        "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
                        "close": float(row["Close"]) if pd.notna(row["Close"]) else None,
                        "volume": float(row["Volume"]) if pd.notna(row["Volume"]) else None,
                    }
                )
            except Exception:
                continue
        if out:
            return out
    return []


def _merge_rows(*row_sets):
    merged = {}
    for rows in row_sets:
        for row in rows or []:
            d = row.get("date")
            if not d:
                continue
            merged[d] = row
    return [merged[d] for d in sorted(merged)]


def _load_daily_rows(store, symbol_row, fetch_start, end_date):
    local_rows = [
        r
        for r in store.get_rows(symbol_row["symbol"])
        if fetch_start <= r["date"] <= end_date
    ]
    needs_remote = (
        len(local_rows) < 260
        or not local_rows
        or local_rows[-1]["date"] < (end_date - timedelta(days=10))
    )
    remote_rows = []
    remote_used = False
    if needs_remote:
        try:
            remote_rows = _fetch_daily_rows_yahoo(symbol_row["yf_symbol"], fetch_start, end_date)
            remote_used = bool(remote_rows)
        except Exception:
            remote_rows = []
    rows = _merge_rows(local_rows, remote_rows)
    source = "db+yahoo" if local_rows and remote_used else "yahoo" if remote_used else "db"
    return rows, source


def _rows_to_daily_frame(rows):
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "Datetime": [pd.Timestamp(r["date"], tz="UTC") for r in rows],
            "Open": [r.get("open") for r in rows],
            "High": [r.get("high") for r in rows],
            "Low": [r.get("low") for r in rows],
            "Close": [r.get("close") for r in rows],
            "Volume": [r.get("volume") for r in rows],
        }
    )
    for col in ("Open", "High", "Low", "Close", "Volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["Close"])
    if frame.empty:
        return pd.DataFrame()
    return frame.set_index("Datetime").sort_index()


def _compute_indicator_frame(df):
    if df is None or df.empty or len(df) < 20:
        return pd.DataFrame()
    frame = df.copy().sort_index()
    close = frame["Close"]
    volume = frame["Volume"]
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    rsi8 = _rsi(close, 8)
    rsi14 = _rsi(close, 14)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal
    atr14 = _atr(frame, 14)
    atr_pct = (atr14 / close.replace(0.0, np.nan)) * 100.0
    adx14 = _adx(frame, 14)
    stoch_k, stoch_d = _stochastic(frame, 14, 3)
    bb_pos = _bollinger_pos(close, 20, 2.0)
    vol_sma20 = volume.rolling(20, min_periods=20).mean()
    vol_mult20 = volume / vol_sma20.replace(0.0, np.nan)

    out = pd.DataFrame(index=frame.index)
    out["open"] = frame["Open"]
    out["high"] = frame["High"]
    out["low"] = frame["Low"]
    out["close"] = close
    out["volume"] = volume
    out["ema9"] = ema9
    out["ema21"] = ema21
    out["sma50"] = sma50
    out["sma200"] = sma200
    out["close_vs_ema9_pct"] = ((close / ema9) - 1.0) * 100.0
    out["close_vs_ema21_pct"] = ((close / ema21) - 1.0) * 100.0
    out["close_vs_sma50_pct"] = ((close / sma50) - 1.0) * 100.0
    out["close_vs_sma200_pct"] = ((close / sma200) - 1.0) * 100.0
    out["rsi8"] = rsi8
    out["rsi14"] = rsi14
    out["macd_line"] = macd_line
    out["macd_signal"] = macd_signal
    out["macd_hist"] = macd_hist
    out["atr14"] = atr14
    out["atr_pct"] = atr_pct
    out["adx14"] = adx14
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d
    out["bb_pos"] = bb_pos
    out["vol_mult20"] = vol_mult20
    out["above_ema9"] = close > ema9
    out["above_ema21"] = close > ema21
    out["above_sma50"] = close > sma50
    out["above_sma200"] = close > sma200
    out["ema9_gt_ema21"] = ema9 > ema21
    out["ema21_gt_sma50"] = ema21 > sma50
    out["macd_hist_pos"] = macd_hist > 0
    out["rsi14_gt_60"] = rsi14 > 60
    out["adx14_gt_20"] = adx14 > 20
    out["bb_pos_gt_0_5"] = bb_pos > 0.5
    return out


def _snapshot_from_series(row):
    if row is None:
        return None
    out = {"bar_time": row.name.isoformat()}
    for field in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "ema9",
        "ema21",
        "sma50",
        "sma200",
        "close_vs_ema9_pct",
        "close_vs_ema21_pct",
        "close_vs_sma50_pct",
        "close_vs_sma200_pct",
        "rsi8",
        "rsi14",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "atr14",
        "atr_pct",
        "adx14",
        "stoch_k",
        "stoch_d",
        "bb_pos",
        "vol_mult20",
    ):
        out[field] = _safe_float(row.get(field))
    for field in (
        "above_ema9",
        "above_ema21",
        "above_sma50",
        "above_sma200",
        "ema9_gt_ema21",
        "ema21_gt_sma50",
        "macd_hist_pos",
        "rsi14_gt_60",
        "adx14_gt_20",
        "bb_pos_gt_0_5",
    ):
        out[field] = _safe_bool(row.get(field))
    return out


def _latest_snapshot_on_or_before(indicator_frame, cutoff_dt):
    if indicator_frame is None or indicator_frame.empty:
        return None, None
    eligible = indicator_frame[indicator_frame.index <= cutoff_dt]
    if eligible.empty:
        return None, None
    row = eligible.iloc[-1]
    return _snapshot_from_series(row), row


def _build_weekly_snapshots_by_day(daily_frame):
    snapshots = {}
    if daily_frame is None or daily_frame.empty:
        return snapshots
    for ts in daily_frame.index:
        subset = daily_frame.loc[:ts]
        weekly = _resample_ohlcv(subset, "W-FRI")
        snapshot = _compute_indicators(weekly)
        if snapshot:
            snapshots[ts.date()] = snapshot
    return snapshots


def _collect_pre_intraday_candidates(daily_frame, daily_indicator_frame, weekly_by_day, start_date, end_date):
    candidates = []
    if daily_frame is None or daily_frame.empty or daily_indicator_frame.empty:
        return candidates
    for ts, row in daily_indicator_frame.iterrows():
        day = ts.date()
        if day < start_date or day > end_date:
            continue
        weekly_snapshot = weekly_by_day.get(day)
        daily_snapshot = _snapshot_from_series(row)
        if not weekly_snapshot or not daily_snapshot:
            continue
        weekly_eval = _evaluate_timeframe(weekly_snapshot, PROFILE["weekly"])
        daily_eval = _evaluate_timeframe(daily_snapshot, PROFILE["daily"])
        if weekly_eval["required_all_pass"] and daily_eval["required_all_pass"]:
            candidates.append(
                {
                    "signal_date": day,
                    "weekly_snapshot": weekly_snapshot,
                    "weekly_eval": weekly_eval,
                    "daily_snapshot": daily_snapshot,
                    "daily_eval": daily_eval,
                    "daily_row": row,
                }
            )
    return candidates


@dataclass
class Position:
    symbol: str
    market: str
    entry_date: date
    entry_idx: int
    signal_time_4h: str
    signal_time_3h: str
    entry_price: float
    stop_price: float
    target_price: float
    allocated_capital: float
    daily_atr14: float | None
    weekly_rsi14: float | None
    daily_rsi14: float | None
    daily_adx14: float | None
    rsi14_4h: float | None
    adx14_4h: float | None
    rsi14_3h: float | None
    adx14_3h: float | None


def _build_signal_rows(symbol_row, daily_rows, start_date, end_date):
    daily_frame = _rows_to_daily_frame(daily_rows)
    if daily_frame.empty or len(daily_frame) < 220:
        return [], {
            "daily_rows": len(daily_rows),
            "daily_data_start": daily_rows[0]["date"].isoformat() if daily_rows else None,
            "daily_data_end": daily_rows[-1]["date"].isoformat() if daily_rows else None,
            "pre_intraday_candidates": 0,
            "signals": 0,
            "skipped_no_intraday_data": 0,
        }, {}

    daily_ind = _compute_indicator_frame(daily_frame)
    weekly_by_day = _build_weekly_snapshots_by_day(daily_frame)
    pre_intraday = _collect_pre_intraday_candidates(
        daily_frame=daily_frame,
        daily_indicator_frame=daily_ind,
        weekly_by_day=weekly_by_day,
        start_date=start_date,
        end_date=end_date,
    )
    if not pre_intraday:
        diag = {
            "daily_rows": len(daily_rows),
            "daily_data_start": daily_rows[0]["date"].isoformat() if daily_rows else None,
            "daily_data_end": daily_rows[-1]["date"].isoformat() if daily_rows else None,
            "pre_intraday_candidates": 0,
            "signals": 0,
            "skipped_no_intraday_data": 0,
        }
        return [], diag, {}

    intraday_fetch_start = max(
        start_date - timedelta(days=INTRADAY_BUFFER_DAYS),
        min(item["signal_date"] for item in pre_intraday) - timedelta(days=INTRADAY_BUFFER_DAYS),
    )
    hourly, effective_intraday_start = _load_intraday_hourly(
        symbol_row["yf_symbol"],
        intraday_fetch_start,
        end_date,
    )
    if hourly.empty:
        diag = {
            "daily_rows": len(daily_rows),
            "daily_data_start": daily_rows[0]["date"].isoformat() if daily_rows else None,
            "daily_data_end": daily_rows[-1]["date"].isoformat() if daily_rows else None,
            "pre_intraday_candidates": len(pre_intraday),
            "signals": 0,
            "skipped_no_intraday_data": len(pre_intraday),
        }
        coverage = {
            "intraday_source": "yahoo_60m_chunked",
            "intraday_requested_start": intraday_fetch_start.isoformat(),
            "intraday_effective_start": effective_intraday_start.isoformat(),
            "intraday_rows": 0,
            "intraday_start": None,
            "intraday_end": None,
        }
        return [], diag, coverage

    tf4 = _resample_ohlcv(hourly, "4h")
    tf3 = _resample_ohlcv(hourly, "3h")
    tf4_ind = _compute_indicator_frame(tf4)
    tf3_ind = _compute_indicator_frame(tf3)
    signals = []
    skipped_no_intraday = 0
    daily_index_by_date = {row["date"]: idx for idx, row in enumerate(daily_rows)}

    for candidate in pre_intraday:
        signal_date = candidate["signal_date"]
        cutoff = _utc_day_end(signal_date)
        tf4_snapshot, tf4_row = _latest_snapshot_on_or_before(tf4_ind, cutoff)
        tf3_snapshot, tf3_row = _latest_snapshot_on_or_before(tf3_ind, cutoff)
        if not tf4_snapshot or not tf3_snapshot:
            skipped_no_intraday += 1
            continue
        tf4_eval = _evaluate_timeframe(tf4_snapshot, PROFILE["4h"])
        tf3_eval = _evaluate_timeframe(tf3_snapshot, PROFILE["3h"])
        if not (tf4_eval["required_all_pass"] and tf3_eval["required_all_pass"]):
            continue

        daily_row = candidate["daily_row"]
        atr14 = daily_row.get("atr14")
        close = daily_row.get("close")
        if atr14 is None or pd.isna(atr14) or close is None or pd.isna(close):
            continue
        entry_price = float(close)
        stop_price = entry_price - (DEFAULT_STOP_ATR_MULTIPLE * float(atr14))
        target_price = entry_price + (DEFAULT_TARGET_ATR_MULTIPLE * float(atr14))
        entry_idx = daily_index_by_date.get(signal_date)
        if entry_idx is None:
            continue

        signals.append(
            {
                "symbol": symbol_row["symbol"],
                "yf_symbol": symbol_row["yf_symbol"],
                "market": symbol_row["market"],
                "signal_date": signal_date,
                "entry_idx": entry_idx,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "signal_time_4h": tf4_snapshot.get("bar_time"),
                "signal_time_3h": tf3_snapshot.get("bar_time"),
                "weekly_snapshot": candidate["weekly_snapshot"],
                "daily_snapshot": candidate["daily_snapshot"],
                "4h_snapshot": tf4_snapshot,
                "3h_snapshot": tf3_snapshot,
                "daily_atr14": float(atr14),
            }
        )

    diag = {
        "daily_rows": len(daily_rows),
        "daily_data_start": daily_rows[0]["date"].isoformat() if daily_rows else None,
        "daily_data_end": daily_rows[-1]["date"].isoformat() if daily_rows else None,
        "pre_intraday_candidates": len(pre_intraday),
        "signals": len(signals),
        "skipped_no_intraday_data": skipped_no_intraday,
    }
    coverage = {
        "intraday_source": "yahoo_60m_chunked",
        "intraday_requested_start": intraday_fetch_start.isoformat(),
        "intraday_effective_start": effective_intraday_start.isoformat(),
        "intraday_rows": int(len(hourly)),
        "intraday_start": hourly.index[0].isoformat() if not hourly.empty else None,
        "intraday_end": hourly.index[-1].isoformat() if not hourly.empty else None,
    }
    return signals, diag, coverage


def _close_position(pos, exit_date, exit_price, exit_reason):
    ret = (exit_price / pos.entry_price) - 1.0
    final_value = pos.allocated_capital * (1.0 + ret)
    pnl_value = final_value - pos.allocated_capital
    return {
        "symbol": pos.symbol,
        "market": pos.market,
        "side": "BUY",
        "signal_date": pos.entry_date.isoformat(),
        "entry_date": pos.entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "signal_time_4h": pos.signal_time_4h,
        "signal_time_3h": pos.signal_time_3h,
        "entry_price": round(pos.entry_price, 4),
        "exit_price": round(exit_price, 4),
        "stop_price": round(pos.stop_price, 4),
        "target_price": round(pos.target_price, 4),
        "allocated_capital": round(pos.allocated_capital, 2),
        "final_value": round(final_value, 2),
        "pnl_value": round(pnl_value, 2),
        "return_pct": round(ret * 100.0, 2),
        "exit_reason": exit_reason,
        "holding_days": (exit_date - pos.entry_date).days,
        "daily_atr14": _safe_float(pos.daily_atr14),
        "weekly_rsi14": _safe_float(pos.weekly_rsi14),
        "daily_rsi14": _safe_float(pos.daily_rsi14),
        "daily_adx14": _safe_float(pos.daily_adx14),
        "rsi14_4h": _safe_float(pos.rsi14_4h),
        "adx14_4h": _safe_float(pos.adx14_4h),
        "rsi14_3h": _safe_float(pos.rsi14_3h),
        "adx14_3h": _safe_float(pos.adx14_3h),
    }, final_value


def _simulate(rows_by_symbol, signals_by_date, start_date, end_date, initial_capital, max_hold_days):
    all_dates = sorted(
        {
            row["date"]
            for rows in rows_by_symbol.values()
            for row in rows
            if start_date <= row["date"] <= end_date
        }
    )
    row_index_by_symbol = {
        symbol: {row["date"]: idx for idx, row in enumerate(rows)}
        for symbol, rows in rows_by_symbol.items()
    }
    positions = {}
    cash = float(initial_capital)
    trades = []

    for day in all_dates:
        for symbol, pos in list(positions.items()):
            idx = row_index_by_symbol[symbol].get(day)
            if idx is None or day <= pos.entry_date:
                continue
            row = rows_by_symbol[symbol][idx]
            stop_hit = row["low"] is not None and row["low"] <= pos.stop_price
            target_hit = row["high"] is not None and row["high"] >= pos.target_price
            exit_price = None
            exit_reason = None
            if stop_hit and target_hit:
                exit_price = pos.stop_price
                exit_reason = "stop_and_target"
            elif stop_hit:
                exit_price = pos.stop_price
                exit_reason = "stop"
            elif target_hit:
                exit_price = pos.target_price
                exit_reason = "target"
            elif idx >= pos.entry_idx + max_hold_days:
                exit_price = row["close"]
                exit_reason = "time"
            if exit_price is not None:
                trade, final_value = _close_position(pos, day, float(exit_price), exit_reason)
                trades.append(trade)
                cash += final_value
                del positions[symbol]

        day_signals = [s for s in signals_by_date.get(day, []) if s["symbol"] not in positions]
        if day_signals and cash > 0:
            allocation = cash / float(len(day_signals))
            for sig in day_signals:
                positions[sig["symbol"]] = Position(
                    symbol=sig["symbol"],
                    market=sig["market"],
                    entry_date=sig["signal_date"],
                    entry_idx=sig["entry_idx"],
                    signal_time_4h=sig["signal_time_4h"],
                    signal_time_3h=sig["signal_time_3h"],
                    entry_price=float(sig["entry_price"]),
                    stop_price=float(sig["stop_price"]),
                    target_price=float(sig["target_price"]),
                    allocated_capital=allocation,
                    daily_atr14=sig.get("daily_atr14"),
                    weekly_rsi14=(sig.get("weekly_snapshot") or {}).get("rsi14"),
                    daily_rsi14=(sig.get("daily_snapshot") or {}).get("rsi14"),
                    daily_adx14=(sig.get("daily_snapshot") or {}).get("adx14"),
                    rsi14_4h=(sig.get("4h_snapshot") or {}).get("rsi14"),
                    adx14_4h=(sig.get("4h_snapshot") or {}).get("adx14"),
                    rsi14_3h=(sig.get("3h_snapshot") or {}).get("rsi14"),
                    adx14_3h=(sig.get("3h_snapshot") or {}).get("adx14"),
                )
            cash = 0.0

    for symbol, pos in list(positions.items()):
        rows = rows_by_symbol[symbol]
        idx = max((i for i, row in enumerate(rows) if row["date"] <= end_date), default=-1)
        if idx < 0:
            continue
        exit_row = rows[idx]
        trade, final_value = _close_position(pos, exit_row["date"], float(exit_row["close"]), "end_of_test")
        trades.append(trade)
        cash += final_value
        del positions[symbol]

    return trades, cash


def _build_summary(
    trades,
    final_capital,
    initial_capital,
    start_date,
    end_date,
    max_hold_days,
    asset_diagnostics,
    data_sources,
):
    trade_count = len(trades)
    wins = sum(1 for t in trades if t["pnl_value"] > 0)
    losses = sum(1 for t in trades if t["pnl_value"] < 0)
    flat = trade_count - wins - losses
    years = max(1e-9, (end_date - start_date).days / 365.25)
    cagr = ((final_capital / initial_capital) ** (1.0 / years) - 1.0) if final_capital > 0 else -1.0

    per_symbol = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0})
    for trade in trades:
        stat = per_symbol[trade["symbol"]]
        stat["trades"] += 1
        if trade["pnl_value"] > 0:
            stat["wins"] += 1
        elif trade["pnl_value"] < 0:
            stat["losses"] += 1
        stat["net_pnl"] += trade["pnl_value"]

    per_symbol_rows = []
    for symbol, stat in sorted(per_symbol.items()):
        trade_total = stat["trades"]
        per_symbol_rows.append(
            {
                "symbol": symbol,
                "trades": trade_total,
                "wins": stat["wins"],
                "losses": stat["losses"],
                "win_rate_pct": round((stat["wins"] / trade_total) * 100.0, 2) if trade_total else 0.0,
                "net_pnl_inr": round(stat["net_pnl"], 2),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile_name": DEFAULT_PROFILE_NAME,
        "profile": PROFILE,
        "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "initial_capital": round(initial_capital, 2),
        "final_capital": round(final_capital, 2),
        "absolute_return_pct": round(((final_capital / initial_capital) - 1.0) * 100.0, 2),
        "cagr_pct": round(cagr * 100.0, 2),
        "trades": trade_count,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate_pct": round((wins / trade_count) * 100.0, 2) if trade_count else 0.0,
        "loss_rate_pct": round((losses / trade_count) * 100.0, 2) if trade_count else 0.0,
        "assumptions": {
            "position_sizing": "All available capital is split equally across same-day new signals. No leverage.",
            "entry_rule": "Entry at signal-day close after weekly + daily + 4h + 3h confirmation.",
            "exit_rule": f"Stop {DEFAULT_STOP_ATR_MULTIPLE:.1f}x daily ATR14, target {DEFAULT_TARGET_ATR_MULTIPLE:.1f}x daily ATR14, or {max(1, int(max_hold_days))} trading days.",
            "intraday_conflict_rule": "If stop and target hit on the same day, stop is assumed first.",
            "one_position_per_asset": True,
            "trade_side": "BUY only",
        },
        "methodology_notes": [
            "Weekly uses the same partial current-week W-FRI resample behavior as the live scan.",
            "4h and 3h are built from Yahoo 60m history, chunked locally and resampled.",
            "Yahoo 60m intraday history is vendor-limited to roughly the most recent 730 days, so an exact 4h/3h backtest cannot extend beyond that source window without another intraday archive.",
            "Exact entry filters match the Topdown Weekly-Daily-Intraday Alignment v1 scan profile.",
        ],
        "asset_diagnostics": asset_diagnostics,
        "data_sources": data_sources,
        "per_symbol": per_symbol_rows,
    }


def _write_csv(path, trades):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol",
        "market",
        "side",
        "signal_date",
        "entry_date",
        "exit_date",
        "signal_time_4h",
        "signal_time_3h",
        "entry_price",
        "exit_price",
        "stop_price",
        "target_price",
        "allocated_capital",
        "final_value",
        "pnl_value",
        "return_pct",
        "exit_reason",
        "holding_days",
        "daily_atr14",
        "weekly_rsi14",
        "daily_rsi14",
        "daily_adx14",
        "rsi14_4h",
        "adx14_4h",
        "rsi14_3h",
        "adx14_3h",
    ]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trades)


def main():
    parser = argparse.ArgumentParser(description="5-year backtest for Topdown Weekly-Daily-Intraday Alignment v1")
    parser.add_argument("--start-date", default="", help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", default="", help="End date YYYY-MM-DD")
    parser.add_argument("--initial-capital", type=float, default=INITIAL_CAPITAL, help="Initial capital")
    parser.add_argument("--max-hold-days", type=int, default=DEFAULT_MAX_HOLD_DAYS, help="Max holding days")
    parser.add_argument("--limit", type=int, default=0, help="Optional universe size limit for testing")
    parser.add_argument(
        "--out-json",
        default=str(REPORTS_DIR / "topdown_mtf_alignment_backtest_5y.json"),
        help="Output JSON report path",
    )
    parser.add_argument(
        "--out-csv",
        default=str(REPORTS_DIR / "topdown_mtf_alignment_backtest_5y_trades.csv"),
        help="Output trade CSV path",
    )
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    end_date = date.fromisoformat(args.end_date) if args.end_date else today
    start_date = date.fromisoformat(args.start_date) if args.start_date else _safe_date_five_years_ago(end_date)
    fetch_start = start_date - timedelta(days=LOOKBACK_BUFFER_DAYS)

    universe = _load_equity_universe()
    if args.limit and args.limit > 0:
        universe = universe[: int(args.limit)]

    store = PriceStore(DB_PATH)
    rows_by_symbol = {}
    signals_by_date = defaultdict(list)
    asset_diagnostics = {}
    data_sources = {}
    total_pre_intraday = 0
    total_signals = 0

    try:
        for row in universe:
            daily_rows, daily_source = _load_daily_rows(store, row, fetch_start, end_date)
            if not daily_rows:
                asset_diagnostics[row["symbol"]] = {
                    "daily_rows": 0,
                    "daily_data_start": None,
                    "daily_data_end": None,
                    "pre_intraday_candidates": 0,
                    "signals": 0,
                    "skipped_no_intraday_data": 0,
                }
                data_sources[row["symbol"]] = {
                    "market": row["market"],
                    "yf_symbol": row["yf_symbol"],
                    "daily_source": "missing",
                "daily_rows": 0,
                "daily_start": None,
                "daily_end": None,
                "intraday_source": "missing",
                "intraday_requested_start": None,
                "intraday_effective_start": None,
                "intraday_rows": 0,
                "intraday_start": None,
                "intraday_end": None,
                }
                continue

            rows_by_symbol[row["symbol"]] = daily_rows
            symbol_signals, diag, coverage = _build_signal_rows(
                symbol_row=row,
                daily_rows=daily_rows,
                start_date=start_date,
                end_date=end_date,
            )
            asset_diagnostics[row["symbol"]] = diag
            total_pre_intraday += diag.get("pre_intraday_candidates", 0)
            total_signals += diag.get("signals", 0)
            data_sources[row["symbol"]] = {
                "market": row["market"],
                "yf_symbol": row["yf_symbol"],
                "daily_source": daily_source,
                "daily_rows": len(daily_rows),
                "daily_start": daily_rows[0]["date"].isoformat() if daily_rows else None,
                "daily_end": daily_rows[-1]["date"].isoformat() if daily_rows else None,
                "intraday_source": coverage.get("intraday_source") if coverage else "not_needed",
                "intraday_requested_start": coverage.get("intraday_requested_start") if coverage else None,
                "intraday_effective_start": coverage.get("intraday_effective_start") if coverage else None,
                "intraday_rows": coverage.get("intraday_rows", 0) if coverage else 0,
                "intraday_start": coverage.get("intraday_start") if coverage else None,
                "intraday_end": coverage.get("intraday_end") if coverage else None,
            }
            for sig in symbol_signals:
                signals_by_date[sig["signal_date"]].append(sig)
    finally:
        store.close()

    trades, final_capital = _simulate(
        rows_by_symbol=rows_by_symbol,
        signals_by_date=signals_by_date,
        start_date=start_date,
        end_date=end_date,
        initial_capital=float(args.initial_capital),
        max_hold_days=max(1, int(args.max_hold_days)),
    )

    report = _build_summary(
        trades=trades,
        final_capital=final_capital,
        initial_capital=float(args.initial_capital),
        start_date=start_date,
        end_date=end_date,
        max_hold_days=max(1, int(args.max_hold_days)),
        asset_diagnostics=asset_diagnostics,
        data_sources=data_sources,
    )
    report["universe_size"] = len(universe)
    report["pre_intraday_candidate_count"] = total_pre_intraday
    report["signal_count"] = total_signals
    report["json_report_path"] = str(Path(args.out_json).resolve())
    report["trade_list_path"] = str(Path(args.out_csv).resolve())
    report["trade_list"] = trades

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2))
    _write_csv(Path(args.out_csv), trades)

    print(f"PERIOD={start_date.isoformat()}..{end_date.isoformat()}")
    print(f"UNIVERSE_SIZE={len(universe)}")
    print(f"PRE_INTRADAY_CANDIDATES={total_pre_intraday}")
    print(f"SIGNALS={total_signals}")
    print(f"TRADES={report['trades']}")
    print(f"WINS={report['wins']}")
    print(f"LOSSES={report['losses']}")
    print(f"WIN_RATE_PCT={report['win_rate_pct']:.2f}")
    print(f"FINAL_CAPITAL={report['final_capital']:.2f}")
    print(f"CAGR_PCT={report['cagr_pct']:.2f}")
    print(f"OUT_JSON={Path(args.out_json).resolve()}")
    print(f"OUT_CSV={Path(args.out_csv).resolve()}")


if __name__ == "__main__":
    main()
