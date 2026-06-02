#!/usr/bin/env python3
import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "market.db"
REPORT_DIR = ROOT / "backend" / "reports"


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _load_closes(symbols: list[str], end: date) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(columns=["symbol", "date", "close"])
    conn = sqlite3.connect(str(DB_PATH))
    try:
        placeholders = ",".join(["?"] * len(symbols))
        params = symbols + [end.isoformat()]
        df = pd.read_sql_query(
            f"""
            SELECT index_name AS symbol, date, close
            FROM prices
            WHERE index_name IN ({placeholders}) AND date <= ?
            ORDER BY index_name, date
            """,
            conn,
            params=params,
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Add forward returns (to a target date) to a stage scan file.")
    parser.add_argument("--scan-csv", required=True, help="stage_scan_*_all.csv")
    parser.add_argument("--target", required=True, help="Target date (YYYY-MM-DD), uses last available <= target.")
    parser.add_argument("--output", default=None, help="Output CSV path (defaults to reports).")
    args = parser.parse_args()

    scan_path = Path(args.scan_csv)
    target = _parse_ymd(args.target)
    scan = pd.read_csv(scan_path)
    if scan.empty or "symbol" not in scan.columns or "as_of" not in scan.columns:
        raise SystemExit(f"Invalid scan CSV: {scan_path}")

    scan["symbol"] = scan["symbol"].astype(str).str.strip().str.upper()
    scan["as_of"] = pd.to_datetime(scan["as_of"], errors="coerce").dt.date
    scan = scan.dropna(subset=["as_of"]).copy()
    symbols = sorted(set(scan["symbol"].tolist()))

    closes = _load_closes(symbols, end=target)
    if closes.empty:
        raise SystemExit(f"No close data found up to target={target} for scan symbols.")

    # Map start close = close on scan as_of date (exact match); if missing, last <= as_of.
    closes_sorted = closes.sort_values(["symbol", "date"])
    start_close_map = {}
    for sym, g in closes_sorted.groupby("symbol"):
        g = g.sort_values("date")
        start_close_map[sym] = g

    start_closes = []
    target_closes = []
    target_dates = []
    for r in scan.itertuples(index=False):
        sym = r.symbol
        as_of = r.as_of
        g = start_close_map.get(sym)
        if g is None or g.empty:
            start_closes.append(np.nan)
            target_closes.append(np.nan)
            target_dates.append(None)
            continue
        g_as = g[g["date"] <= as_of]
        start_close = float(g_as.iloc[-1]["close"]) if not g_as.empty else np.nan
        g_t = g[g["date"] <= target]
        target_close = float(g_t.iloc[-1]["close"]) if not g_t.empty else np.nan
        tdate = g_t.iloc[-1]["date"] if not g_t.empty else None
        start_closes.append(start_close)
        target_closes.append(target_close)
        target_dates.append(tdate)

    scan["start_close"] = pd.to_numeric(pd.Series(start_closes), errors="coerce")
    scan["target_date"] = pd.Series(target_dates).astype("string")
    scan["target_close"] = pd.to_numeric(pd.Series(target_closes), errors="coerce")
    scan["return_to_target_pct"] = (scan["target_close"] / scan["start_close"] - 1.0) * 100.0
    scan["return_to_target_pct"] = scan["return_to_target_pct"].replace([np.inf, -np.inf], np.nan).round(4)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output) if args.output else (REPORT_DIR / f"{scan_path.stem}_to_{target.strftime('%Y%m%d')}.csv")
    scan.to_csv(out_path, index=False)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

