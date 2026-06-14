#!/usr/bin/env python3
import argparse
import csv
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.dhan_intraday import fetch_intraday_history as _fetch_dhan_intraday_history  # noqa: E402
from backend.equity_gainer_indicator_study import _atr  # noqa: E402


REPORT_DIR = ROOT / "backend" / "reports"
CACHE_DIR = REPORT_DIR / "cache" / "bullish_15m_oi_ema9"
DEFAULT_INITIAL_CAPITAL = 100000.0
DEFAULT_RISK_PER_TRADE_PCT = 1.0
DEFAULT_MAX_HOLD_SESSIONS = 2
DEFAULT_MIN_RANGE_MULTIPLE = 3.0
DEFAULT_MAX_RANGE_MULTIPLE = 4.0
DEFAULT_MIN_REWARD_RISK = 1.5
DEFAULT_PATTERN_SIDE = "bullish"
DEFAULT_SYMBOL = "BAJFINANCE"
DEFAULT_MARKET = "india"


def _safe_float(value, digits=4):
    try:
        value = float(value)
    except Exception:
        return None
    if math.isfinite(value):
        return round(value, digits)
    return None


def _safe_bool(value):
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return bool(value)


def _normalize_pattern_side(side):
    side = str(side or "").strip().lower()
    if side in {"bull", "bullish", "buy", "long"}:
        return "bullish"
    if side in {"bear", "bearish", "sell", "short"}:
        return "bearish"
    raise ValueError(f"Unsupported pattern side: {side}")


def _symbol_slug(symbol):
    return str(symbol or "").strip().lower().replace(" ", "_").replace(".", "_").replace("/", "_")


def _pct_diff(a, b):
    try:
        a = float(a)
        b = float(b)
    except Exception:
        return None
    if not math.isfinite(a) or not math.isfinite(b) or b == 0.0:
        return None
    return ((a / b) - 1.0) * 100.0


def _load_intraday_15m(symbol, market="india", refresh=False, chunk_days=14):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    last_err = None
    for period in ("60d", "59d", "45d", "30d"):
        cache_name = f"{str(symbol).replace('/', '_').replace('.', '_')}_{period}_15m.csv"
        cache_path = CACHE_DIR / cache_name
        if cache_path.exists() and not refresh:
            try:
                cached = pd.read_csv(cache_path, parse_dates=["Datetime"])
                if not cached.empty:
                    cached["Datetime"] = pd.to_datetime(cached["Datetime"], utc=True)
                    frame = cached.set_index("Datetime")[["Open", "High", "Low", "Close", "Volume"]].copy()
                    if not frame.empty:
                        return frame.sort_index(), period, "cache"
            except Exception:
                pass

        try:
            frame, contract_meta = _fetch_dhan_intraday_history(symbol, interval="15m", data_range=period, market=market)
        except Exception as exc:
            last_err = exc
            continue
        if frame is None or frame.empty:
            continue
        frame = frame.sort_index().copy()
        index_name = frame.index.name or "index"
        to_write = frame.reset_index().rename(columns={index_name: "Datetime"})
        to_write.to_csv(cache_path, index=False)
        return frame, period, {"source": "dhan", "contract": contract_meta}

    if last_err is not None:
        raise last_err
    raise RuntimeError(f"No 15m history returned for {symbol}")


def _anchored_session_vwap(frame):
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    out = pd.Series(index=frame.index, dtype=float)
    for _, group in frame.groupby(frame.index.date):
        if group.empty:
            continue
        typical = (group["High"] + group["Low"] + group["Close"]) / 3.0
        vol = pd.to_numeric(group["Volume"], errors="coerce").fillna(0.0)
        cum_vol = vol.cumsum().replace(0.0, np.nan)
        cum_pv = (typical * vol).cumsum()
        out.loc[group.index] = cum_pv / cum_vol
    return out


def _resample_session_ohlcv(frame, rule, offset_minutes=45):
    if frame is None or frame.empty:
        return pd.DataFrame()
    shifted = frame.copy()
    shifted.index = shifted.index - pd.Timedelta(minutes=offset_minutes)
    out = (
        shifted.resample(rule, label="right", closed="right", origin="start_day")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open", "High", "Low", "Close"])
    )
    out.index = out.index + pd.Timedelta(minutes=offset_minutes)
    return out


def _compute_3h_ema_frame(df):
    if df is None or df.empty or len(df) < 20:
        return pd.DataFrame()
    frame = df.copy().sort_index()
    close = frame["Close"]
    ema9 = close.ewm(span=9, adjust=False).mean()
    out = pd.DataFrame(index=frame.index)
    out["ema9"] = ema9
    out["ema9_prev"] = ema9.shift(1)
    out["ema9_slope"] = ema9 - ema9.shift(1)
    return out


def _round_down(value, interval):
    return math.floor(float(value) / float(interval)) * float(interval)


def _round_up(value, interval):
    return math.ceil(float(value) / float(interval)) * float(interval)


def _strike_interval_for_price(price):
    price = float(price)
    if price < 400.0:
        return 5.0
    if price <= 1000.0:
        return 20.0
    return 50.0


def _build_proxy_oi_levels(entry_price, signal_low, signal_high, ema9_3h, vwap_15m, atr15):
    interval = _strike_interval_for_price(entry_price)
    support_anchor = min(
        x for x in [entry_price, signal_low, ema9_3h, vwap_15m] if x is not None and math.isfinite(float(x))
    )
    resistance_anchor = max(
        x for x in [entry_price, signal_high, ema9_3h, vwap_15m] if x is not None and math.isfinite(float(x))
    )
    put_wall = _round_down(support_anchor, interval)
    call_wall = _round_up(resistance_anchor, interval)
    if put_wall >= entry_price:
        put_wall = entry_price - interval
    if call_wall <= entry_price:
        call_wall = entry_price + interval
    support_gap = entry_price - put_wall
    resistance_gap = call_wall - entry_price
    confirmed = (
        support_gap > 0.0
        and resistance_gap > 0.0
        and support_gap <= max(2.5 * float(atr15), interval * 1.5)
        and resistance_gap >= max(1.0 * float(atr15), interval * 1.0)
    )
    return {
        "interval": float(interval),
        "put_wall": round(float(put_wall), 4),
        "call_wall": round(float(call_wall), 4),
        "support_gap": round(float(support_gap), 4),
        "resistance_gap": round(float(resistance_gap), 4),
        "confirmed": bool(confirmed),
    }


def _attach_intraday_context(frame):
    frame = frame.copy().sort_index()
    frame["range"] = frame["High"] - frame["Low"]
    frame["body"] = (frame["Close"] - frame["Open"]).abs()
    frame["avg_range_30"] = frame["range"].rolling(30, min_periods=30).mean().shift(1)
    frame["avg_body_30"] = frame["body"].rolling(30, min_periods=30).mean().shift(1)
    frame["avg_vol_30"] = frame["Volume"].rolling(30, min_periods=30).mean().shift(1)
    frame["prior_30_high"] = frame["High"].rolling(30, min_periods=30).max().shift(1)
    frame["prior_30_low"] = frame["Low"].rolling(30, min_periods=30).min().shift(1)
    frame["close_location"] = (frame["Close"] - frame["Low"]) / (frame["range"].replace(0.0, np.nan))
    frame["range_multiple"] = frame["range"] / frame["avg_range_30"]
    frame["body_multiple"] = frame["body"] / frame["avg_body_30"]
    frame["volume_multiple"] = frame["Volume"] / frame["avg_vol_30"]
    frame["atr15"] = _atr(frame[["Open", "High", "Low", "Close", "Volume"]], 14)
    frame["session_vwap"] = _anchored_session_vwap(frame[["Open", "High", "Low", "Close", "Volume"]])
    frame["close_from_low_loc"] = (frame["Close"] - frame["Low"]) / (frame["range"].replace(0.0, np.nan))
    frame["close_from_high_loc"] = (frame["High"] - frame["Close"]) / (frame["range"].replace(0.0, np.nan))

    tf3 = _resample_session_ohlcv(frame[["Open", "High", "Low", "Close", "Volume"]], "3h", offset_minutes=45)
    tf3_ind = _compute_3h_ema_frame(tf3)
    if tf3_ind is None or tf3_ind.empty:
        frame["ema9_3h"] = np.nan
        frame["ema9_3h_prev"] = np.nan
        frame["ema9_3h_slope"] = np.nan
        return frame

    tf3_for_merge = tf3_ind.reset_index()
    if "bar_time" not in tf3_for_merge.columns:
        index_col = tf3_ind.index.name or "index"
        if index_col in tf3_for_merge.columns:
            tf3_for_merge = tf3_for_merge.rename(columns={index_col: "bar_time"})
        else:
            tf3_for_merge = tf3_for_merge.rename(columns={tf3_for_merge.columns[0]: "bar_time"})
    tf3_for_merge = tf3_for_merge[["bar_time", "ema9"]].sort_values("bar_time")
    tf3_for_merge["ema9_prev"] = tf3_for_merge["ema9"].shift(1)
    tf3_for_merge["ema9_slope"] = tf3_for_merge["ema9"] - tf3_for_merge["ema9_prev"]

    intraday = frame.reset_index()
    if "bar_time" not in intraday.columns:
        index_col = frame.index.name or "index"
        if index_col in intraday.columns:
            intraday = intraday.rename(columns={index_col: "bar_time"})
        else:
            intraday = intraday.rename(columns={intraday.columns[0]: "bar_time"})
    intraday = intraday.sort_values("bar_time")
    merged = pd.merge_asof(
        intraday,
        tf3_for_merge,
        on="bar_time",
        direction="backward",
        allow_exact_matches=True,
    )
    merged = merged.set_index("bar_time")
    frame["ema9_3h"] = merged["ema9"].values
    frame["ema9_3h_prev"] = merged["ema9_prev"].values
    frame["ema9_3h_slope"] = merged["ema9_slope"].values
    return frame


def _classify_signal(
    row,
    min_range_multiple=DEFAULT_MIN_RANGE_MULTIPLE,
    max_range_multiple=DEFAULT_MAX_RANGE_MULTIPLE,
    side=DEFAULT_PATTERN_SIDE,
):
    if row is None:
        return None
    side = _normalize_pattern_side(side)
    close = row["Close"]
    open_ = row["Open"]
    high = row["High"]
    low = row["Low"]
    ema9_3h = row.get("ema9_3h")
    ema9_prev = row.get("ema9_3h_prev")
    vwap = row.get("session_vwap")
    range_multiple = row.get("range_multiple")
    body_multiple = row.get("body_multiple")
    volume_multiple = row.get("volume_multiple")
    prior_30_high = row.get("prior_30_high")
    prior_30_low = row.get("prior_30_low")
    close_location = row.get("close_location")
    close_from_low_loc = row.get("close_from_low_loc")
    close_from_high_loc = row.get("close_from_high_loc")
    atr15 = row.get("atr15")
    if any(
        x is None or (isinstance(x, float) and not math.isfinite(float(x)))
        for x in [
            close,
            open_,
            high,
            low,
            ema9_3h,
            ema9_prev,
            vwap,
            range_multiple,
            body_multiple,
            volume_multiple,
            prior_30_high,
            prior_30_low,
            close_location,
            close_from_low_loc,
            close_from_high_loc,
            atr15,
        ]
    ):
        return None
    if not (float(min_range_multiple) <= float(range_multiple) <= float(max_range_multiple)):
        return None
    setup_type = None
    if side == "bullish":
        bullish_candle = float(close) > float(open_) and float(close_location) >= 0.65
        if not bullish_candle:
            return None
        trend_ok = float(close) > float(ema9_3h) and float(ema9_3h) > float(ema9_prev) and float(close) > float(vwap)
        if not trend_ok:
            return None
        if (
            float(close) > float(prior_30_high)
            and float(volume_multiple) >= 1.5
            and float(body_multiple) >= 2.0
        ):
            setup_type = "breakout"
        elif (
            float(low) <= min(float(ema9_3h), float(vwap)) * 1.003
            and float(close) >= max(float(ema9_3h), float(vwap))
            and float(volume_multiple) >= 1.1
            and float(body_multiple) >= 2.0
        ):
            setup_type = "pullback"
        else:
            return None
    else:
        bearish_candle = float(close) < float(open_) and float(close_location) <= 0.35
        if not bearish_candle:
            return None
        trend_ok = float(close) < float(ema9_3h) and float(ema9_3h) < float(ema9_prev) and float(close) < float(vwap)
        if not trend_ok:
            return None
        if (
            float(close) < float(prior_30_low)
            and float(volume_multiple) >= 1.5
            and float(body_multiple) >= 2.0
        ):
            setup_type = "breakout"
        elif (
            float(high) >= max(float(ema9_3h), float(vwap)) * 0.997
            and float(close) <= min(float(ema9_3h), float(vwap))
            and float(volume_multiple) >= 1.1
            and float(body_multiple) >= 2.0
        ):
            setup_type = "pullback"
        else:
            return None

    return {
        "side": side,
        "setup_type": setup_type,
        "range_multiple": round(float(range_multiple), 4),
        "body_multiple": round(float(body_multiple), 4),
        "volume_multiple": round(float(volume_multiple), 4),
        "atr15": round(float(atr15), 4),
        "ema9_3h": round(float(ema9_3h), 4),
        "ema9_3h_prev": round(float(ema9_prev), 4),
        "session_vwap": round(float(vwap), 4),
        "prior_30_high": round(float(prior_30_high), 4),
        "prior_30_low": round(float(prior_30_low), 4),
        "close_location": round(float(close_location), 4),
        "close_from_low_loc": round(float(close_from_low_loc), 4),
        "close_from_high_loc": round(float(close_from_high_loc), 4),
    }


def _build_candidate(frame, idx, signal_meta, min_reward_risk=DEFAULT_MIN_REWARD_RISK):
    signal_row = frame.iloc[idx]
    entry_idx = idx + 1
    if entry_idx >= len(frame):
        return None
    entry_row = frame.iloc[entry_idx]
    entry_price = float(entry_row["Open"]) if pd.notna(entry_row["Open"]) else float(entry_row["Close"])
    atr15 = float(signal_meta["atr15"])
    signal_low = float(signal_row["Low"])
    signal_high = float(signal_row["High"])
    ema9_3h = float(signal_meta["ema9_3h"])
    session_vwap = float(signal_meta["session_vwap"])
    side = str(signal_meta.get("side") or "bullish").strip().lower()
    proxy = _build_proxy_oi_levels(entry_price, signal_low, signal_high, ema9_3h, session_vwap, atr15)
    stop_buffer = max(0.10 * atr15, entry_price * 0.001)
    if side == "bearish":
        stop_anchor = max(signal_high, ema9_3h, proxy["call_wall"])
        stop_price = round(float(stop_anchor) + float(stop_buffer), 4)
        target_anchor = min(signal_meta["prior_30_low"], proxy["put_wall"])
        risk = stop_price - entry_price
        reward = entry_price - target_anchor
    else:
        stop_anchor = min(signal_low, ema9_3h, proxy["put_wall"])
        stop_price = round(float(stop_anchor) - float(stop_buffer), 4)
        target_anchor = max(signal_meta["prior_30_high"], proxy["call_wall"])
        risk = entry_price - stop_price
        reward = target_anchor - entry_price
    if risk <= 0.0 or reward <= 0.0:
        return None
    reward_risk = reward / risk
    if reward_risk < float(min_reward_risk):
        return None

    signal_time = signal_row.name
    entry_time = entry_row.name
    side_label = "SHORT" if side == "bearish" else "LONG"
    reason = (
        f"{side_label} {signal_meta['setup_type'].title()} | candle {signal_meta['range_multiple']:.2f}x avg range, "
        f"body {signal_meta['body_multiple']:.2f}x avg body, vol {signal_meta['volume_multiple']:.2f}x avg, "
        f"3h EMA9 {signal_meta['ema9_3h']:.2f}, VWAP {signal_meta['session_vwap']:.2f}, "
        f"proxy OI {'confirmed' if proxy['confirmed'] else 'unconfirmed'}"
    )
    return {
        "symbol": signal_row.get("symbol", ""),
        "direction": side,
        "setup_type": signal_meta["setup_type"],
        "signal_idx": idx,
        "signal_time": signal_time,
        "entry_idx": entry_idx,
        "entry_time": entry_time,
        "entry_price": round(float(entry_price), 4),
        "stop_price": round(float(stop_price), 4),
        "target_price": round(float(target_anchor), 4),
        "risk": round(float(risk), 4),
        "reward": round(float(reward), 4),
        "reward_risk": round(float(reward_risk), 4),
        "range_multiple": signal_meta["range_multiple"],
        "body_multiple": signal_meta["body_multiple"],
        "volume_multiple": signal_meta["volume_multiple"],
        "atr15": signal_meta["atr15"],
        "ema9_3h": signal_meta["ema9_3h"],
        "ema9_3h_prev": signal_meta["ema9_3h_prev"],
        "session_vwap": signal_meta["session_vwap"],
        "prior_30_high": signal_meta["prior_30_high"],
        "prior_30_low": signal_meta["prior_30_low"],
        "close_location": signal_meta["close_location"],
        "proxy_interval": proxy["interval"],
        "proxy_put_wall": proxy["put_wall"],
        "proxy_call_wall": proxy["call_wall"],
        "proxy_support_gap": proxy["support_gap"],
        "proxy_resistance_gap": proxy["resistance_gap"],
        "proxy_oi_pass": proxy["confirmed"],
        "reason": reason,
    }


def _simulate_trade(frame, candidate, session_dates, max_hold_sessions):
    signal_date = candidate["signal_time"].date()
    if signal_date not in session_dates:
        return None
    signal_session_idx = session_dates.index(signal_date)
    end_session_idx = min(signal_session_idx + max_hold_sessions, len(session_dates) - 1)
    allowed_dates = set(session_dates[signal_session_idx : end_session_idx + 1])

    entry_idx = candidate["entry_idx"]
    if entry_idx >= len(frame):
        return None

    bars = frame.iloc[entry_idx:]
    bars = bars[bars.index.date <= session_dates[end_session_idx]]
    if bars.empty:
        return None

    entry_price = float(candidate["entry_price"])
    stop_price = float(candidate["stop_price"])
    target_price = float(candidate["target_price"])
    direction = str(candidate.get("direction") or "bullish").strip().lower()
    risk = (entry_price - stop_price) if direction != "bearish" else (stop_price - entry_price)
    if risk <= 0.0:
        return None

    high_water = entry_price
    low_water = entry_price
    exit_price = None
    exit_reason = None
    exit_time = None
    exit_idx = None

    for idx, (_, bar) in enumerate(bars.iterrows(), start=entry_idx):
        if bar.name.date() not in allowed_dates:
            break
        high = float(bar["High"]) if pd.notna(bar["High"]) else None
        low = float(bar["Low"]) if pd.notna(bar["Low"]) else None
        close = float(bar["Close"]) if pd.notna(bar["Close"]) else None
        if high is not None:
            high_water = max(high_water, high)
        if low is not None:
            low_water = min(low_water, low)
        if direction == "bearish":
            stop_hit = high is not None and high >= stop_price
            target_hit = low is not None and low <= target_price
        else:
            stop_hit = low is not None and low <= stop_price
            target_hit = high is not None and high >= target_price
        if stop_hit and target_hit:
            exit_price = stop_price if direction != "bearish" else stop_price
            exit_reason = "stop_and_target"
            exit_time = bar.name
            exit_idx = idx
            break
        if stop_hit:
            exit_price = stop_price
            exit_reason = "stop"
            exit_time = bar.name
            exit_idx = idx
            break
        if target_hit:
            exit_price = target_price
            exit_reason = "target"
            exit_time = bar.name
            exit_idx = idx
            break

    if exit_price is None:
        last_bar = bars.iloc[-1]
        exit_price = float(last_bar["Close"]) if pd.notna(last_bar["Close"]) else float(last_bar["Open"])
        exit_reason = "time"
        exit_time = last_bar.name
        exit_idx = bars.index.get_loc(last_bar.name) + entry_idx

    pnl_pct = (_pct_diff(exit_price, entry_price) or 0.0) if direction != "bearish" else (_pct_diff(entry_price, exit_price) or 0.0)
    r_multiple = ((exit_price - entry_price) / risk) if direction != "bearish" else ((entry_price - exit_price) / risk)
    if direction == "bearish":
        mfe_pct = _pct_diff(entry_price, low_water) or 0.0
        mae_pct = _pct_diff(high_water, entry_price) or 0.0
    else:
        mfe_pct = _pct_diff(high_water, entry_price) or 0.0
        mae_pct = _pct_diff(low_water, entry_price) or 0.0

    return {
        **candidate,
        "exit_idx": exit_idx,
        "exit_time": exit_time,
        "exit_price": round(float(exit_price), 4),
        "exit_reason": exit_reason,
        "pnl_pct": round(float(pnl_pct), 4),
        "r_multiple": round(float(r_multiple), 4),
        "mfe_pct": round(float(mfe_pct), 4),
        "mae_pct": round(float(mae_pct), 4),
        "holding_bars": int(exit_idx - entry_idx + 1),
        "win": bool(exit_price < entry_price) if direction == "bearish" else bool(exit_price > entry_price),
    }


def _collect_signals(
    frame,
    side=DEFAULT_PATTERN_SIDE,
    min_range_multiple=DEFAULT_MIN_RANGE_MULTIPLE,
    max_range_multiple=DEFAULT_MAX_RANGE_MULTIPLE,
    min_reward_risk=DEFAULT_MIN_REWARD_RISK,
):
    side = _normalize_pattern_side(side)
    signals = []
    if frame is None or frame.empty or len(frame) < 60:
        return signals
    for idx in range(30, len(frame) - 1):
        row = frame.iloc[idx]
        meta = _classify_signal(row, min_range_multiple, max_range_multiple, side=side)
        if not meta:
            continue
        candidate = _build_candidate(frame, idx, meta, min_reward_risk)
        if candidate is None:
            continue
        candidate["symbol"] = row.get("symbol", "")
        signals.append(candidate)
    return signals


def _build_funnel_stats(
    frame,
    side=DEFAULT_PATTERN_SIDE,
    min_range_multiple=DEFAULT_MIN_RANGE_MULTIPLE,
    max_range_multiple=DEFAULT_MAX_RANGE_MULTIPLE,
):
    side = _normalize_pattern_side(side)
    stats = {
        "bars_scanned": 0,
        "range_gate": 0,
        "directional_gate": 0,
        "trend_gate": 0,
        "setup_gate": 0,
    }
    if frame is None or frame.empty or len(frame) < 60:
        return stats
    for idx in range(30, len(frame) - 1):
        stats["bars_scanned"] += 1
        row = frame.iloc[idx]
        rm = row.get("range_multiple")
        if rm is None or not math.isfinite(float(rm)):
            continue
        if not (float(min_range_multiple) <= float(rm) <= float(max_range_multiple)):
            continue
        stats["range_gate"] += 1
        bullish = row["Close"] > row["Open"] and row.get("close_location") is not None and float(row["close_location"]) >= 0.65
        bearish = row["Close"] < row["Open"] and row.get("close_location") is not None and float(row["close_location"]) <= 0.35
        directional_ok = bullish if side == "bullish" else bearish
        if not directional_ok:
            continue
        stats["directional_gate"] += 1
        ema9 = row.get("ema9_3h")
        ema9_prev = row.get("ema9_3h_prev")
        vwap = row.get("session_vwap")
        if (
            ema9 is None
            or ema9_prev is None
            or vwap is None
            or not all(math.isfinite(float(x)) for x in [ema9, ema9_prev, vwap])
        ):
            continue
        trend_ok = (
            float(row["Close"]) > float(ema9) and float(ema9) > float(ema9_prev) and float(row["Close"]) > float(vwap)
            if side == "bullish"
            else float(row["Close"]) < float(ema9) and float(ema9) < float(ema9_prev) and float(row["Close"]) < float(vwap)
        )
        if not trend_ok:
            continue
        stats["trend_gate"] += 1
        if _classify_signal(row, min_range_multiple, max_range_multiple, side=side):
            stats["setup_gate"] += 1
    return stats


def _summarize(trades, initial_capital, risk_per_trade_pct):
    if not trades:
        return {
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "flat": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "avg_r": 0.0,
            "final_equity": round(float(initial_capital), 2),
            "max_drawdown_pct": 0.0,
            "equity_curve": [],
        }

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    losses = sum(1 for t in trades if t["pnl_pct"] < 0)
    flat = len(trades) - wins - losses
    gross_profit = sum(max(t["r_multiple"], 0.0) for t in trades)
    gross_loss = abs(sum(min(t["r_multiple"], 0.0) for t in trades))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_r = float(np.mean([t["r_multiple"] for t in trades])) if trades else 0.0

    equity = float(initial_capital)
    peak = equity
    max_dd = 0.0
    equity_curve = []
    for trade in trades:
        equity *= 1.0 + ((risk_per_trade_pct / 100.0) * trade["r_multiple"])
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak else 0.0
        max_dd = max(max_dd, dd)
        equity_curve.append(
            {
                "time": trade["exit_time"].isoformat() if trade.get("exit_time") else None,
                "equity": round(equity, 2),
                "r_multiple": round(trade["r_multiple"], 4),
                "setup_type": trade["setup_type"],
                "proxy_oi_pass": trade["proxy_oi_pass"],
            }
        )

    return {
        "trade_count": len(trades),
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate_pct": round((wins / len(trades)) * 100.0, 2) if trades else 0.0,
        "profit_factor": round(float(profit_factor), 4) if math.isfinite(profit_factor) else None,
        "avg_r": round(float(avg_r), 4),
        "final_equity": round(float(equity), 2),
        "max_drawdown_pct": round(float(max_dd) * 100.0, 2),
        "equity_curve": equity_curve,
    }


def _subset_stats(trades, predicate):
    subset = [t for t in trades if predicate(t)]
    summary = _summarize(subset, DEFAULT_INITIAL_CAPITAL, DEFAULT_RISK_PER_TRADE_PCT)
    summary["trade_count"] = len(subset)
    return summary


def _pattern_split(trades):
    out = {}
    for setup in ("breakout", "pullback"):
        subset = [t for t in trades if t["setup_type"] == setup]
        stats = _summarize(subset, DEFAULT_INITIAL_CAPITAL, DEFAULT_RISK_PER_TRADE_PCT)
        stats["trade_count"] = len(subset)
        out[setup] = stats
    return out


def _write_csv(path, trades):
    fields = [
        "symbol",
        "direction",
        "setup_type",
        "signal_time",
        "entry_time",
        "exit_time",
        "entry_price",
        "stop_price",
        "target_price",
        "exit_price",
        "exit_reason",
        "pnl_pct",
        "r_multiple",
        "win",
        "range_multiple",
        "body_multiple",
        "volume_multiple",
        "atr15",
        "ema9_3h",
        "ema9_3h_prev",
        "session_vwap",
        "prior_30_high",
        "prior_30_low",
        "close_location",
        "proxy_interval",
        "proxy_put_wall",
        "proxy_call_wall",
        "proxy_support_gap",
        "proxy_resistance_gap",
        "proxy_oi_pass",
        "reward_risk",
        "reason",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for trade in trades:
            row = dict(trade)
            row["signal_time"] = trade["signal_time"].isoformat() if trade.get("signal_time") else None
            row["entry_time"] = trade["entry_time"].isoformat() if trade.get("entry_time") else None
            row["exit_time"] = trade["exit_time"].isoformat() if trade.get("exit_time") else None
            writer.writerow({field: row.get(field) for field in fields})


def main():
    parser = argparse.ArgumentParser(description="15m VWAP-EMA9 pilot backtest")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Stock symbol, default BAJFINANCE")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="Market label for symbol resolution")
    parser.add_argument("--side", default=DEFAULT_PATTERN_SIDE, choices=["bullish", "bearish"], help="Pattern side to scan")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached 15m data")
    parser.add_argument("--max-hold-sessions", type=int, default=DEFAULT_MAX_HOLD_SESSIONS, help="Max trading sessions to hold")
    parser.add_argument("--risk-per-trade-pct", type=float, default=DEFAULT_RISK_PER_TRADE_PCT, help="Notional risk per trade in percent of equity")
    parser.add_argument("--initial-capital", type=float, default=DEFAULT_INITIAL_CAPITAL, help="Starting capital for the equity curve")
    parser.add_argument("--min-range-multiple", type=float, default=DEFAULT_MIN_RANGE_MULTIPLE, help="Min 15m candle range multiple")
    parser.add_argument("--max-range-multiple", type=float, default=DEFAULT_MAX_RANGE_MULTIPLE, help="Max 15m candle range multiple")
    parser.add_argument("--min-reward-risk", type=float, default=DEFAULT_MIN_REWARD_RISK, help="Min structural reward/risk to accept a setup")
    parser.add_argument(
        "--out-json",
        default=None,
        help="Output JSON report path",
    )
    parser.add_argument(
        "--out-csv",
        default=None,
        help="Output trade CSV path",
    )
    args = parser.parse_args()

    scan_side = _normalize_pattern_side(args.side)
    min_range_multiple = float(args.min_range_multiple)
    max_range_multiple = float(args.max_range_multiple)
    min_reward_risk = float(args.min_reward_risk)

    frame, period_used, data_source = _load_intraday_15m(args.symbol, market=args.market, refresh=bool(args.refresh_cache))
    if frame is None or frame.empty:
        raise SystemExit(f"No 15m data available for {args.symbol} via Dhan")

    frame = frame.copy().sort_index()
    frame["symbol"] = str(args.symbol).upper()
    frame = _attach_intraday_context(frame)

    signals = _collect_signals(frame, scan_side, min_range_multiple, max_range_multiple, min_reward_risk)
    session_dates = sorted({ts.date() for ts in frame.index})
    executed_trades = []
    skipped_overlap = 0
    last_exit_idx = -1
    for candidate in signals:
        if candidate["signal_idx"] <= last_exit_idx:
            skipped_overlap += 1
            continue
        trade = _simulate_trade(frame, candidate, session_dates, max(1, int(args.max_hold_sessions)))
        if trade is None:
            continue
        executed_trades.append(trade)
        last_exit_idx = int(trade["exit_idx"])

    executed_trades.sort(key=lambda x: x["signal_time"])
    summary = _summarize(executed_trades, float(args.initial_capital), float(args.risk_per_trade_pct))
    overall = dict(summary)
    overall["signal_count"] = len(signals)
    overall["executed_trade_count"] = len(executed_trades)
    overall["skipped_overlap"] = skipped_overlap
    overall["setup_scope"] = f"{scan_side} only"
    funnel_stats = _build_funnel_stats(frame, scan_side, min_range_multiple, max_range_multiple)

    relaxed_signals = _collect_signals(frame, scan_side, min_range_multiple, max_range_multiple, min_reward_risk=1.0)
    relaxed_executed = []
    relaxed_last_exit_idx = -1
    for candidate in relaxed_signals:
        if candidate["signal_idx"] <= relaxed_last_exit_idx:
            continue
        trade = _simulate_trade(frame, candidate, session_dates, max(1, int(args.max_hold_sessions)))
        if trade is None:
            continue
        relaxed_executed.append(trade)
        relaxed_last_exit_idx = int(trade["exit_idx"])
    relaxed_executed.sort(key=lambda x: x["signal_time"])
    relaxed_summary = _summarize(relaxed_executed, float(args.initial_capital), float(args.risk_per_trade_pct))
    relaxed_summary["signal_count"] = len(relaxed_signals)
    relaxed_summary["executed_trade_count"] = len(relaxed_executed)

    pattern_split = _pattern_split(executed_trades)
    proxy_oi_split = {
        "confirmed": _subset_stats(executed_trades, lambda t: bool(t["proxy_oi_pass"])),
        "unconfirmed": _subset_stats(executed_trades, lambda t: not bool(t["proxy_oi_pass"])),
    }
    for section in proxy_oi_split.values():
        section["trade_count"] = int(section.get("trade_count", 0))

    best_pattern = None
    if pattern_split:
        best_pattern = max(
            pattern_split.items(),
            key=lambda item: (item[1].get("win_rate_pct", 0.0), item[1].get("avg_r", 0.0), item[1].get("trade_count", 0)),
        )[0]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": str(args.symbol).upper(),
        "side": scan_side,
        "yf_symbol": None,
        "data_source": data_source,
        "period_used": period_used,
        "window": {
            "start": frame.index[0].isoformat(),
            "end": frame.index[-1].isoformat(),
            "bars": int(len(frame)),
            "max_hold_sessions": int(args.max_hold_sessions),
        },
        "filter_funnel": funnel_stats,
        "filters": {
            "setup_scope": f"{scan_side} only",
            "range_multiple": f"{min_range_multiple:.1f}x to {max_range_multiple:.1f}x",
            "body_multiple_min": 2.0,
            "trend_filter": "3h EMA9 rising and price above 3h EMA9 + session VWAP" if scan_side == "bullish" else "3h EMA9 falling and price below 3h EMA9 + session VWAP",
            "breakout_filter": "close > prior 30-bar high and volume >= 1.5x 30-bar average" if scan_side == "bullish" else "close < prior 30-bar low and volume >= 1.5x 30-bar average",
            "pullback_filter": "low touches 3h EMA9, close reclaims above EMA9 and VWAP, volume >= 1.1x 30-bar average" if scan_side == "bullish" else "high tags 3h EMA9/VWAP, close rejects back below, volume >= 1.1x 30-bar average",
            "reward_risk_min": min_reward_risk,
            "oi_layer": "proxy only",
            "entry_at": "next 15m open after signal candle close",
            "risk_per_trade_pct": float(args.risk_per_trade_pct),
        },
        "summary": overall,
        "pattern_split": pattern_split,
        "proxy_oi_split": proxy_oi_split,
        "best_pattern_by_win_rate": best_pattern,
        "relaxed_reference": {
            "min_reward_risk": 1.0,
            "summary": relaxed_summary,
            "pattern_split": _pattern_split(relaxed_executed),
            "proxy_oi_split": {
                "confirmed": _subset_stats(relaxed_executed, lambda t: bool(t["proxy_oi_pass"])),
                "unconfirmed": _subset_stats(relaxed_executed, lambda t: not bool(t["proxy_oi_pass"])),
            },
            "trade_samples": [
                {
                    "signal_time": t["signal_time"].isoformat(),
                    "entry_time": t["entry_time"].isoformat() if t.get("entry_time") else None,
                    "exit_time": t["exit_time"].isoformat() if t.get("exit_time") else None,
                    "direction": t["direction"],
                    "setup_type": t["setup_type"],
                    "entry_price": t["entry_price"],
                    "stop_price": t["stop_price"],
                    "target_price": t["target_price"],
                    "exit_price": t["exit_price"],
                    "exit_reason": t["exit_reason"],
                    "pnl_pct": t["pnl_pct"],
                    "r_multiple": t["r_multiple"],
                    "win": t["win"],
                    "range_multiple": t["range_multiple"],
                    "body_multiple": t["body_multiple"],
                    "volume_multiple": t["volume_multiple"],
                    "ema9_3h": t["ema9_3h"],
                    "session_vwap": t["session_vwap"],
                    "proxy_put_wall": t["proxy_put_wall"],
                    "proxy_call_wall": t["proxy_call_wall"],
                    "proxy_oi_pass": t["proxy_oi_pass"],
                    "reason": t["reason"],
                }
                for t in relaxed_executed[:20]
            ],
        },
        "trade_samples": [
            {
                "signal_time": t["signal_time"].isoformat(),
                "entry_time": t["entry_time"].isoformat() if t.get("entry_time") else None,
                "exit_time": t["exit_time"].isoformat() if t.get("exit_time") else None,
                "direction": t["direction"],
                "setup_type": t["setup_type"],
                "entry_price": t["entry_price"],
                "stop_price": t["stop_price"],
                "target_price": t["target_price"],
                "exit_price": t["exit_price"],
                "exit_reason": t["exit_reason"],
                "pnl_pct": t["pnl_pct"],
                "r_multiple": t["r_multiple"],
                "win": t["win"],
                "range_multiple": t["range_multiple"],
                "body_multiple": t["body_multiple"],
                "volume_multiple": t["volume_multiple"],
                "ema9_3h": t["ema9_3h"],
                "session_vwap": t["session_vwap"],
                "proxy_put_wall": t["proxy_put_wall"],
                "proxy_call_wall": t["proxy_call_wall"],
                "proxy_oi_pass": t["proxy_oi_pass"],
                "reason": t["reason"],
            }
            for t in executed_trades[:20]
        ],
        "assumptions": {
            "entry_at": "next bar open",
            "hold_limit": "2 trading sessions after signal bar",
            "risk_curve": "1% notional risk per trade on a normalized equity curve",
            "proxy_oi": "rounded support/resistance levels are used as intraday proxy OI walls because historical option-chain data is not available in this repo",
            "reward_filter": "signals are only kept when structural reward to risk is at least the configured minimum",
        },
    }

    symbol_slug = _symbol_slug(args.symbol)
    out_json = Path(args.out_json or (REPORT_DIR / f"{symbol_slug}_{scan_side}_15m_oi_ema9_backtest.json"))
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str))
    out_csv = Path(args.out_csv or (REPORT_DIR / f"{symbol_slug}_{scan_side}_15m_oi_ema9_backtest_trades.csv"))
    _write_csv(out_csv, executed_trades)

    print(f"SYMBOL={report['symbol']}")
    print(f"SIDE={report['side']}")
    print(f"YF_SYMBOL={report['yf_symbol']}")
    print(f"DATA_SOURCE={report['data_source']}")
    print(f"PERIOD_USED={report['period_used']}")
    print(f"WINDOW={report['window']['start']}..{report['window']['end']}")
    print(f"BAR_COUNT={report['window']['bars']}")
    print(f"FUNNEL_RANGE_GATE={report['filter_funnel']['range_gate']}")
    print(f"FUNNEL_DIRECTIONAL_GATE={report['filter_funnel']['directional_gate']}")
    print(f"FUNNEL_TREND_GATE={report['filter_funnel']['trend_gate']}")
    print(f"FUNNEL_SETUP_GATE={report['filter_funnel']['setup_gate']}")
    print(f"SIGNALS={report['summary']['signal_count']}")
    print(f"TRADES={report['summary']['trade_count']}")
    print(f"WINS={report['summary']['wins']}")
    print(f"LOSSES={report['summary']['losses']}")
    print(f"WIN_RATE_PCT={report['summary']['win_rate_pct']}")
    print(f"PROFIT_FACTOR={report['summary']['profit_factor']}")
    print(f"AVG_R={report['summary']['avg_r']}")
    print(f"MAX_DRAWDOWN_PCT={report['summary']['max_drawdown_pct']}")
    print(f"FINAL_EQUITY={report['summary']['final_equity']}")
    print(f"BEST_PATTERN={report['best_pattern_by_win_rate']}")
    print(f"RELAXED_TRADES={report['relaxed_reference']['summary']['trade_count']}")
    print(f"RELAXED_WIN_RATE_PCT={report['relaxed_reference']['summary']['win_rate_pct']}")
    print(f"OUT_JSON={out_json.resolve()}")
    print(f"OUT_CSV={out_csv.resolve()}")


if __name__ == "__main__":
    main()
