#!/usr/bin/env python3
import argparse
import json
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_fetcher import GLOBAL_STOCKS


DB_PATH = ROOT / "market.db"
REPORT_DIR = ROOT / "backend" / "reports"

EXCLUDE_SYMBOLS = {
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "INDIA_VIX",
    "SP500",
    "NASDAQ",
    "DAX",
    "NIKKEI",
    "HANGSENG",
    "GOLD",
    "SILVER",
    "CRUDEOIL",
    "BRENT",
    "NATGAS",
    "COPPER",
    "PLATINUM",
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "BITCOIN",
    "ETHEREUM",
    "SOLANA",
    "TMPV",
}

DAILY_BATCH = 20


def _utc_today():
    return datetime.now(timezone.utc).date()


def _load_equity_universe():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("SELECT DISTINCT index_name FROM prices ORDER BY index_name").fetchall()
    finally:
        conn.close()
    raw = [str(r[0]).strip().upper() for r in rows if r and r[0]]
    equities = [s for s in raw if s and s not in EXCLUDE_SYMBOLS]
    out = []
    global_set = set(GLOBAL_STOCKS.keys())
    for symbol in equities:
        if symbol in global_set:
            yf_symbol = GLOBAL_STOCKS[symbol]
            market = "global"
        else:
            yf_symbol = f"{symbol}.NS"
            market = "india"
        out.append({"symbol": symbol, "yf_symbol": yf_symbol, "market": market})
    return out


def _normalize_download(df, ticker):
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        if ticker in out.columns.get_level_values(0):
            out = out[ticker].copy()
        elif ticker in out.columns.get_level_values(-1):
            out.columns = out.columns.droplevel(-1)
        else:
            try:
                out = out.xs(ticker, axis=1, level=0)
            except Exception:
                return pd.DataFrame()
    rename_map = {}
    for col in out.columns:
        txt = str(col)
        if txt.lower().startswith("adj close"):
            continue
        rename_map[col] = txt.title()
    out = out.rename(columns=rename_map)
    needed = ["Open", "High", "Low", "Close", "Volume"]
    keep = [c for c in needed if c in out.columns]
    if "Close" not in keep:
        return pd.DataFrame()
    out = out[keep].copy()
    out = out.dropna(subset=["Close"])
    out.index = pd.to_datetime(out.index, utc=True)
    if "Volume" not in out.columns:
        out["Volume"] = np.nan
    return out


def _download_daily_recent(universe):
    frames = {}
    for start in range(0, len(universe), DAILY_BATCH):
        batch = universe[start:start + DAILY_BATCH]
        tickers = [item["yf_symbol"] for item in batch]
        raw = yf.download(
            tickers=tickers,
            period="90d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            group_by="ticker",
        )
        for item in batch:
            frames[item["symbol"]] = _normalize_download(raw, item["yf_symbol"])
    return frames


def _download_history(yf_symbol, period, interval):
    df = yf.download(
        tickers=yf_symbol,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
        group_by="ticker",
    )
    return _normalize_download(df, yf_symbol)


def _rsi(series, window):
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss.notna(), np.nan)
    out = out.mask((avg_loss == 0) & avg_gain.notna(), 100.0)
    return out


def _atr(df, window):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _adx(df, window=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr = _atr(df, window)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr.replace(0.0, np.nan)
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)) * 100.0
    return dx.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _stochastic(df, k_window=14, d_window=3):
    lowest_low = df["Low"].rolling(k_window, min_periods=k_window).min()
    highest_high = df["High"].rolling(k_window, min_periods=k_window).max()
    denom = (highest_high - lowest_low).replace(0.0, np.nan)
    k = ((df["Close"] - lowest_low) / denom) * 100.0
    d = k.rolling(d_window, min_periods=d_window).mean()
    return k, d


def _bollinger_pos(series, window=20, num_std=2.0):
    sma = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    upper = sma + (num_std * std)
    lower = sma - (num_std * std)
    denom = (upper - lower).replace(0.0, np.nan)
    return (series - lower) / denom


def _compute_indicators(df):
    if df is None or df.empty or len(df) < 20:
        return {}
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

    last = frame.index[-1]
    out = {
        "bar_time": last.isoformat(),
        "close": _safe(close.iloc[-1]),
        "ema9": _safe(ema9.iloc[-1]),
        "ema21": _safe(ema21.iloc[-1]),
        "sma50": _safe(sma50.iloc[-1]),
        "sma200": _safe(sma200.iloc[-1]),
        "close_vs_ema9_pct": _pct_diff(close.iloc[-1], ema9.iloc[-1]),
        "close_vs_ema21_pct": _pct_diff(close.iloc[-1], ema21.iloc[-1]),
        "close_vs_sma50_pct": _pct_diff(close.iloc[-1], sma50.iloc[-1]),
        "close_vs_sma200_pct": _pct_diff(close.iloc[-1], sma200.iloc[-1]),
        "rsi8": _safe(rsi8.iloc[-1]),
        "rsi14": _safe(rsi14.iloc[-1]),
        "macd_line": _safe(macd_line.iloc[-1]),
        "macd_signal": _safe(macd_signal.iloc[-1]),
        "macd_hist": _safe(macd_hist.iloc[-1]),
        "atr_pct": _safe(atr_pct.iloc[-1]),
        "adx14": _safe(adx14.iloc[-1]),
        "stoch_k": _safe(stoch_k.iloc[-1]),
        "stoch_d": _safe(stoch_d.iloc[-1]),
        "bb_pos": _safe(bb_pos.iloc[-1]),
        "vol_mult20": _safe(vol_mult20.iloc[-1]),
        "above_ema9": _bool(close.iloc[-1] > ema9.iloc[-1]) if pd.notna(ema9.iloc[-1]) else None,
        "above_ema21": _bool(close.iloc[-1] > ema21.iloc[-1]) if pd.notna(ema21.iloc[-1]) else None,
        "above_sma50": _bool(close.iloc[-1] > sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else None,
        "above_sma200": _bool(close.iloc[-1] > sma200.iloc[-1]) if pd.notna(sma200.iloc[-1]) else None,
        "ema9_gt_ema21": _bool(ema9.iloc[-1] > ema21.iloc[-1]) if pd.notna(ema9.iloc[-1]) and pd.notna(ema21.iloc[-1]) else None,
        "ema21_gt_sma50": _bool(ema21.iloc[-1] > sma50.iloc[-1]) if pd.notna(ema21.iloc[-1]) and pd.notna(sma50.iloc[-1]) else None,
        "macd_hist_pos": _bool(macd_hist.iloc[-1] > 0) if pd.notna(macd_hist.iloc[-1]) else None,
        "rsi14_gt_60": _bool(rsi14.iloc[-1] > 60) if pd.notna(rsi14.iloc[-1]) else None,
        "adx14_gt_20": _bool(adx14.iloc[-1] > 20) if pd.notna(adx14.iloc[-1]) else None,
        "bb_pos_gt_0_5": _bool(bb_pos.iloc[-1] > 0.5) if pd.notna(bb_pos.iloc[-1]) else None,
    }
    return out


def _safe(value):
    try:
        value = float(value)
    except Exception:
        return None
    if math.isfinite(value):
        return round(value, 4)
    return None


def _bool(value):
    return bool(value)


def _pct_diff(a, b):
    try:
        a = float(a)
        b = float(b)
        if not math.isfinite(a) or not math.isfinite(b) or b == 0.0:
            return None
        return round(((a / b) - 1.0) * 100.0, 4)
    except Exception:
        return None


def _resample_ohlcv(df, rule):
    if df is None or df.empty:
        return pd.DataFrame()
    out = (
        df.resample(rule, label="right", closed="right")
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna(subset=["Open", "High", "Low", "Close"])
    )
    return out


def _screen_30d_gainers(recent_frames, min_gain, max_gain, as_of):
    winners = []
    start_target = as_of - timedelta(days=30)
    for symbol, df in recent_frames.items():
        if df is None or df.empty:
            continue
        frame = df[df.index.date <= as_of].copy()
        if frame.empty:
            continue
        latest_ts = frame.index[-1]
        start_slice = frame[frame.index.date <= start_target]
        if start_slice.empty:
            continue
        start_ts = start_slice.index[-1]
        start_close = float(frame.loc[start_ts, "Close"])
        end_close = float(frame.loc[latest_ts, "Close"])
        if start_close <= 0:
            continue
        ret = ((end_close / start_close) - 1.0) * 100.0
        if min_gain <= ret <= max_gain:
            winners.append(
                {
                    "symbol": symbol,
                    "yf_symbol": None,
                    "start_date": start_ts.date().isoformat(),
                    "end_date": latest_ts.date().isoformat(),
                    "start_close": round(start_close, 4),
                    "end_close": round(end_close, 4),
                    "return_pct": round(ret, 2),
                }
            )
    winners.sort(key=lambda x: (x["return_pct"], x["symbol"]))
    return winners


def _numeric_summary(indicator_rows):
    numeric_fields = []
    if not indicator_rows:
        return pd.DataFrame(), pd.DataFrame()
    sample = indicator_rows[0]
    for key, value in sample.items():
        if key in {"symbol", "market", "return_pct", "timeframe", "bar_time"}:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric_fields.append(key)

    summary_rows = []
    tight_rows = []
    for timeframe in sorted({r["timeframe"] for r in indicator_rows}):
        tf_rows = [r for r in indicator_rows if r["timeframe"] == timeframe]
        for field in numeric_fields:
            values = [float(r[field]) for r in tf_rows if r.get(field) is not None]
            if not values:
                continue
            arr = np.array(values, dtype=float)
            q25 = float(np.nanpercentile(arr, 25))
            med = float(np.nanmedian(arr))
            q75 = float(np.nanpercentile(arr, 75))
            iqr = q75 - q25
            rel_iqr = None
            if med not in (0.0, -0.0):
                rel_iqr = abs(iqr / med)
            row = {
                "timeframe": timeframe,
                "indicator": field,
                "count": int(arr.size),
                "min": round(float(np.nanmin(arr)), 4),
                "q25": round(q25, 4),
                "median": round(med, 4),
                "q75": round(q75, 4),
                "max": round(float(np.nanmax(arr)), 4),
                "mean": round(float(np.nanmean(arr)), 4),
                "iqr": round(iqr, 4),
                "relative_iqr": round(rel_iqr, 4) if rel_iqr is not None and math.isfinite(rel_iqr) else None,
            }
            summary_rows.append(row)
            tight_rows.append(row)
    summary_df = pd.DataFrame(summary_rows)
    tight_df = pd.DataFrame(tight_rows)
    if not tight_df.empty:
        tight_df = tight_df.sort_values(["timeframe", "relative_iqr", "iqr", "indicator"], na_position="last")
    return summary_df, tight_df


def _state_summary(indicator_rows):
    if not indicator_rows:
        return pd.DataFrame()
    bool_fields = []
    sample = indicator_rows[0]
    for key, value in sample.items():
        if isinstance(value, bool):
            bool_fields.append(key)
    rows = []
    for timeframe in sorted({r["timeframe"] for r in indicator_rows}):
        tf_rows = [r for r in indicator_rows if r["timeframe"] == timeframe]
        for field in bool_fields:
            values = [r[field] for r in tf_rows if r.get(field) is not None]
            if not values:
                continue
            true_count = sum(1 for x in values if x)
            rows.append(
                {
                    "timeframe": timeframe,
                    "state": field,
                    "count": len(values),
                    "true_count": true_count,
                    "true_ratio": round(true_count / len(values), 4),
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["timeframe", "true_ratio", "state"], ascending=[True, False, True])
    return df


def main():
    parser = argparse.ArgumentParser(description="Study 30-day 10-15% equity gainers across multi-timeframe indicators")
    parser.add_argument("--as-of-date", default="", help="As-of date YYYY-MM-DD. Default: today UTC.")
    parser.add_argument("--min-gain-pct", type=float, default=10.0, help="Minimum 30-day gain percentage")
    parser.add_argument("--max-gain-pct", type=float, default=15.0, help="Maximum 30-day gain percentage")
    parser.add_argument("--limit", type=int, default=0, help="Optional max winners to analyze after screening")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else _utc_today()
    universe = _load_equity_universe()
    recent_frames = _download_daily_recent(universe)
    winners = _screen_30d_gainers(
        recent_frames=recent_frames,
        min_gain=float(args.min_gain_pct),
        max_gain=float(args.max_gain_pct),
        as_of=as_of,
    )

    yf_map = {item["symbol"]: item["yf_symbol"] for item in universe}
    market_map = {item["symbol"]: item["market"] for item in universe}
    for row in winners:
        row["yf_symbol"] = yf_map.get(row["symbol"])
        row["market"] = market_map.get(row["symbol"])

    if args.limit and args.limit > 0:
        winners = winners[: int(args.limit)]

    per_stock_rows = []
    skipped = []
    timeframe_rules = {
        "monthly": ("1d", "15y", "ME"),
        "weekly": ("1d", "15y", "W-FRI"),
        "daily": ("1d", "3y", None),
        "4h": ("60m", "730d", "4H"),
        "3h": ("60m", "730d", "3H"),
    }

    for winner in winners:
        symbol = winner["symbol"]
        yf_symbol = winner["yf_symbol"]
        try:
            daily_long = _download_history(yf_symbol, period="15y", interval="1d")
            hourly = _download_history(yf_symbol, period="730d", interval="60m")
        except Exception as exc:
            skipped.append({"symbol": symbol, "yf_symbol": yf_symbol, "reason": repr(exc)})
            continue

        tf_frames = {
            "monthly": _resample_ohlcv(daily_long, "ME"),
            "weekly": _resample_ohlcv(daily_long, "W-FRI"),
            "daily": daily_long.copy(),
            "4h": _resample_ohlcv(hourly, "4h"),
            "3h": _resample_ohlcv(hourly, "3h"),
        }

        for timeframe, tf_df in tf_frames.items():
            indicators = _compute_indicators(tf_df)
            if not indicators:
                continue
            row = {
                "symbol": symbol,
                "market": winner["market"],
                "return_pct": winner["return_pct"],
                "timeframe": timeframe,
            }
            row.update(indicators)
            per_stock_rows.append(row)

    winners_df = pd.DataFrame(winners)
    indicators_df = pd.DataFrame(per_stock_rows)
    common_ranges_df, tight_df = _numeric_summary(per_stock_rows)
    states_df = _state_summary(per_stock_rows)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = as_of.strftime("%Y%m%d")
    winners_path = REPORT_DIR / f"equity_30d_gainers_10_15pct_{stamp}.csv"
    indicators_path = REPORT_DIR / f"equity_30d_gainers_indicator_matrix_{stamp}.csv"
    ranges_path = REPORT_DIR / f"equity_30d_gainers_common_ranges_{stamp}.csv"
    tight_path = REPORT_DIR / f"equity_30d_gainers_tight_indicators_{stamp}.csv"
    states_path = REPORT_DIR / f"equity_30d_gainers_state_consensus_{stamp}.csv"
    json_path = REPORT_DIR / f"equity_30d_gainers_study_{stamp}.json"

    winners_df.to_csv(winners_path, index=False)
    indicators_df.to_csv(indicators_path, index=False)
    common_ranges_df.to_csv(ranges_path, index=False)
    tight_df.to_csv(tight_path, index=False)
    states_df.to_csv(states_path, index=False)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "screen": {
            "as_of_date": as_of.isoformat(),
            "lookback_days": 30,
            "start_target_date": (as_of - timedelta(days=30)).isoformat(),
            "min_gain_pct": float(args.min_gain_pct),
            "max_gain_pct": float(args.max_gain_pct),
            "universe_size": len(universe),
            "winner_count": len(winners),
        },
        "files": {
            "winners_csv": str(winners_path),
            "indicator_matrix_csv": str(indicators_path),
            "common_ranges_csv": str(ranges_path),
            "tight_indicators_csv": str(tight_path),
            "state_consensus_csv": str(states_path),
        },
        "winners": winners,
        "skipped": skipped,
        "top_tight_indicators": tight_df.groupby("timeframe").head(12).to_dict(orient="records") if not tight_df.empty else [],
        "top_state_consensus": states_df.groupby("timeframe").head(12).to_dict(orient="records") if not states_df.empty else [],
    }
    json_path.write_text(json.dumps(report, indent=2))

    print(f"AS_OF_DATE={as_of.isoformat()}")
    print(f"START_TARGET_DATE={(as_of - timedelta(days=30)).isoformat()}")
    print(f"UNIVERSE_SIZE={len(universe)}")
    print(f"WINNER_COUNT={len(winners)}")
    print(f"WINNERS_CSV={winners_path}")
    print(f"INDICATORS_CSV={indicators_path}")
    print(f"RANGES_CSV={ranges_path}")
    print(f"TIGHT_CSV={tight_path}")
    print(f"STATES_CSV={states_path}")
    print(f"JSON_REPORT={json_path}")
    if not winners_df.empty:
        cols = ["symbol", "market", "start_date", "end_date", "start_close", "end_close", "return_pct"]
        print(winners_df[cols].to_string(index=False))
    else:
        print("NO_WINNERS_FOUND")


if __name__ == "__main__":
    main()
