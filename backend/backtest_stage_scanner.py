#!/usr/bin/env python3
import argparse
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.monthly_indicator_dataset import (  # noqa: E402
    _as_ohlcv_with_dates,
    _compute_all_indicators,
    _daily_to_monthly_ohlcv,
    _daily_to_weekly_ohlcv,
    _load_daily,
)


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


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _list_symbols() -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("SELECT DISTINCT index_name FROM prices ORDER BY index_name").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        if not r or not r[0]:
            continue
        sym = str(r[0]).strip().upper()
        if not sym or sym in EXCLUDE_SYMBOLS:
            continue
        out.append(sym)
    return out


@dataclass(frozen=True)
class FilterRow:
    stage: str
    column: str
    filter_type: str
    allowed: str | None
    min_val: float | None
    max_val: float | None


def _load_filters(path: Path) -> tuple[list[FilterRow], dict[str, int]]:
    df = pd.read_csv(path)
    required = {"stage", "column", "filter_type"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Filter CSV missing columns {sorted(missing)}: {path}")
    rows: list[FilterRow] = []
    counts: dict[str, int] = {}
    for r in df.itertuples(index=False):
        stage = str(getattr(r, "stage"))
        column = str(getattr(r, "column"))
        ftype = str(getattr(r, "filter_type"))
        allowed = getattr(r, "allowed", None)
        allowed = None if (allowed is None or (isinstance(allowed, float) and np.isnan(allowed)) or str(allowed).strip() == "") else str(allowed).strip().lower()
        mn = getattr(r, "min", None)
        mx = getattr(r, "max", None)
        mn = None if mn is None or (isinstance(mn, float) and np.isnan(mn)) else float(mn)
        mx = None if mx is None or (isinstance(mx, float) and np.isnan(mx)) else float(mx)
        rows.append(FilterRow(stage=stage, column=column, filter_type=ftype, allowed=allowed, min_val=mn, max_val=mx))
        counts[stage] = counts.get(stage, 0) + 1
    return rows, counts


def _boolish(value) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    txt = str(value).strip().lower()
    if txt in {"true", "1", "yes"}:
        return "true"
    if txt in {"false", "0", "no"}:
        return "false"
    return None


def _score_stage(snapshot: dict[str, object], filters: list[FilterRow], stage: str, singleton_pad_pct: float, singleton_pad_abs: float):
    evaluated = 0
    passed = 0
    for f in filters:
        if f.stage != stage:
            continue
        val = snapshot.get(f.column)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            continue
        if f.filter_type == "bool":
            v = _boolish(val)
            if v is None or f.allowed is None:
                continue
            evaluated += 1
            if v == f.allowed:
                passed += 1
        else:
            num = pd.to_numeric(pd.Series([val]), errors="coerce").iloc[0]
            if pd.isna(num) or f.min_val is None or f.max_val is None:
                continue
            evaluated += 1
            mn = f.min_val
            mx = f.max_val
            if mn == mx:
                center = mn
                pad = abs(center) * float(singleton_pad_pct)
                if abs(center) < 1.0:
                    pad = max(pad, float(singleton_pad_abs))
                mn = center - pad
                mx = center + pad
            if mn <= float(num) <= mx:
                passed += 1
    score = (passed / evaluated) if evaluated else 0.0
    return {"stage": stage, "evaluated": evaluated, "passed": passed, "score": float(score)}


def _build_snapshot_for_date(
    symbol: str,
    as_of_day: date,
    day_feat: pd.DataFrame,
    week_feat: pd.DataFrame,
    month_feat: pd.DataFrame,
) -> dict[str, object] | None:
    ts = pd.Timestamp(as_of_day)
    if ts not in day_feat.index:
        return None

    week_period = ts.to_period("W-SUN")
    month_period = ts.to_period("M")
    if week_period not in week_feat.index or month_period not in month_feat.index:
        return None

    day_row = day_feat.loc[ts]
    week_row = week_feat.loc[week_period]
    month_row = month_feat.loc[month_period]

    snap: dict[str, object] = {"symbol": symbol, "as_of": str(as_of_day)}
    for col, val in day_row.items():
        if col == "close_date":
            continue
        snap[f"day_{col}"] = val
    for col, val in week_row.items():
        if col == "close_date":
            continue
        snap[f"week_{col}"] = val
    for col, val in month_row.items():
        if col == "close_date":
            continue
        snap[f"month_{col}"] = val
    return snap


def _forward_return(close: pd.Series, idx: int, horizon: int) -> tuple[float | None, str | None]:
    if idx < 0 or idx >= len(close):
        return None, None
    j = idx + horizon
    if j >= len(close):
        return None, None
    start = float(close.iloc[idx])
    end = float(close.iloc[j])
    if not math.isfinite(start) or start == 0.0 or not math.isfinite(end):
        return None, None
    ret = (end / start - 1.0) * 100.0
    tdate = str(close.index[j].date())
    return float(ret), tdate


def _score_bucket(score: float, step: float) -> str:
    if score is None or not math.isfinite(score):
        return ""
    lo = max(0.0, math.floor(score / step) * step)
    hi = min(1.0, lo + step)
    return f"{lo:.2f}-{hi:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the stage scanner over a date range and compute forward-return stats by stage/score.")
    parser.add_argument("--filters", required=True, help="ADBE_stage_filters_*_combined.csv")
    parser.add_argument("--end", required=True, help="Backtest end date (YYYY-MM-DD).")
    parser.add_argument("--months", type=int, default=2, help="Lookback window length in months (approx 30d each).")
    parser.add_argument("--horizons", default="5,10,20", help="Comma-separated trading-day horizons (default 5,10,20).")
    parser.add_argument("--bucket-step", type=float, default=0.05, help="Score bucket step (default 0.05).")
    parser.add_argument("--singleton-pad-pct", type=float, default=0.1)
    parser.add_argument("--singleton-pad-abs", type=float, default=0.05)
    parser.add_argument("--output-prefix", default=None, help="Output prefix path (no extension). Defaults to reports.")
    args = parser.parse_args()

    end = _parse_ymd(args.end)
    start = end - timedelta(days=int(args.months) * 30)
    horizons = [int(x.strip()) for x in str(args.horizons).split(",") if x.strip()]
    horizons = sorted(set([h for h in horizons if h > 0]))
    if not horizons:
        raise SystemExit("No valid horizons provided.")

    filters_path = Path(args.filters)
    filters, counts = _load_filters(filters_path)
    stages = ["Setup", "PartyStart", "PartyOver"]

    symbols = _list_symbols()
    rows = []
    for sym in symbols:
        # Load enough future for horizons by using end + ~60 days
        daily = _load_daily(sym, end + timedelta(days=120))
        if daily.empty:
            continue
        daily = daily.sort_index()
        # build feature frames once per symbol
        day_feat = _compute_all_indicators(_as_ohlcv_with_dates(daily))
        if day_feat.empty:
            continue
        week_ohlcv = _daily_to_weekly_ohlcv(daily)
        month_ohlcv = _daily_to_monthly_ohlcv(daily)
        week_feat = _compute_all_indicators(week_ohlcv) if not week_ohlcv.empty else pd.DataFrame()
        month_feat = _compute_all_indicators(month_ohlcv) if not month_ohlcv.empty else pd.DataFrame()
        if week_feat.empty or month_feat.empty:
            continue

        # as-of dates for this symbol within [start,end]
        days = [d.date() for d in daily.index if start <= d.date() <= end]
        if not days:
            continue
        close = daily["close"] if "close" in daily.columns else daily["Close"]
        if close is None or close.empty:
            continue
        close = close.dropna()
        if close.empty:
            continue

        # map date -> index position in close series
        close_dates = [d.date() for d in close.index]
        pos_map = {d: i for i, d in enumerate(close_dates)}

        for d in days:
            if d not in pos_map:
                continue
            snap = _build_snapshot_for_date(sym, d, day_feat, week_feat, month_feat)
            if not snap:
                continue
            stage_scores = []
            for st in stages:
                stage_scores.append(
                    _score_stage(
                        snap,
                        filters,
                        st,
                        singleton_pad_pct=float(args.singleton_pad_pct),
                        singleton_pad_abs=float(args.singleton_pad_abs),
                    )
                )
            best = max(stage_scores, key=lambda x: (x["score"], x["evaluated"]))
            row = {
                "symbol": sym,
                "as_of": str(d),
                "best_stage": best["stage"],
                "best_score": round(best["score"], 6),
                "best_evaluated": int(best["evaluated"]),
                "best_bucket": _score_bucket(best["score"], float(args.bucket_step)),
            }
            for s in stage_scores:
                row[f"{s['stage']}_score"] = round(s["score"], 6)
                row[f"{s['stage']}_evaluated"] = int(s["evaluated"])

            idx = pos_map[d]
            for h in horizons:
                ret, tdate = _forward_return(close, idx, h)
                row[f"fwd_{h}d_date"] = tdate
                row[f"fwd_{h}d_ret_pct"] = None if ret is None else round(ret, 6)
            rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit("No backtest rows created (check DB coverage).")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = Path(args.output_prefix) if args.output_prefix else (REPORT_DIR / f"stage_backtest_{end.strftime('%Y%m%d')}_last{int(args.months)}m")
    rows_path = Path(str(prefix) + "_rows.csv")
    out.to_csv(rows_path, index=False)

    # Aggregate: by best_stage + best_bucket
    agg_rows = []
    for (st, bucket), g in out.groupby(["best_stage", "best_bucket"]):
        if not bucket or pd.isna(bucket):
            continue
        base = {"stage": st, "bucket": bucket, "n_rows": int(len(g))}
        for h in horizons:
            col = f"fwd_{h}d_ret_pct"
            vals = pd.to_numeric(g[col], errors="coerce").dropna()
            base[f"n_{h}d"] = int(len(vals))
            if len(vals) == 0:
                base[f"avg_{h}d"] = None
                base[f"median_{h}d"] = None
                base[f"winrate_{h}d"] = None
            else:
                base[f"avg_{h}d"] = round(float(vals.mean()), 6)
                base[f"median_{h}d"] = round(float(vals.median()), 6)
                base[f"winrate_{h}d"] = round(float((vals > 0).mean()), 6)
        agg_rows.append(base)

    agg = pd.DataFrame(agg_rows)
    agg = agg.sort_values(["stage", "bucket"])
    agg_path = Path(str(prefix) + "_calibration.csv")
    agg.to_csv(agg_path, index=False)

    print(str(rows_path))
    print(str(agg_path))
    print(f"start={start} end={end} symbols={out['symbol'].nunique()} rows={len(out)} filter_counts={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

