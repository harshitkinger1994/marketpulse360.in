#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass(frozen=True)
class GainStart:
    start: date
    end: date


def _read_best_gain_start(daily_streaks_csv: Path) -> GainStart:
    df = pd.read_csv(daily_streaks_csv)
    if df.empty or "is_best_total_gain" not in df.columns:
        raise SystemExit(f"Missing is_best_total_gain in {daily_streaks_csv}")
    best = df[df["is_best_total_gain"] == True]  # noqa: E712
    if best.empty:
        raise SystemExit(f"No best streak found in {daily_streaks_csv}")
    row = best.iloc[0]
    return GainStart(start=_parse_ymd(str(row["start"])), end=_parse_ymd(str(row["end"])))


def _get_prev_trading_day(days: list[date], start: date) -> date | None:
    prior = [d for d in days if d < start]
    return prior[-1] if prior else None


def _format_interval(iv) -> str:
    # iv is pandas.Interval
    left = iv.left
    right = iv.right
    try:
        left = float(left)
        right = float(right)
        # Keep compact ranges like "10-15"
        if abs(left) >= 1000 or abs(right) >= 1000:
            return f"{left:.2f}-{right:.2f}"
        if abs(left) >= 100 or abs(right) >= 100:
            return f"{left:.1f}-{right:.1f}"
        return f"{left:.2f}-{right:.2f}"
    except Exception:
        return str(iv)


def _bin_labels(series: pd.Series, bins: int) -> tuple[pd.Series, list[str]]:
    s = pd.to_numeric(series, errors="coerce")
    s = s.replace([np.inf, -np.inf], np.nan)
    s_non = s.dropna()
    if s_non.nunique() < 2:
        labels = ["single"]
        out = pd.Series(["single"] * len(s), index=s.index)
        return out, labels
    # Prefer quantile bins so ranges look like typical groupings.
    try:
        cats = pd.qcut(s, q=bins, duplicates="drop")
    except Exception:
        # Fallback to fewer bins
        q = min(max(2, int(s_non.nunique())), bins)
        cats = pd.qcut(s, q=q, duplicates="drop")
    raw_labels = [_format_interval(iv) for iv in cats.cat.categories]
    # Ensure unique category names even if formatting collapses intervals.
    seen = {}
    labels = []
    for lab in raw_labels:
        n = seen.get(lab, 0)
        seen[lab] = n + 1
        labels.append(lab if n == 0 else f"{lab}#{n+1}")
    labeled = cats.cat.rename_categories(labels)
    return labeled.astype("string"), labels


def _build_table(mtf: pd.DataFrame, prefix: str, prev_day: date, start_day: date, bins: int) -> pd.DataFrame:
    cols = [c for c in mtf.columns if c.startswith(prefix + "_")]
    if not cols:
        return pd.DataFrame()

    mtf_days = mtf.copy()
    mtf_days["day_date"] = pd.to_datetime(mtf_days["day_date"], errors="coerce").dt.date
    mtf_days = mtf_days.dropna(subset=["day_date"]).copy()

    prev_row = mtf_days[mtf_days["day_date"] == prev_day]
    start_row = mtf_days[mtf_days["day_date"] == start_day]
    if prev_row.empty or start_row.empty:
        return pd.DataFrame()
    prev_row = prev_row.iloc[0]
    start_row = start_row.iloc[0]

    rows = []
    for col in cols:
        raw_series = mtf_days[col]
        # boolean-like
        if raw_series.dropna().astype(str).str.lower().isin(["true", "false"]).all():
            prev_v = prev_row[col]
            start_v = start_row[col]
            rows.append(
                {
                    "timeframe": prefix,
                    "indicator": col[len(prefix) + 1 :],
                    "column": col,
                    "prev_day": str(prev_day),
                    "start_day": str(start_day),
                    "prev_value": str(prev_v),
                    "start_value": str(start_v),
                    "range_low": None,
                    "range_high": None,
                    "prev_bin": str(prev_v),
                    "start_bin": str(start_v),
                }
            )
            continue

        labeled, _ = _bin_labels(raw_series, bins=bins)
        prev_v = prev_row[col]
        start_v = start_row[col]
        prev_bin = labeled.loc[prev_row.name] if prev_row.name in labeled.index else None
        start_bin = labeled.loc[start_row.name] if start_row.name in labeled.index else None

        prev_num = pd.to_numeric(pd.Series([prev_v]), errors="coerce").iloc[0]
        start_num = pd.to_numeric(pd.Series([start_v]), errors="coerce").iloc[0]
        low = None
        high = None
        if pd.notna(prev_num) or pd.notna(start_num):
            vals = [v for v in [prev_num, start_num] if pd.notna(v)]
            if vals:
                low = float(min(vals))
                high = float(max(vals))

        rows.append(
            {
                "timeframe": prefix,
                "indicator": col[len(prefix) + 1 :],
                "column": col,
                "prev_day": str(prev_day),
                "start_day": str(start_day),
                "prev_value": None if pd.isna(prev_num) else float(prev_num),
                "start_value": None if pd.isna(start_num) else float(start_num),
                "range_low": low,
                "range_high": high,
                "prev_bin": None if pd.isna(prev_bin) else str(prev_bin),
                "start_bin": None if pd.isna(start_bin) else str(start_bin),
            }
        )
    out = pd.DataFrame(rows)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build per-indicator range bins for day-1 vs day1-of-best-gain-streak (monthly then weekly then daily)."
    )
    parser.add_argument("--mtf-csv", required=True, help="Daily MTF CSV (day_* + week_* + month_*).")
    parser.add_argument("--daily-streaks", required=True, help="Daily streaks CSV with is_best_total_gain.")
    parser.add_argument("--bins", type=int, default=10, help="Quantile bins per indicator (default 10).")
    parser.add_argument("--output", required=True, help="Output CSV path.")
    args = parser.parse_args()

    mtf = pd.read_csv(args.mtf_csv)
    if mtf.empty or "day_date" not in mtf.columns:
        raise SystemExit(f"Invalid mtf dataset: {args.mtf_csv}")

    mtf["day_date"] = pd.to_datetime(mtf["day_date"], errors="coerce").dt.date
    mtf = mtf.dropna(subset=["day_date"]).copy()
    trading_days = sorted(set(mtf["day_date"].tolist()))

    gain = _read_best_gain_start(Path(args.daily_streaks))
    prev_day = _get_prev_trading_day(trading_days, gain.start)
    if prev_day is None:
        raise SystemExit(f"No trading day found before gain start {gain.start}")

    # Monthly first, then weekly, then daily
    tables = []
    for prefix in ["month", "week", "day"]:
        tbl = _build_table(mtf, prefix=prefix, prev_day=prev_day, start_day=gain.start, bins=int(args.bins))
        if not tbl.empty:
            tables.append(tbl)

    out = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(args.output)
    print(f"gain_start={gain.start} prev_day={prev_day} bins={args.bins} rows={len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
