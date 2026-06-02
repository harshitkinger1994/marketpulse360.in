#!/usr/bin/env python3
import argparse
import sys
import time
from io import StringIO
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from apollo_ema9_strategy import NIFTY50_TICKERS, _load_fno_tickers
from pathlib import Path
import json

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_LOOKBACK_DAYS = 5
RVOL_MULT = 2.0
RVOL_WINDOW = 20
BREAKOUT_LOOKBACK = 6
INSTITUTIONAL_RVOL_MULT = 1.8
CONSOLIDATION_WINDOW = 6
CONSOLIDATION_MAX_RANGE_PCT = 0.9
CONSOLIDATION_MAX_BODY_PCT = 0.35
CONSOLIDATION_MAX_VOL_CV = 0.55
MIN_BODY_RATIO = 0.6
MAX_UPPER_WICK_RATIO = 0.35
NIFTY500_LIST = Path(__file__).parent / "nifty500_tickers.txt"
NIFTY500_JSON = Path(__file__).parent / "nifty500_tickers.json"
STRATEGY_OUT_DIR = Path(__file__).resolve().parents[1] / "strategies"
STRATEGY_HISTORY_DIR = STRATEGY_OUT_DIR / "history"


def _safe_slug(value, fallback):
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    out = []
    prev_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        else:
            if not prev_dash:
                out.append("_")
                prev_dash = True
    slug = "".join(out).strip("_")
    return slug or fallback


def _download_nifty500_list():
    urls = [
        "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
        "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
        "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,text/plain,*/*",
        "Referer": "https://www.niftyindices.com/",
    }
    last_err = None
    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                last_err = f"{url} -> {resp.status_code}"
                continue
            content = resp.text
            if "Symbol" not in content and "SYMBOL" not in content:
                last_err = f"{url} -> unexpected content"
                continue
            df = pd.read_csv(StringIO(content))
            col = None
            for c in df.columns:
                if c.strip().lower() in ("symbol", "symbols"):
                    col = c
                    break
            if not col:
                last_err = f"{url} -> no symbol column"
                continue
            tickers = []
            for t in df[col].astype(str):
                t = t.strip()
                if not t:
                    continue
                if not t.endswith(".NS"):
                    t = f"{t}.NS"
                tickers.append(t)
            if tickers:
                NIFTY500_LIST.write_text("\n".join(sorted(set(tickers))) + "\n")
                return tickers
        except Exception as exc:
            last_err = exc
            continue
    return []


def _fetch_yahoo_5m(ticker, days=DEFAULT_LOOKBACK_DAYS, retries=2):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "range": f"{int(days)}d",
        "interval": "5m",
        "includePrePost": "false",
        "events": "div",
    }
    last_err = None
    for _ in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if not result:
                return pd.DataFrame()
            ts = result.get("timestamp") or []
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            df = pd.DataFrame({
                "ts": ts,
                "open": quote.get("open") or [],
                "high": quote.get("high") or [],
                "low": quote.get("low") or [],
                "close": quote.get("close") or [],
                "volume": quote.get("volume") or [],
            })
            df = df.dropna(subset=["close"])
            if df.empty:
                return df
            df["dt_utc"] = pd.to_datetime(df["ts"], unit="s", utc=True)
            df["dt_ist"] = df["dt_utc"].dt.tz_convert(IST)
            df["date_ist"] = df["dt_ist"].dt.date
            return df
        except Exception as exc:
            last_err = exc
            continue
    return pd.DataFrame()




def _add_vwap(df):
    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["pv"] = typical * df["volume"]
    df["cum_pv"] = df.groupby("date_ist")["pv"].cumsum()
    df["cum_vol"] = df.groupby("date_ist")["volume"].cumsum()
    df["vwap"] = df["cum_pv"] / df["cum_vol"]
    return df


def _calc_index_bias(index_df):
    if index_df.empty or len(index_df) < RVOL_WINDOW:
        return None
    d = _add_vwap(index_df.copy())
    d["ema9"] = d["close"].ewm(span=9, adjust=False).mean()
    last = d.iloc[-1]
    if pd.isna(last.get("vwap")) or pd.isna(last.get("ema9")):
        return None
    return bool(last["close"] > last["vwap"] and last["close"] > last["ema9"])


def _build_market_context():
    nifty = _fetch_yahoo_5m("^NSEI", days=2, retries=1)
    bank = _fetch_yahoo_5m("^NSEBANK", days=2, retries=1)
    nifty_bias = _calc_index_bias(nifty)
    bank_bias = _calc_index_bias(bank)
    if nifty_bias is None and bank_bias is None:
        overall = None
    elif nifty_bias is None:
        overall = bank_bias
    elif bank_bias is None:
        overall = nifty_bias
    else:
        overall = nifty_bias and bank_bias
    return {
        "nifty_bias_bullish": nifty_bias,
        "banknifty_bias_bullish": bank_bias,
        "bullish": overall,
    }


def _grade_signal(signal_row):
    score = 0
    rvol = float(signal_row.get("rvol", 0) or 0)
    body_ratio = float(signal_row.get("body_ratio", 0) or 0)
    close_vs_vwap_pct = float(signal_row.get("close_vs_vwap_pct", 0) or 0)
    breakout_gap_pct = float(signal_row.get("breakout_gap_pct", 0) or 0)
    if rvol >= 2.5:
        score += 2
    elif rvol >= 2.0:
        score += 1
    if body_ratio >= 0.75:
        score += 2
    elif body_ratio >= 0.6:
        score += 1
    if close_vs_vwap_pct >= 0.35:
        score += 1
    if breakout_gap_pct >= 0.15:
        score += 1
    if score >= 5:
        return "A"
    if score >= 3:
        return "B"
    return "C"


def _load_nifty500_tickers():
    if NIFTY500_LIST.exists() or NIFTY500_JSON.exists():
        pass
    else:
        _download_nifty500_list()
    if NIFTY500_JSON.exists():
        try:
            obj = json.loads(NIFTY500_JSON.read_text())
            if isinstance(obj, dict) and "tickers" in obj:
                return [t for t in obj["tickers"] if isinstance(t, str) and t.strip()]
        except Exception:
            pass
    if NIFTY500_LIST.exists():
        tickers = []
        for line in NIFTY500_LIST.read_text().splitlines():
            t = line.strip()
            if t and not t.startswith("#"):
                tickers.append(t)
        return tickers
    return []


def _compute_signals(df, filter_mode="basic", market_bullish=None):
    if df.empty or len(df) < RVOL_WINDOW:
        return pd.DataFrame()
    df = df.copy()
    df = _add_vwap(df)
    df["vol_sma_20"] = df["volume"].rolling(window=RVOL_WINDOW).mean()
    df["rvol"] = df["volume"] / df["vol_sma_20"]
    df["vol_rising"] = (df["volume"] > df["volume"].shift(1)) & (df["volume"].shift(1) > df["volume"].shift(2))
    df["range"] = (df["high"] - df["low"]).clip(lower=0.000001)
    df["body"] = (df["close"] - df["open"]).abs()
    df["body_ratio"] = df["body"] / df["range"]
    df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["upper_wick_ratio"] = (df["upper_wick"] / df["range"]).clip(lower=0)
    df["breakout_high"] = df["high"].shift(1).rolling(window=BREAKOUT_LOOKBACK).max()
    df["breakout"] = df["close"] > df["breakout_high"]
    df["close_vs_vwap_pct"] = ((df["close"] - df["vwap"]) / df["vwap"]) * 100
    df["breakout_gap_pct"] = ((df["close"] - df["breakout_high"]) / df["breakout_high"]) * 100

    basic_signal = (
        (df["volume"] > RVOL_MULT * df["vol_sma_20"])
        & (df["close"] > df["vwap"])
        & (df["breakout"] == True)
    )

    cons_high = df["high"].shift(1).rolling(window=CONSOLIDATION_WINDOW).max()
    cons_low = df["low"].shift(1).rolling(window=CONSOLIDATION_WINDOW).min()
    prev_close = df["close"].shift(1)
    cons_range_pct = ((cons_high - cons_low) / prev_close) * 100
    prev_body_pct = (df["body"].shift(1) / prev_close) * 100
    avg_prev_body_pct = prev_body_pct.rolling(window=CONSOLIDATION_WINDOW).mean()
    prev_vol = df["volume"].shift(1)
    vol_mean = prev_vol.rolling(window=CONSOLIDATION_WINDOW).mean()
    vol_std = prev_vol.rolling(window=CONSOLIDATION_WINDOW).std()
    vol_cv = vol_std / vol_mean

    consolidation_ok = (
        (cons_range_pct <= CONSOLIDATION_MAX_RANGE_PCT)
        & (avg_prev_body_pct <= CONSOLIDATION_MAX_BODY_PCT)
        & (vol_cv <= CONSOLIDATION_MAX_VOL_CV)
    )
    candle_quality_ok = (
        (df["body_ratio"] >= MIN_BODY_RATIO)
        & (df["upper_wick_ratio"] <= MAX_UPPER_WICK_RATIO)
        & (df["close"] > df["open"])
    )
    institutional_signal = (
        (df["volume"] > INSTITUTIONAL_RVOL_MULT * df["vol_sma_20"])
        & (df["close"] > df["vwap"])
        & (df["breakout"] == True)
        & consolidation_ok
        & candle_quality_ok
    )

    df["consolidation_ok"] = consolidation_ok
    df["candle_quality_ok"] = candle_quality_ok
    if market_bullish is False:
        institutional_signal = institutional_signal & False
    df["signal"] = institutional_signal if filter_mode == "institutional" else basic_signal
    return df


def run():
    start_ts = time.time()
    parser = argparse.ArgumentParser(description="5m momentum breakout scanner")
    parser.add_argument("--tickers", "-t", help="Comma-separated tickers override")
    parser.add_argument("--allow-stale", action="store_true", help="Allow signals from previous trading day")
    parser.add_argument("--all-today", action="store_true", help="List all signals from today (not just latest)")
    parser.add_argument("--only-retouch", action="store_true", help="Show only rows where VWAP retouch is YES")
    parser.add_argument("--min-close-ret", type=float, default=None, help="Min return to close (percent), e.g. 1.0")
    parser.add_argument("--nifty500", action="store_true", help="Use NIFTY 500 tickers from local list")
    parser.add_argument("--fno", action="store_true", help="Use F&O (NIFTY futures-eligible) tickers")
    parser.add_argument("--backfill-days", type=int, default=0, help="Generate history for last N trading days")
    parser.add_argument("--no-write", action="store_true", help="Do not write strategy JSON files")
    parser.add_argument("--debug", action="store_true", help="Print debug stats when no signals")
    parser.add_argument(
        "--filter-mode",
        choices=["basic", "institutional"],
        default="basic",
        help="Signal logic mode. basic=breakout+volume+VWAP, institutional=adds consolidation+candle quality+index alignment.",
    )
    parser.add_argument(
        "--strategy-prefix",
        default="intraday_momentum",
        help="Output strategy id/file prefix. Example: intraday_momentum_commodities",
    )
    parser.add_argument(
        "--market",
        default="all",
        help="Strategy market tag used by website filters (all/india/global/commodities/crypto)",
    )
    parser.add_argument(
        "--title-prefix",
        default="Intraday Momentum",
        help="Strategy title prefix shown in UI",
    )
    parser.add_argument(
        "--owner",
        default="HARSHIT",
        help="Owner label shown in UI",
    )
    args = parser.parse_args()

    strategy_prefix = _safe_slug(args.strategy_prefix, "intraday_momentum")
    strategy_id_on = f"{strategy_prefix}_on"
    strategy_id_wait = f"{strategy_prefix}_wait"

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif args.fno:
        tickers = _load_fno_tickers() or NIFTY50_TICKERS
    elif args.nifty500:
        tickers = _load_nifty500_tickers()
        if not tickers:
            print("NIFTY 500 list missing and auto-download failed.")
            print("Add tickers to `market-context-local-data/nifty500_tickers.txt`.")
            return
    else:
        tickers = _load_fno_tickers() or NIFTY50_TICKERS

    # Default behavior: list all breakout signals for today
    if len(sys.argv) == 1:
        args.all_today = True
        if _load_nifty500_tickers():
            args.nifty500 = True

    today_ist = datetime.now(tz=IST).date()
    signals = []
    history_by_date = {}
    backfill_targets = []
    market_ctx = _build_market_context() if args.filter_mode == "institutional" else {
        "nifty_bias_bullish": None,
        "banknifty_bias_bullish": None,
        "bullish": None,
    }

    debug_counts = {
        "scanned": 0,
        "with_data": 0,
        "today_rows": 0,
        "latest_rvol": 0,
        "latest_vwap": 0,
        "latest_breakout": 0,
        "latest_all": 0,
        "index_aligned": 0,
    }
    for ticker in tickers:
        debug_counts["scanned"] += 1
        df = _fetch_yahoo_5m(ticker)
        if df.empty:
            continue
        debug_counts["with_data"] += 1
        latest_date = df.iloc[-1]["date_ist"]
        if (latest_date != today_ist) and (not args.allow_stale):
            continue
        df = _compute_signals(
            df,
            filter_mode=args.filter_mode,
            market_bullish=market_ctx.get("bullish"),
        )
        if df.empty:
            continue
        debug_counts["today_rows"] += 1
        last = df.iloc[-1]
        if pd.notna(last.get("vol_sma_20")) and last.get("volume") > RVOL_MULT * last.get("vol_sma_20"):
            debug_counts["latest_rvol"] += 1
        if pd.notna(last.get("vwap")) and last.get("close") > last.get("vwap"):
            debug_counts["latest_vwap"] += 1
        if pd.notna(last.get("breakout_high")) and last.get("close") > last.get("breakout_high"):
            debug_counts["latest_breakout"] += 1
        if market_ctx.get("bullish") is not False:
            debug_counts["index_aligned"] += 1
        vol_mult_used = INSTITUTIONAL_RVOL_MULT if args.filter_mode == "institutional" else RVOL_MULT
        all_ok = (
            pd.notna(last.get("vol_sma_20"))
            and last.get("volume") > vol_mult_used * last.get("vol_sma_20")
            and pd.notna(last.get("vwap"))
            and last.get("close") > last.get("vwap")
            and pd.notna(last.get("breakout_high"))
            and last.get("close") > last.get("breakout_high")
        )
        if args.filter_mode == "institutional":
            all_ok = all_ok and bool(last.get("consolidation_ok")) and bool(last.get("candle_quality_ok")) and market_ctx.get("bullish") is not False
        if all_ok:
            debug_counts["latest_all"] += 1
        day_last_close = {}
        for d in df["date_ist"].unique():
            day_last_close[d] = df[df["date_ist"] == d].iloc[-1]["close"]
        # Backfill history (last N days per ticker)
        if args.backfill_days and args.backfill_days > 0:
            uniq_dates = sorted(df["date_ist"].unique())
            target_dates = uniq_dates[-args.backfill_days:]
            backfill_targets = list({*backfill_targets, *target_dates})
            for use_date in target_dates:
                day_df = df[df["date_ist"] == use_date]
                day_df = day_df[day_df["signal"] == True]
                for _, row in day_df.iterrows():
                    after = df[(df["date_ist"] == use_date) & (df["dt_ist"] > row["dt_ist"])]
                    if after.empty:
                        vwap_retouch = "NO"
                        retouch_time = "NA"
                        retouch_close = row["close"]
                        trade_on = "NO"
                        entry_time = "NA"
                        entry_close = row["close"]
                        exit_close = day_last_close.get(use_date) or row["close"]
                        ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                        exit_15m = entry_close
                        ret_15m_pct = 0.0
                    else:
                        touch = after[
                            (after["low"] <= after["vwap"]) & (after["high"] >= after["vwap"])
                        ]
                        if touch.empty:
                            vwap_retouch = "NO"
                            retouch_time = "NA"
                            retouch_close = row["close"]
                            trade_on = "NO"
                            entry_time = "NA"
                            entry_close = row["close"]
                            exit_close = day_last_close.get(use_date) or entry_close
                            ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                            exit_15m = entry_close
                            ret_15m_pct = 0.0
                        else:
                            retouch = touch.iloc[0]
                            vwap_retouch = "YES"
                            retouch_time = retouch["dt_ist"].strftime("%Y-%m-%d %H:%M")
                            retouch_close = retouch["close"]
                            after_touch = after[after["dt_ist"] > retouch["dt_ist"]]
                            trade_candle = after_touch[
                                (after_touch["close"] > after_touch["open"])
                                & (after_touch["close"] > after_touch["vwap"])
                            ]
                            if trade_candle.empty:
                                trade_on = "NO"
                                entry_time = "NA"
                                entry_close = retouch_close
                                exit_close = day_last_close.get(use_date) or entry_close
                                ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                                exit_15m = entry_close
                                ret_15m_pct = 0.0
                            else:
                                trade_on = "YES"
                                entry = trade_candle.iloc[0]
                                entry_time = entry["dt_ist"].strftime("%Y-%m-%d %H:%M")
                                entry_close = entry["close"]
                                exit_close = day_last_close.get(use_date) or entry_close
                                ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                                entry_idx = entry.name
                                future = df.loc[entry_idx + 3] if (entry_idx + 3) in df.index else None
                                exit_15m = future["close"] if future is not None else entry_close
                                ret_15m_pct = ((exit_15m - entry_close) / entry_close * 100) if entry_close else 0.0
                    history_by_date.setdefault(use_date, []).append({
                        "ticker": ticker,
                        "signal_time": row["dt_ist"].strftime("%Y-%m-%d %H:%M"),
                        "retouch_time": retouch_time,
                        "signal_close": row["close"],
                        "retouch_close": retouch_close,
                        "trade_on": trade_on,
                        "entry_time": entry_time,
                        "entry_close": entry_close,
                        "rvol": row["rvol"],
                        "rsi": row.get("rsi"),
                        "vwap": row["vwap"],
                        "vwap_retouch": vwap_retouch,
                        "ret_close_pct": ret_close_pct,
                        "ret_15m_pct": ret_15m_pct,
                        "exit_close": exit_close,
                        "exit_15m": exit_15m,
                        "breakout_high": row.get("breakout_high"),
                        "close_vs_vwap_pct": row.get("close_vs_vwap_pct"),
                        "breakout_gap_pct": row.get("breakout_gap_pct"),
                        "body_ratio": row.get("body_ratio"),
                        "upper_wick_ratio": row.get("upper_wick_ratio"),
                        "consolidation_ok": bool(row.get("consolidation_ok")) if not pd.isna(row.get("consolidation_ok")) else None,
                        "candle_quality_ok": bool(row.get("candle_quality_ok")) if not pd.isna(row.get("candle_quality_ok")) else None,
                        "index_bullish": market_ctx.get("bullish"),
                        "filter_mode": args.filter_mode,
                        "mode": "BREAK",
                        "time": row["dt_ist"].strftime("%Y-%m-%d %H:%M"),
                    })

        if args.all_today:
            if latest_date != today_ist and args.allow_stale:
                use_date = latest_date
            else:
                use_date = today_ist
            day_df = df[df["date_ist"] == use_date]
            day_df = day_df[day_df["signal"] == True]
            for _, row in day_df.iterrows():
                after = df[(df["date_ist"] == use_date) & (df["dt_ist"] > row["dt_ist"])]
                if after.empty:
                    vwap_retouch = "NO"
                    retouch_time = "NA"
                    retouch_close = row["close"]
                    trade_on = "NO"
                    entry_time = "NA"
                    entry_close = row["close"]
                    ret_close_pct = 0.0
                    ret_15m_pct = 0.0
                    exit_close = day_last_close.get(use_date) or row["close"]
                    exit_15m = row["close"]
                else:
                    touch = after[
                        (after["low"] <= after["vwap"]) & (after["high"] >= after["vwap"])
                    ]
                    if touch.empty:
                        vwap_retouch = "NO"
                        retouch_time = "NA"
                        retouch_close = row["close"]
                        trade_on = "NO"
                        entry_time = "NA"
                        entry_close = row["close"]
                        exit_close = day_last_close.get(use_date) or row["close"]
                        exit_15m = row["close"]
                        ret_close_pct = ((exit_close - retouch_close) / retouch_close * 100) if retouch_close else 0.0
                        ret_15m_pct = 0.0
                    else:
                        retouch = touch.iloc[0]
                        vwap_retouch = "YES"
                        retouch_time = retouch["dt_ist"].strftime("%Y-%m-%d %H:%M")
                        retouch_close = retouch["close"]
                        after_touch = after[after["dt_ist"] > retouch["dt_ist"]]
                        trade_candle = after_touch[
                            (after_touch["close"] > after_touch["open"])
                            & (after_touch["close"] > after_touch["vwap"])
                        ]
                        if trade_candle.empty:
                            trade_on = "NO"
                            entry_time = "NA"
                            entry_close = retouch_close
                            exit_close = day_last_close.get(use_date) or entry_close
                            ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                            exit_15m = entry_close
                            ret_15m_pct = 0.0
                        else:
                            trade_on = "YES"
                            entry = trade_candle.iloc[0]
                            entry_time = entry["dt_ist"].strftime("%Y-%m-%d %H:%M")
                            entry_close = entry["close"]
                            exit_close = day_last_close.get(use_date) or entry_close
                            ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                            entry_idx = entry.name
                            future = df.loc[entry_idx + 3] if (entry_idx + 3) in df.index else None
                            exit_15m = future["close"] if future is not None else entry_close
                            ret_15m_pct = ((exit_15m - entry_close) / entry_close * 100) if entry_close else 0.0
                signals.append({
                    "ticker": ticker,
                    "signal_time": row["dt_ist"].strftime("%Y-%m-%d %H:%M"),
                    "retouch_time": retouch_time,
                    "signal_close": row["close"],
                    "retouch_close": retouch_close,
                    "trade_on": trade_on,
                    "entry_time": entry_time,
                    "entry_close": entry_close,
                    "rvol": row["rvol"],
                    "rsi": row.get("rsi"),
                    "vwap": row["vwap"],
                    "vwap_retouch": vwap_retouch,
                    "ret_close_pct": ret_close_pct,
                    "ret_15m_pct": ret_15m_pct,
                    "exit_close": exit_close,
                    "exit_15m": exit_15m,
                    "breakout_high": row.get("breakout_high"),
                    "close_vs_vwap_pct": row.get("close_vs_vwap_pct"),
                    "breakout_gap_pct": row.get("breakout_gap_pct"),
                    "body_ratio": row.get("body_ratio"),
                    "upper_wick_ratio": row.get("upper_wick_ratio"),
                    "consolidation_ok": bool(row.get("consolidation_ok")) if not pd.isna(row.get("consolidation_ok")) else None,
                    "candle_quality_ok": bool(row.get("candle_quality_ok")) if not pd.isna(row.get("candle_quality_ok")) else None,
                    "index_bullish": market_ctx.get("bullish"),
                    "filter_mode": args.filter_mode,
                    "mode": "BREAK",
                    "time": row["dt_ist"].strftime("%Y-%m-%d %H:%M"),
                })
        else:
            latest = df.iloc[-1]
            if pd.isna(latest["signal"]) or not bool(latest["signal"]):
                continue
            use_date = latest["date_ist"]
            after = df[(df["date_ist"] == use_date) & (df["dt_ist"] > latest["dt_ist"])]
            if after.empty:
                vwap_retouch = "NO"
                retouch_time = "NA"
                retouch_close = latest["close"]
                trade_on = "NO"
                entry_time = "NA"
                entry_close = latest["close"]
                exit_close = day_last_close.get(use_date) or latest["close"]
                exit_15m = latest["close"]
                ret_close_pct = ((exit_close - retouch_close) / retouch_close * 100) if retouch_close else 0.0
                ret_15m_pct = 0.0
            else:
                touch = after[
                    (after["low"] <= after["vwap"]) & (after["high"] >= after["vwap"])
                ]
                if touch.empty:
                    vwap_retouch = "NO"
                    retouch_time = "NA"
                    retouch_close = latest["close"]
                    trade_on = "NO"
                    entry_time = "NA"
                    entry_close = latest["close"]
                    exit_close = day_last_close.get(use_date) or latest["close"]
                    exit_15m = latest["close"]
                    ret_close_pct = ((exit_close - retouch_close) / retouch_close * 100) if retouch_close else 0.0
                    ret_15m_pct = 0.0
                else:
                    retouch = touch.iloc[0]
                    vwap_retouch = "YES"
                    retouch_time = retouch["dt_ist"].strftime("%Y-%m-%d %H:%M")
                    retouch_close = retouch["close"]
                    after_touch = after[after["dt_ist"] > retouch["dt_ist"]]
                    trade_candle = after_touch[
                        (after_touch["close"] > after_touch["open"])
                        & (after_touch["close"] > after_touch["vwap"])
                    ]
                    if trade_candle.empty:
                        trade_on = "NO"
                        entry_time = "NA"
                        entry_close = retouch_close
                        exit_close = day_last_close.get(use_date) or entry_close
                        ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                        exit_15m = entry_close
                        ret_15m_pct = 0.0
                    else:
                        trade_on = "YES"
                        entry = trade_candle.iloc[0]
                        entry_time = entry["dt_ist"].strftime("%Y-%m-%d %H:%M")
                        entry_close = entry["close"]
                        exit_close = day_last_close.get(use_date) or entry_close
                        ret_close_pct = ((exit_close - entry_close) / entry_close * 100) if entry_close else 0.0
                        entry_idx = entry.name
                        future = df.loc[entry_idx + 3] if (entry_idx + 3) in df.index else None
                        exit_15m = future["close"] if future is not None else entry_close
                        ret_15m_pct = ((exit_15m - entry_close) / entry_close * 100) if entry_close else 0.0
            signals.append({
                "ticker": ticker,
                "signal_time": latest["dt_ist"].strftime("%Y-%m-%d %H:%M"),
                "retouch_time": retouch_time,
                "signal_close": latest["close"],
                "retouch_close": retouch_close,
                "trade_on": trade_on,
                "entry_time": entry_time,
                "entry_close": entry_close,
                "rvol": latest["rvol"],
                "rsi": latest.get("rsi"),
                "vwap": latest["vwap"],
                "vwap_retouch": vwap_retouch,
                "ret_close_pct": ret_close_pct,
                "ret_15m_pct": ret_15m_pct,
                "exit_close": exit_close,
                "exit_15m": exit_15m,
                "breakout_high": latest.get("breakout_high"),
                "close_vs_vwap_pct": latest.get("close_vs_vwap_pct"),
                "breakout_gap_pct": latest.get("breakout_gap_pct"),
                "body_ratio": latest.get("body_ratio"),
                "upper_wick_ratio": latest.get("upper_wick_ratio"),
                "consolidation_ok": bool(latest.get("consolidation_ok")) if not pd.isna(latest.get("consolidation_ok")) else None,
                "candle_quality_ok": bool(latest.get("candle_quality_ok")) if not pd.isna(latest.get("candle_quality_ok")) else None,
                "index_bullish": market_ctx.get("bullish"),
                "filter_mode": args.filter_mode,
                "mode": "BREAK",
                "time": latest["dt_ist"].strftime("%Y-%m-%d %H:%M"),
            })

    # Build website strategy payloads (trade ON vs waiting)
    trade_on_items = []
    wait_items = []
    for s in signals:
        entry_time = s.get("entry_time") or "NA"
        setup_grade = _grade_signal(s)
        short_reason = (
            f"Grade {setup_grade} | RVOL {s.get('rvol', 0):.2f}x | "
            f"Breakout {s.get('breakout_gap_pct', 0):+.2f}% | Close-VWAP {s.get('close_vs_vwap_pct', 0):+.2f}%"
        )
        if s.get("trade_on") == "YES":
            lines = [
                f"BUY Signal {s['signal_time']} | VWAP touch {s['retouch_time']} | Trade ON {entry_time}",
                f"{short_reason} | Entry {s['entry_close']:.2f} | Close {s['ret_close_pct']:+.2f}% | +15m {s['ret_15m_pct']:+.2f}%"
            ]
            trade_on_items.append({
                "ticker": s["ticker"],
                "name": s["ticker"],
                "side": "BUY",
                "notify_key": f"{s['ticker']}|{s['signal_time']}|ON",
                "signal_time": s.get("signal_time"),
                "entry_time": s.get("entry_time"),
                "entry_price": round(float(s.get("entry_close") or 0), 2),
                "exit_close_pct": round(float(s.get("ret_close_pct") or 0), 2),
                "exit_15m_pct": round(float(s.get("ret_15m_pct") or 0), 2),
                "rvol": round(float(s.get("rvol") or 0), 2),
                "breakout_gap_pct": round(float(s.get("breakout_gap_pct") or 0), 2),
                "close_vs_vwap_pct": round(float(s.get("close_vs_vwap_pct") or 0), 2),
                "setup_grade": setup_grade,
                "filter_mode": s.get("filter_mode", args.filter_mode),
                "lines": lines,
            })
        else:
            lines = [
                f"BUY Signal {s['signal_time']} | VWAP touch {s['retouch_time']} | Trade ON {entry_time}",
                f"{short_reason} | Waiting for green 5m close above VWAP.",
            ]
            wait_items.append({
                "ticker": s["ticker"],
                "name": s["ticker"],
                "side": "BUY",
                "notify_key": f"{s['ticker']}|{s['signal_time']}|WAIT",
                "signal_time": s.get("signal_time"),
                "rvol": round(float(s.get("rvol") or 0), 2),
                "breakout_gap_pct": round(float(s.get("breakout_gap_pct") or 0), 2),
                "close_vs_vwap_pct": round(float(s.get("close_vs_vwap_pct") or 0), 2),
                "setup_grade": setup_grade,
                "filter_mode": s.get("filter_mode", args.filter_mode),
                "lines": lines,
            })

    if not args.no_write and STRATEGY_OUT_DIR.exists():
        now_utc = datetime.now(tz=timezone.utc)
        now = now_utc.isoformat()
        day_key = now_utc.astimezone(IST).strftime("%Y%m%d")
        runtime_sec = round(time.time() - start_ts, 2)
        trade_on_payload = {
            "strategy_id": strategy_id_on,
            "title": f"{args.title_prefix} — Trade ON (VWAP + Green Close)",
            "owner": args.owner,
            "trade_type": "INTRADAY",
            "market": args.market,
            "filter_mode": args.filter_mode,
            "rules": {
                "breakout_condition": "close above previous 6-candle high",
                "entry_window_candles": "same-day intraday",
                "entry_trigger": "VWAP retouch + green close above VWAP",
                "volume_multiple": INSTITUTIONAL_RVOL_MULT if args.filter_mode == "institutional" else RVOL_MULT,
                "lookback_volume": RVOL_WINDOW,
                "filter_mode": args.filter_mode,
            },
            "market_context": market_ctx,
            "generated_at": now,
            "runtime_sec": runtime_sec,
            "notes": [
                "Entry only after VWAP retouch and a green 5m close above VWAP.",
                "Signal precondition: breakout above previous 6 candles + volume > 2x (20-bar avg) + close > VWAP.",
                (
                    "Institutional mode: adds consolidation, candle quality, and Nifty/BankNifty bullish alignment."
                    if args.filter_mode == "institutional"
                    else "Basic mode: breakout + volume + VWAP only."
                ),
                "Exit shown at day close and +15m from entry.",
            ],
            "items": trade_on_items,
        }
        wait_payload = {
            "strategy_id": strategy_id_wait,
            "title": f"{args.title_prefix} — Signal Waiting (VWAP not confirmed)",
            "owner": args.owner,
            "trade_type": "INTRADAY",
            "market": args.market,
            "filter_mode": args.filter_mode,
            "rules": {
                "breakout_condition": "close above previous 6-candle high",
                "entry_window_candles": "same-day intraday",
                "entry_trigger": "waiting for VWAP retouch + green close above VWAP",
                "volume_multiple": INSTITUTIONAL_RVOL_MULT if args.filter_mode == "institutional" else RVOL_MULT,
                "lookback_volume": RVOL_WINDOW,
                "filter_mode": args.filter_mode,
            },
            "market_context": market_ctx,
            "generated_at": now,
            "runtime_sec": runtime_sec,
            "notes": [
                "Signal fired but trade is OFF until VWAP retouch + green close above VWAP.",
                "Signal precondition: breakout above previous 6 candles + volume > 2x (20-bar avg) + close > VWAP.",
            ],
            "items": wait_items,
        }
        (STRATEGY_OUT_DIR / f"{strategy_id_on}.json").write_text(
            json.dumps(trade_on_payload, indent=2)
        )
        (STRATEGY_OUT_DIR / f"{strategy_id_wait}.json").write_text(
            json.dumps(wait_payload, indent=2)
        )
        STRATEGY_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        (STRATEGY_HISTORY_DIR / f"{strategy_id_on}_{day_key}.json").write_text(
            json.dumps(trade_on_payload, indent=2)
        )
        (STRATEGY_HISTORY_DIR / f"{strategy_id_wait}_{day_key}.json").write_text(
            json.dumps(wait_payload, indent=2)
        )
        # Backfill history files for last N days
        if args.backfill_days:
            # ensure last N trading weekdays exist even if no data
            if not backfill_targets:
                backfill_targets = []
            if args.backfill_days > 0:
                d = today_ist
                while len(backfill_targets) < args.backfill_days:
                    if d.weekday() < 5 and d not in backfill_targets:
                        backfill_targets.append(d)
                    d = d - timedelta(days=1)
            for d in sorted(backfill_targets)[-max(1, args.backfill_days):]:
                dkey = d.strftime("%Y%m%d")
                day_items = history_by_date.get(d, [])
                trade_on_items_day = []
                wait_items_day = []
                for s in day_items:
                    entry_time = s.get("entry_time") or "NA"
                    if s.get("trade_on") == "YES":
                        lines = [
                            f"Signal {s['signal_time']} | VWAP touch {s['retouch_time']} | Trade ON {entry_time} | "
                            f"Entry {s['entry_close']:.2f} | Exit@Close {s['exit_close']:.2f} ({s['ret_close_pct']:+.2f}%) | "
                            f"Exit@+15m {s['exit_15m']:.2f} ({s['ret_15m_pct']:+.2f}%)"
                        ]
                        trade_on_items_day.append({
                            "ticker": s["ticker"],
                            "name": s["ticker"],
                            "notify_key": f"{s['ticker']}|{s['signal_time']}|ON",
                            "lines": lines
                        })
                    else:
                        lines = [
                            f"Signal {s['signal_time']} | VWAP touch {s['retouch_time']} | Trade ON {entry_time} | "
                            "Waiting for green 5m close above VWAP.",
                        ]
                        wait_items_day.append({
                            "ticker": s["ticker"],
                            "name": s["ticker"],
                            "notify_key": f"{s['ticker']}|{s['signal_time']}|WAIT",
                            "lines": lines
                        })
                day_on = dict(trade_on_payload)
                day_wait = dict(wait_payload)
                day_on["generated_at"] = d.isoformat()
                day_wait["generated_at"] = d.isoformat()
                day_on["items"] = trade_on_items_day
                day_wait["items"] = wait_items_day
                (STRATEGY_HISTORY_DIR / f"{strategy_id_on}_{dkey}.json").write_text(
                    json.dumps(day_on, indent=2)
                )
                (STRATEGY_HISTORY_DIR / f"{strategy_id_wait}_{dkey}.json").write_text(
                    json.dumps(day_wait, indent=2)
                )

    if not signals:
        print("No momentum breakout signals right now.")
        if args.debug:
            print(
                f"Debug mode={args.filter_mode} | index_bullish={market_ctx.get('bullish')} "
                f"(nifty={market_ctx.get('nifty_bias_bullish')}, banknifty={market_ctx.get('banknifty_bias_bullish')}) | "
                f"Debug: scanned {debug_counts['scanned']} | data {debug_counts['with_data']} | "
                f"today_rows {debug_counts['today_rows']} | latest_rvol {debug_counts['latest_rvol']} | "
                f"latest_vwap {debug_counts['latest_vwap']} | latest_breakout {debug_counts['latest_breakout']} | "
                f"latest_all {debug_counts['latest_all']}"
            )
        return

    if args.only_retouch:
        signals = [s for s in signals if s.get("vwap_retouch") == "YES"]
        if not signals:
            print("No momentum breakout retouch signals right now.")
            return
    if args.min_close_ret is not None:
        signals = [s for s in signals if s.get("ret_close_pct", 0.0) >= args.min_close_ret]
        if not signals:
            print("No momentum breakout signals meeting min close return.")
            return


    def _fmt_num(val, width, decimals=2, signed=False, suffix=""):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return "NA".ljust(width)
        fmt = f"{{:{'+' if signed else ''}.{decimals}f}}"
        out = fmt.format(val) + suffix
        return out.ljust(width)

    def _fmt_txt(val, width):
        return (val if val is not None else "NA").ljust(width)

    header = (
        "Ticker       Grade Signal Time       Entry Time        RetClose% Ret15m%  RVOL  "
        "Brk%   VwapGap% Touch  ON"
    )
    print(header)
    print("-" * len(header))
    for s in sorted(signals, key=lambda x: (x["time"], x["ticker"])):
        grade = _grade_signal(s)
        line = (
            f"{_fmt_txt(s['ticker'],12)} "
            f"{_fmt_txt(grade,5)} "
            f"{_fmt_txt(s['signal_time'],17)}"
            f"{_fmt_txt(s['entry_time'],18)}"
            f"{_fmt_num(s.get('ret_close_pct'),9,2,True,'%')}"
            f"{_fmt_num(s.get('ret_15m_pct'),9,2,True,'%')}"
            f"{_fmt_num(s.get('rvol'),6)}"
            f"{_fmt_num(s.get('breakout_gap_pct'),7,2,True,'%')}"
            f"{_fmt_num(s.get('close_vs_vwap_pct'),9,2,True,'%')}"
            f"{_fmt_txt(s.get('vwap_retouch'),6)}"
            f"{_fmt_txt(s.get('trade_on'),3)}"
        )
        print(line)


if __name__ == "__main__":
    run()
