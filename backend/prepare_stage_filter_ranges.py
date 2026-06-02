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
class Stage:
    name: str
    start: date
    end: date


def _read_best_streak(daily_streaks_csv: Path) -> tuple[date, date]:
    df = pd.read_csv(daily_streaks_csv)
    if df.empty or "is_best_total_gain" not in df.columns:
        raise SystemExit(f"Missing is_best_total_gain in {daily_streaks_csv}")
    best = df[df["is_best_total_gain"] == True]  # noqa: E712
    if best.empty:
        raise SystemExit(f"No best streak found in {daily_streaks_csv}")
    row = best.iloc[0]
    return _parse_ymd(str(row["start"])), _parse_ymd(str(row["end"]))


def _trading_days(mtf: pd.DataFrame) -> list[date]:
    dd = pd.to_datetime(mtf["day_date"], errors="coerce").dt.date
    return sorted(set(dd.dropna().tolist()))


def _prev_trading_day(days: list[date], d: date) -> date | None:
    prior = [x for x in days if x < d]
    return prior[-1] if prior else None


def _next_trading_days(days: list[date], start: date, n: int) -> list[date]:
    if n <= 0:
        return []
    after = [x for x in days if x >= start]
    return after[:n]


def _find_party_over_start(mtf: pd.DataFrame, days: list[date], gain_end: date) -> date | None:
    # First trading day after gain_end where day_return_pct <= 0
    df = mtf.copy()
    df["day_date"] = pd.to_datetime(df["day_date"], errors="coerce").dt.date
    df = df.dropna(subset=["day_date"]).copy()
    df = df.sort_values("day_date")
    df = df[df["day_date"] > gain_end]
    if df.empty or "day_return_pct" not in df.columns:
        return None
    ret = pd.to_numeric(df["day_return_pct"], errors="coerce")
    stop = df.loc[ret <= 0.0, "day_date"]
    return stop.iloc[0] if not stop.empty else None


def _is_booleanish(series: pd.Series) -> bool:
    s = series.dropna()
    if s.empty:
        return False
    lowered = s.astype(str).str.strip().str.lower().unique().tolist()
    return all(v in {"true", "false", "1", "0"} for v in lowered)


def _coerce_bool(value) -> str | None:
    if pd.isna(value):
        return None
    txt = str(value).strip().lower()
    if txt in {"true", "1", "yes"}:
        return "true"
    if txt in {"false", "0", "no"}:
        return "false"
    return None


def _fmt_num(x: float) -> str:
    if x is None or pd.isna(x):
        return ""
    ax = abs(float(x))
    if ax >= 1_000_000:
        return f"{float(x):.0f}"
    if ax >= 1_000:
        return f"{float(x):.2f}".rstrip("0").rstrip(".")
    if ax >= 100:
        return f"{float(x):.2f}".rstrip("0").rstrip(".")
    if ax >= 10:
        return f"{float(x):.3f}".rstrip("0").rstrip(".")
    return f"{float(x):.4f}".rstrip("0").rstrip(".")


def _make_range_str(low: float | None, high: float | None) -> str:
    if low is None or high is None or pd.isna(low) or pd.isna(high):
        return ""
    if float(low) == float(high):
        return _fmt_num(low)
    return f"{_fmt_num(low)}-{_fmt_num(high)}"


def _stage_slice(mtf: pd.DataFrame, stage: Stage) -> pd.DataFrame:
    df = mtf.copy()
    df["day_date"] = pd.to_datetime(df["day_date"], errors="coerce").dt.date
    df = df.dropna(subset=["day_date"]).copy()
    return df[(df["day_date"] >= stage.start) & (df["day_date"] <= stage.end)].copy()


def _build_filters_for_prefix(mtf: pd.DataFrame, prefix: str, stages: list[Stage], min_coverage: float) -> pd.DataFrame:
    cols = [c for c in mtf.columns if c.startswith(prefix + "_")]
    if not cols:
        return pd.DataFrame()

    out_rows = []
    total_rows = len(mtf)
    for stage in stages:
        sdf = _stage_slice(mtf, stage)
        if sdf.empty:
            continue
        for col in cols:
            ser = sdf[col]
            # Coverage = non-null fraction within the stage slice
            coverage = float(ser.notna().mean()) if len(ser) else 0.0
            if coverage < min_coverage:
                continue

            if _is_booleanish(ser):
                vals = [_coerce_bool(v) for v in ser.tolist()]
                vals = [v for v in vals if v is not None]
                if not vals:
                    continue
                mode = pd.Series(vals).mode()
                allowed = mode.iloc[0] if not mode.empty else vals[-1]
                out_rows.append(
                    {
                        "timeframe": prefix,
                        "stage": stage.name,
                        "indicator": col[len(prefix) + 1 :],
                        "column": col,
                        "stage_start": str(stage.start),
                        "stage_end": str(stage.end),
                        "filter_type": "bool",
                        "allowed": allowed,
                        "min": None,
                        "max": None,
                        "range": allowed,
                        "coverage": round(coverage, 4),
                    }
                )
                continue

            num = pd.to_numeric(ser, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            if num.empty:
                continue
            low = float(num.min())
            high = float(num.max())
            out_rows.append(
                {
                    "timeframe": prefix,
                    "stage": stage.name,
                    "indicator": col[len(prefix) + 1 :],
                    "column": col,
                    "stage_start": str(stage.start),
                    "stage_end": str(stage.end),
                    "filter_type": "range",
                    "allowed": None,
                    "min": round(low, 6),
                    "max": round(high, 6),
                    "range": _make_range_str(low, high),
                    "coverage": round(coverage, 4),
                }
            )

    out = pd.DataFrame(out_rows)
    if out.empty:
        return out
    stage_order = {"Setup": 0, "PartyStart": 1, "PartyOver": 2}
    out["_stage_order"] = out["stage"].map(stage_order).fillna(99).astype(int)
    out = out.sort_values(["timeframe", "_stage_order", "indicator"]).drop(columns=["_stage_order"])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare stage-wise indicator filter ranges for scanning.")
    parser.add_argument("--mtf-csv", required=True, help="Daily MTF CSV (day_* + week_* + month_*).")
    parser.add_argument("--daily-streaks", required=True, help="Daily gain streaks CSV with is_best_total_gain.")
    parser.add_argument("--over-days", type=int, default=1, help="PartyOver window length in trading days (default 1).")
    parser.add_argument("--min-coverage", type=float, default=0.8, help="Min non-null fraction within stage slice.")
    parser.add_argument("--output-prefix", required=True, help="Output file prefix (no extension).")
    args = parser.parse_args()

    mtf = pd.read_csv(args.mtf_csv)
    if mtf.empty or "day_date" not in mtf.columns:
        raise SystemExit(f"Invalid mtf dataset: {args.mtf_csv}")

    days = _trading_days(mtf)
    gain_start, gain_end = _read_best_streak(Path(args.daily_streaks))
    prev_day = _prev_trading_day(days, gain_start)
    if prev_day is None:
        raise SystemExit(f"No trading day before gain_start={gain_start}")

    over_start = _find_party_over_start(mtf, days, gain_end) or gain_end
    over_days = int(args.over_days)
    over_list = _next_trading_days(days, over_start, over_days)
    if not over_list:
        over_list = [over_start]
    over_end = over_list[-1]

    stages = [
        Stage(name="Setup", start=prev_day, end=prev_day),
        Stage(name="PartyStart", start=gain_start, end=gain_end),
        Stage(name="PartyOver", start=over_start, end=over_end),
    ]

    min_cov = float(args.min_coverage)
    frames = []
    for prefix in ["month", "week", "day"]:
        f = _build_filters_for_prefix(mtf, prefix, stages, min_coverage=min_cov)
        if not f.empty:
            frames.append(f)
            out_path = Path(args.output_prefix + f"_{prefix}.csv")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            f.to_csv(out_path, index=False)
            print(str(out_path))

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    combined_path = Path(args.output_prefix + "_combined.csv")
    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(combined_path, index=False)
    print(str(combined_path))

    print(f"setup={prev_day} party={gain_start}..{gain_end} over={over_start}..{over_end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
