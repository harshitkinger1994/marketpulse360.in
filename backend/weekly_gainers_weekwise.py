#!/usr/bin/env python3
import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import sys


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "market.db"
REPORT_DIR = ROOT / "backend" / "reports"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_fetcher import GLOBAL_STOCKS  # noqa: E402

EXCLUDE_SYMBOLS = {
    # Indices / volatility
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "INDIA_VIX",
    "SP500",
    "NASDAQ",
    "DAX",
    "NIKKEI",
    "HANGSENG",
    # Commodities / crypto / meta
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


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _load_prices(start: date, end: date) -> pd.DataFrame:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        df = pd.read_sql_query(
            """
            SELECT index_name AS symbol, date, close
            FROM prices
            WHERE date >= ? AND date <= ?
            """,
            conn,
            params=[start.isoformat(), end.isoformat()],
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df = df[~df["symbol"].isin(EXCLUDE_SYMBOLS)].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"])
    df = df.sort_values(["symbol", "date"])
    return df


def _weekly_gainers(df: pd.DataFrame, weeks_start: date, weeks_end: date, top_n: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    # Weekly buckets ending on Sunday (ISO week), then use the last available close in that bucket.
    df = df.copy()
    df["week"] = df["date"].dt.to_period("W-SUN")
    weekly = (
        df.sort_values(["symbol", "date"])
        .groupby(["symbol", "week"], as_index=False)
        .agg(close=("close", "last"), close_date=("date", "last"))
    )
    weekly = weekly.sort_values(["symbol", "week"])
    weekly["prev_close"] = weekly.groupby("symbol")["close"].shift(1)
    weekly["prev_close_date"] = weekly.groupby("symbol")["close_date"].shift(1)
    weekly["weekly_gain_pct"] = (weekly["close"] / weekly["prev_close"] - 1.0) * 100.0

    weekly = weekly.dropna(subset=["weekly_gain_pct", "prev_close", "prev_close_date"]).copy()
    weekly = weekly[weekly["weekly_gain_pct"] > 0.0].copy()
    weekly["week_start"] = weekly["week"].dt.start_time.dt.date
    weekly["week_end"] = weekly["week"].dt.end_time.dt.date

    weekly = weekly[(weekly["week_end"] >= weeks_start) & (weekly["week_end"] <= weeks_end)].copy()
    if weekly.empty:
        return pd.DataFrame()

    weekly = weekly.sort_values(["week_end", "weekly_gain_pct"], ascending=[True, False])

    if top_n and top_n > 0:
        weekly["rank"] = weekly.groupby("week")["weekly_gain_pct"].rank(method="first", ascending=False)
        weekly = weekly[weekly["rank"] <= float(top_n)].copy()
        weekly = weekly.sort_values(["week_end", "rank", "symbol"])
        weekly["rank"] = weekly["rank"].astype(int)
    else:
        weekly = weekly.sort_values(["week_end", "weekly_gain_pct"], ascending=[True, False])
        weekly["rank"] = weekly.groupby("week")["weekly_gain_pct"].rank(method="first", ascending=False).astype(int)

    weekly["week_start"] = weekly["week_start"].astype(str)
    weekly["week_end"] = weekly["week_end"].astype(str)
    weekly["close_date"] = weekly["close_date"].dt.date.astype(str)
    weekly["prev_close_date"] = weekly["prev_close_date"].dt.date.astype(str)

    weekly = weekly[
        [
            "week_start",
            "week_end",
            "rank",
            "symbol",
            "weekly_gain_pct",
            "prev_close",
            "close",
            "prev_close_date",
            "close_date",
        ]
    ].copy()
    weekly = weekly.rename(columns={"prev_close": "prev_week_close", "close": "week_close"})
    weekly["weekly_gain_pct"] = weekly["weekly_gain_pct"].round(4)
    weekly["prev_week_close"] = weekly["prev_week_close"].round(4)
    weekly["week_close"] = weekly["week_close"].round(4)
    return weekly


def main() -> int:
    parser = argparse.ArgumentParser(description="Week-wise top gainers for the last N months (from market.db).")
    parser.add_argument("--as-of", default=None, help="As-of date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--months", type=int, default=2, help="Lookback window in months (approx 30 days each).")
    parser.add_argument("--top", type=int, default=25, help="Top N gainers per week. Use 0 for all.")
    parser.add_argument(
        "--market",
        choices=["all", "india", "global"],
        default="all",
        help="Filter symbols: india=not in GLOBAL_STOCKS, global=in GLOBAL_STOCKS, all=no filter.",
    )
    parser.add_argument("--output", default=None, help="Output CSV path (defaults to reports folder).")
    args = parser.parse_args()

    as_of = _parse_ymd(args.as_of) if args.as_of else date.today()
    # Approximate 30 days per month; weekly buckets are calendar weeks.
    lookback_days = max(1, int(args.months) * 30)
    weeks_start = as_of - timedelta(days=lookback_days)
    weeks_end = as_of

    # Pull extra days so the first week in-range can still compute a pct change.
    fetch_start = weeks_start - timedelta(days=14)

    df = _load_prices(fetch_start, weeks_end)
    if not df.empty and args.market != "all":
        global_set = set(GLOBAL_STOCKS.keys())
        is_global = df["symbol"].isin(global_set)
        df = df[is_global].copy() if args.market == "global" else df[~is_global].copy()
    out = _weekly_gainers(df, weeks_start, weeks_end, args.top)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (
        Path(args.output)
        if args.output
        else (REPORT_DIR / f"equity_weekly_gainers_weekwise_{args.market}_{as_of.strftime('%Y%m%d')}.csv")
    )
    out.to_csv(output_path, index=False)

    print(str(output_path))
    print(f"weeks_start={weeks_start.isoformat()} weeks_end={weeks_end.isoformat()} top={args.top}")
    print(f"rows={len(out)} symbols={out['symbol'].nunique() if not out.empty else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
