#!/usr/bin/env python3
import argparse
import math
import sys
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


REPORT_DIR = ROOT / "backend" / "reports"


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


def _build_snapshot(symbol: str, as_of: date) -> dict[str, object] | None:
    daily = _load_daily(symbol, as_of)
    if daily.empty:
        return None
    last_day = _as_of_trading_day(daily, as_of)
    if last_day is None:
        return None
    daily_cut = daily[daily.index.date <= last_day].copy()

    day_feat = _compute_all_indicators(_as_ohlcv_with_dates(daily_cut))
    if day_feat.empty or pd.Timestamp(last_day) not in day_feat.index:
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

    snap: dict[str, object] = {"symbol": symbol, "as_of": str(last_day)}
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


def _is_boolish(series: pd.Series) -> bool:
    s = series.dropna()
    if s.empty:
        return False
    u = s.astype(str).str.strip().str.lower().unique().tolist()
    return all(x in {"true", "false", "1", "0"} for x in u)


def _coerce_bool(series: pd.Series) -> pd.Series:
    def _one(v):
        if pd.isna(v):
            return np.nan
        t = str(v).strip().lower()
        if t in {"true", "1", "yes"}:
            return 1.0
        if t in {"false", "0", "no"}:
            return 0.0
        return np.nan

    return series.map(_one)


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 2 or b.size < 2:
        return None
    ma = float(np.mean(a))
    mb = float(np.mean(b))
    va = float(np.var(a, ddof=1))
    vb = float(np.var(b, ddof=1))
    pooled = math.sqrt(((a.size - 1) * va + (b.size - 1) * vb) / max(1.0, (a.size + b.size - 2)))
    if pooled == 0.0 or not math.isfinite(pooled):
        return None
    return (mb - ma) / pooled  # b - a


def _analyze_features(df: pd.DataFrame, label_col: str, group_cols: list[str] | None = None) -> pd.DataFrame:
    feature_cols = [c for c in df.columns if c.startswith(("day_", "week_", "month_"))]
    if not feature_cols:
        return pd.DataFrame()

    group_cols = group_cols or []

    out_rows = []
    group_keys = [()] if not group_cols else df[group_cols].drop_duplicates().itertuples(index=False, name=None)
    for gk in group_keys:
        if not group_cols:
            gdf = df
        else:
            mask = pd.Series(True, index=df.index)
            for col, val in zip(group_cols, gk):
                mask &= df[col] == val
            gdf = df[mask]
        if gdf.empty:
            continue

        y = pd.to_numeric(gdf[label_col], errors="coerce")
        if y.isna().all():
            continue

        neg = gdf[y == 1]
        pos = gdf[y == 0]
        n_neg = len(neg)
        n_pos = len(pos)
        if n_neg < 3 or n_pos < 3:
            continue

        for col in feature_cols:
            s = gdf[col]
            coverage = float(s.notna().mean())
            if coverage < 0.8:
                continue

            tf, ind = col.split("_", 1)
            if _is_boolish(s):
                b = _coerce_bool(s)
                bneg = b.loc[neg.index].dropna().to_numpy(dtype=float)
                bpos = b.loc[pos.index].dropna().to_numpy(dtype=float)
                if bneg.size < 3 or bpos.size < 3:
                    continue
                pneg = float(np.mean(bneg))
                ppos = float(np.mean(bpos))
                out_rows.append(
                    {
                        **({} if not group_cols else {c: v for c, v in zip(group_cols, gk)}),
                        "timeframe": tf,
                        "indicator": ind,
                        "column": col,
                        "type": "bool",
                        "n_pos": n_pos,
                        "n_neg": n_neg,
                        "coverage": round(coverage, 4),
                        "pos_mean": round(ppos, 4),
                        "neg_mean": round(pneg, 4),
                        "mean_diff_neg_minus_pos": round(pneg - ppos, 4),
                        "cohens_d": None,
                        "abs_signal": round(abs(pneg - ppos), 6),
                    }
                )
            else:
                num = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
                nneg = num.loc[neg.index].dropna().to_numpy(dtype=float)
                npos = num.loc[pos.index].dropna().to_numpy(dtype=float)
                if nneg.size < 3 or npos.size < 3:
                    continue
                d = _cohens_d(npos, nneg)
                out_rows.append(
                    {
                        **({} if not group_cols else {c: v for c, v in zip(group_cols, gk)}),
                        "timeframe": tf,
                        "indicator": ind,
                        "column": col,
                        "type": "num",
                        "n_pos": n_pos,
                        "n_neg": n_neg,
                        "coverage": round(coverage, 4),
                        "pos_mean": round(float(np.mean(npos)), 6),
                        "neg_mean": round(float(np.mean(nneg)), 6),
                        "mean_diff_neg_minus_pos": round(float(np.mean(nneg) - np.mean(npos)), 6),
                        "cohens_d": None if d is None else round(float(d), 6),
                        "abs_signal": None if d is None else round(abs(float(d)), 6),
                    }
                )

    out = pd.DataFrame(out_rows)
    if out.empty:
        return out
    out = out.sort_values(["abs_signal", "coverage"], ascending=[False, False])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare negative vs positive forward returns and rank indicators driving negatives.")
    parser.add_argument("--scan-with-returns", required=True, help="stage_scan_*_all_to_YYYYMMDD.csv")
    parser.add_argument("--as-of", required=True, help="As-of date filter (YYYY-MM-DD) to analyze.")
    parser.add_argument("--output-prefix", required=True, help="Output prefix path (no extension).")
    args = parser.parse_args()

    scan = pd.read_csv(args.scan_with_returns)
    scan["symbol"] = scan["symbol"].astype(str).str.strip().str.upper()
    scan["as_of"] = pd.to_datetime(scan["as_of"], errors="coerce").dt.date
    as_of = _parse_ymd(args.as_of)
    scan = scan[scan["as_of"] == as_of].copy()
    if scan.empty:
        raise SystemExit(f"No rows for as_of={as_of} in {args.scan_with_returns}")
    scan["as_of"] = scan["as_of"].astype(str)

    scan["return_to_target_pct"] = pd.to_numeric(scan["return_to_target_pct"], errors="coerce")
    scan = scan.dropna(subset=["return_to_target_pct"]).copy()
    scan["is_negative"] = (scan["return_to_target_pct"] < 0).astype(int)

    # Build snapshots
    snaps = []
    for sym in sorted(set(scan["symbol"].tolist())):
        snap = _build_snapshot(sym, as_of)
        if snap:
            snaps.append(snap)
    feats = pd.DataFrame(snaps)
    if feats.empty:
        raise SystemExit("No indicator snapshots built.")

    merged = scan.merge(feats, on=["symbol", "as_of"], how="inner")
    if merged.empty:
        raise SystemExit("No merge between scan rows and snapshots.")

    overall = _analyze_features(merged, label_col="is_negative", group_cols=None)
    by_stage = _analyze_features(merged, label_col="is_negative", group_cols=["best_stage"])

    out_prefix = Path(args.output_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    overall_path = Path(str(out_prefix) + "_overall.csv")
    stage_path = Path(str(out_prefix) + "_by_stage.csv")
    overall.to_csv(overall_path, index=False)
    by_stage.to_csv(stage_path, index=False)

    print(str(overall_path))
    print(str(stage_path))
    print(
        f"rows={len(merged)} neg={int(merged['is_negative'].sum())} pos={int((merged['is_negative']==0).sum())} as_of={as_of}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
