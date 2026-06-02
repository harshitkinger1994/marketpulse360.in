#!/usr/bin/env python3
import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
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


def _as_of_trading_day(daily: pd.DataFrame, as_of: date) -> date | None:
    if daily is None or daily.empty:
        return None
    idx = daily.index
    idx = idx[idx.date <= as_of]
    if len(idx) == 0:
        return None
    return idx[-1].date()


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
    timeframe: str
    stage: str
    column: str
    filter_type: str
    allowed: str | None
    min_val: float | None
    max_val: float | None


def _load_filters(path: Path) -> tuple[list[FilterRow], dict[str, int]]:
    df = pd.read_csv(path)
    required = {"timeframe", "stage", "column", "filter_type"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Filter CSV missing columns {sorted(missing)}: {path}")
    rows: list[FilterRow] = []
    by_stage: dict[str, int] = {}
    for r in df.itertuples(index=False):
        timeframe = str(getattr(r, "timeframe"))
        stage = str(getattr(r, "stage"))
        column = str(getattr(r, "column"))
        ftype = str(getattr(r, "filter_type"))
        allowed = getattr(r, "allowed", None)
        allowed = None if (allowed is None or (isinstance(allowed, float) and np.isnan(allowed)) or str(allowed).strip() == "") else str(allowed).strip().lower()
        mn = getattr(r, "min", None)
        mx = getattr(r, "max", None)
        mn = None if mn is None or (isinstance(mn, float) and np.isnan(mn)) else float(mn)
        mx = None if mx is None or (isinstance(mx, float) and np.isnan(mx)) else float(mx)
        rows.append(FilterRow(timeframe=timeframe, stage=stage, column=column, filter_type=ftype, allowed=allowed, min_val=mn, max_val=mx))
        by_stage[stage] = by_stage.get(stage, 0) + 1
    return rows, by_stage


def _boolish(value) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    txt = str(value).strip().lower()
    if txt in {"true", "1", "yes"}:
        return "true"
    if txt in {"false", "0", "no"}:
        return "false"
    return None


def _build_mtf_snapshot(symbol: str, as_of: date, max_stale_days: int) -> dict[str, object] | None:
    daily = _load_daily(symbol, as_of)
    if daily.empty:
        return None
    last_day = _as_of_trading_day(daily, as_of)
    if last_day is None:
        return None
    if (as_of - last_day).days > int(max_stale_days):
        return None

    daily_cut = daily[daily.index.date <= last_day].copy()
    day_feat = _compute_all_indicators(_as_ohlcv_with_dates(daily_cut))
    if day_feat.empty:
        return None
    day_row = day_feat.loc[pd.Timestamp(last_day)]

    weekly = _daily_to_weekly_ohlcv(daily_cut)
    week_feat = _compute_all_indicators(weekly)
    week_period = pd.Timestamp(last_day).to_period("W-SUN")
    if week_feat.empty or week_period not in week_feat.index:
        return None
    week_row = week_feat.loc[week_period]

    monthly = _daily_to_monthly_ohlcv(daily_cut)
    month_feat = _compute_all_indicators(monthly)
    month_period = pd.Timestamp(last_day).to_period("M")
    if month_feat.empty or month_period not in month_feat.index:
        return None
    month_row = month_feat.loc[month_period]

    snapshot: dict[str, object] = {"symbol": symbol, "as_of": str(last_day)}
    for col, val in day_row.items():
        if col == "close_date":
            continue
        snapshot[f"day_{col}"] = val
    for col, val in week_row.items():
        if col == "close_date":
            continue
        snapshot[f"week_{col}"] = val
    for col, val in month_row.items():
        if col == "close_date":
            continue
        snapshot[f"month_{col}"] = val
    return snapshot


def _score_stage(
    snapshot: dict[str, object],
    filters: list[FilterRow],
    stage: str,
    singleton_pad_pct: float,
    singleton_pad_abs: float,
) -> dict[str, object]:
    evaluated = 0
    passed = 0
    failed = 0
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
                failed += 1
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
            else:
                failed += 1
    score = (passed / evaluated) if evaluated else 0.0
    return {"stage": stage, "evaluated": evaluated, "passed": passed, "failed": failed, "score": round(score, 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan symbols and classify into Setup/PartyStart/PartyOver stages using ADBE-derived filter ranges.")
    parser.add_argument("--as-of", required=True, help="As-of date (YYYY-MM-DD). If non-trading day, uses last available <= date per symbol.")
    parser.add_argument("--filters", required=True, help="Combined filter CSV (e.g., ADBE_stage_filters_*_combined.csv).")
    parser.add_argument("--min-evaluated", type=int, default=120, help="Minimum evaluated filters to consider a match.")
    parser.add_argument("--min-score", type=float, default=0.75, help="Minimum score to include in stage list.")
    parser.add_argument("--max-stale-days", type=int, default=7, help="Skip symbols whose last bar is older than this many days.")
    parser.add_argument(
        "--singleton-pad-pct",
        type=float,
        default=0.1,
        help="For numeric filters where min==max, allow +/- this percent padding (default 0.10).",
    )
    parser.add_argument(
        "--singleton-pad-abs",
        type=float,
        default=0.05,
        help="For near-zero numeric filters (|v|<1) where min==max, allow +/- this absolute padding (default 0.05).",
    )
    parser.add_argument("--limit", type=int, default=50, help="Max symbols per stage in output tables.")
    parser.add_argument("--output-prefix", default=None, help="Output prefix path (no extension). Defaults to reports.")
    args = parser.parse_args()

    as_of = _parse_ymd(args.as_of)
    filters_path = Path(args.filters)
    filters, counts = _load_filters(filters_path)
    stages = ["Setup", "PartyStart", "PartyOver"]

    symbols = _list_symbols()
    rows = []
    for sym in symbols:
        snap = _build_mtf_snapshot(sym, as_of, max_stale_days=int(args.max_stale_days))
        if not snap:
            continue
        stage_scores = []
        for st in stages:
            s = _score_stage(
                snap,
                filters,
                st,
                singleton_pad_pct=float(args.singleton_pad_pct),
                singleton_pad_abs=float(args.singleton_pad_abs),
            )
            stage_scores.append(s)
        best = max(stage_scores, key=lambda x: (x["score"], x["evaluated"]))
        row = {
            "symbol": sym,
            "as_of": snap["as_of"],
            "best_stage": best["stage"],
            "best_score": best["score"],
            "best_evaluated": best["evaluated"],
        }
        for s in stage_scores:
            row[f"{s['stage']}_score"] = s["score"]
            row[f"{s['stage']}_evaluated"] = s["evaluated"]
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit("No symbols produced scan rows (check market.db data).")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = Path(args.output_prefix) if args.output_prefix else (REPORT_DIR / f"stage_scan_{as_of.strftime('%Y%m%d')}")
    all_path = Path(str(prefix) + "_all.csv")
    out.to_csv(all_path, index=False)

    stage_paths = {}
    for st in stages:
        sdf = out[(out[f"{st}_evaluated"] >= int(args.min_evaluated)) & (out[f"{st}_score"] >= float(args.min_score))].copy()
        sdf = sdf.sort_values([f"{st}_score", f"{st}_evaluated"], ascending=[False, False]).head(int(args.limit))
        sp = Path(str(prefix) + f"_{st.lower()}.csv")
        sdf.to_csv(sp, index=False)
        stage_paths[st] = sp

    print(str(all_path))
    for st in stages:
        print(str(stage_paths[st]))
    print(f"filters={filters_path} counts={counts} min_score={args.min_score} min_evaluated={args.min_evaluated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
