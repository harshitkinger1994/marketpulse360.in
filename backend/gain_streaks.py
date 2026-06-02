#!/usr/bin/env python3
import argparse
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "market.db"
REPORT_DIR = ROOT / "backend" / "reports"


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _load_daily(symbol: str, start: date | None, end: date) -> pd.DataFrame:
    symbol = str(symbol).strip().upper()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if start:
            df = pd.read_sql_query(
                """
                SELECT date, open, high, low, close, volume
                FROM prices
                WHERE index_name = ? AND date >= ? AND date <= ?
                ORDER BY date
                """,
                conn,
                params=[symbol, start.isoformat(), end.isoformat()],
            )
        else:
            df = pd.read_sql_query(
                """
                SELECT date, open, high, low, close, volume
                FROM prices
                WHERE index_name = ? AND date <= ?
                ORDER BY date
                """,
                conn,
                params=[symbol, end.isoformat()],
            )
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.set_index("date").sort_index()
    return df


def _daily_to_weekly_ohlcv(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    tmp = daily.copy()
    tmp["week"] = tmp.index.to_period("W-SUN")
    out = (
        tmp.groupby("week")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            close_date=("close", lambda s: s.index[-1]),
        )
        .reset_index()
    )
    out["close_date"] = pd.to_datetime(out["close_date"], errors="coerce")
    out = out.dropna(subset=["close_date", "open", "high", "low", "close"])
    out = out.sort_values("week").set_index("week")
    return out


def _daily_to_monthly_ohlcv(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    tmp = daily.copy()
    tmp["month"] = tmp.index.to_period("M")
    out = (
        tmp.groupby("month")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            close_date=("close", lambda s: s.index[-1]),
        )
        .reset_index()
    )
    out["close_date"] = pd.to_datetime(out["close_date"], errors="coerce")
    out = out.dropna(subset=["close_date", "open", "high", "low", "close"])
    out = out.sort_values("month").set_index("month")
    return out


def _add_returns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    out["prev_close"] = out["close"].shift(1)
    out["return_pct"] = (out["close"] / out["prev_close"] - 1.0) * 100.0
    out["return_pct"] = out["return_pct"].replace([np.inf, -np.inf], np.nan)
    return out


def _find_gain_runs(df: pd.DataFrame, time_col: str, ret_col: str) -> pd.DataFrame:
    empty = pd.DataFrame(
        columns=[
            "run",
            "start",
            "end",
            "bars",
            "avg_gain_pct",
            "min_gain_pct",
            "max_gain_pct",
            "total_gain_pct",
        ]
    )
    if df.empty:
        return empty
    tmp = df.copy()
    tmp = tmp.dropna(subset=[time_col, ret_col]).copy()
    if tmp.empty:
        return empty

    tmp["is_gain"] = tmp[ret_col] > 0.0
    # Run id increments when state changes.
    tmp["_run_id"] = (tmp["is_gain"] != tmp["is_gain"].shift(1)).cumsum()
    gains = tmp[tmp["is_gain"]].copy()
    if gains.empty:
        return empty

    def _compound_gain(series: pd.Series) -> float:
        vals = pd.to_numeric(series, errors="coerce").dropna().astype(float) / 100.0
        if vals.empty:
            return np.nan
        return (float(np.prod(1.0 + vals)) - 1.0) * 100.0

    grouped = gains.groupby("_run_id")
    out = (
        grouped.agg(
            start=(time_col, "min"),
            end=(time_col, "max"),
            bars=(ret_col, "count"),
            avg_gain_pct=(ret_col, "mean"),
            min_gain_pct=(ret_col, "min"),
            max_gain_pct=(ret_col, "max"),
        )
        .reset_index(drop=True)
    )
    out["total_gain_pct"] = grouped[ret_col].apply(_compound_gain).values
    out = out.sort_values(["start"]).reset_index(drop=True)
    out.insert(0, "run", np.arange(1, len(out) + 1, dtype=int))
    out["avg_gain_pct"] = out["avg_gain_pct"].round(6)
    out["min_gain_pct"] = out["min_gain_pct"].round(6)
    out["max_gain_pct"] = out["max_gain_pct"].round(6)
    out["total_gain_pct"] = out["total_gain_pct"].round(6)
    return out


def _to_ymd(series: pd.Series) -> pd.Series:
    s = pd.to_datetime(series, errors="coerce")
    return s.dt.date.astype(str)


def main() -> int:
    parser = argparse.ArgumentParser(description="Find gain streaks (consecutive positive returns) by timeframe.")
    parser.add_argument("--symbol", required=True, help="Symbol as stored in market.db (e.g., ADBE).")
    parser.add_argument("--as-of", default=None, help="As-of date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--last-months", type=int, default=2, help="Lookback window in months (approx 30d each).")
    parser.add_argument("--output-prefix", default=None, help="Output prefix (defaults to reports folder with symbol+date).")
    args = parser.parse_args()

    symbol = str(args.symbol).strip().upper()
    as_of = _parse_ymd(args.as_of) if args.as_of else date.today()
    lookback_days = max(1, int(args.last_months) * 30)
    start = as_of - timedelta(days=lookback_days)

    daily = _load_daily(symbol, start, as_of)
    if daily.empty:
        raise SystemExit(f"No data found in market.db for symbol={symbol} between {start.isoformat()} and {as_of.isoformat()}")

    daily_bars = daily.reset_index().rename(columns={"date": "day_date"})
    daily_bars["day_date"] = pd.to_datetime(daily_bars["day_date"], errors="coerce")
    daily_bars = _add_returns(daily_bars)

    weekly = _daily_to_weekly_ohlcv(daily)
    weekly_bars = weekly.reset_index()
    weekly_bars["week_end"] = weekly_bars["close_date"]
    weekly_bars = _add_returns(weekly_bars)

    monthly = _daily_to_monthly_ohlcv(daily)
    monthly_bars = monthly.reset_index()
    monthly_bars["month_end"] = monthly_bars["close_date"]
    monthly_bars = _add_returns(monthly_bars)

    daily_runs = _find_gain_runs(daily_bars, "day_date", "return_pct")
    weekly_runs = _find_gain_runs(weekly_bars, "week_end", "return_pct")
    monthly_runs = _find_gain_runs(monthly_bars, "month_end", "return_pct")

    def _flag_best_total(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or "total_gain_pct" not in df.columns:
            return df
        mx = pd.to_numeric(df["total_gain_pct"], errors="coerce").max()
        if pd.isna(mx):
            df["is_best_total_gain"] = False
            return df
        df["is_best_total_gain"] = pd.to_numeric(df["total_gain_pct"], errors="coerce") == mx
        return df

    daily_runs = _flag_best_total(daily_runs)
    weekly_runs = _flag_best_total(weekly_runs)
    monthly_runs = _flag_best_total(monthly_runs)

    if not daily_runs.empty:
        daily_runs["start"] = _to_ymd(daily_runs["start"])
        daily_runs["end"] = _to_ymd(daily_runs["end"])
    if not weekly_runs.empty:
        weekly_runs["start"] = _to_ymd(weekly_runs["start"])
        weekly_runs["end"] = _to_ymd(weekly_runs["end"])
    if not monthly_runs.empty:
        monthly_runs["start"] = _to_ymd(monthly_runs["start"])
        monthly_runs["end"] = _to_ymd(monthly_runs["end"])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = Path(args.output_prefix) if args.output_prefix else (REPORT_DIR / f"{symbol}_gain_streaks_{as_of.strftime('%Y%m%d')}_last{int(args.last_months)}m")

    daily_path = Path(str(base) + "_daily.csv")
    weekly_path = Path(str(base) + "_weekly.csv")
    monthly_path = Path(str(base) + "_monthly.csv")

    daily_runs.to_csv(daily_path, index=False)
    weekly_runs.to_csv(weekly_path, index=False)
    monthly_runs.to_csv(monthly_path, index=False)

    print(str(daily_path))
    print(str(weekly_path))
    print(str(monthly_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
