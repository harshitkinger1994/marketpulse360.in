#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd


REPORT_DIR = Path(__file__).resolve().parents[1] / "backend" / "reports"


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass(frozen=True)
class Window:
    name: str
    start: date
    end: date


def _read_best_streak(path: Path) -> tuple[date, date] | None:
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if df.empty or "is_best_total_gain" not in df.columns:
        return None
    best = df[df["is_best_total_gain"] == True]  # noqa: E712
    if best.empty:
        return None
    row = best.iloc[0]
    start = _parse_ymd(str(row["start"]))
    end = _parse_ymd(str(row["end"]))
    return start, end


def _trading_days(df: pd.DataFrame) -> list[date]:
    days = pd.to_datetime(df["day_date"], errors="coerce").dt.date.dropna().tolist()
    # Unique, sorted
    return sorted(set(days))


def _pad_by_trading_days(days: list[date], start: date, end: date, pad: int) -> tuple[date, date]:
    if not days:
        return start, end
    days_sorted = days
    # Clamp start/end into available range
    if start < days_sorted[0]:
        start = days_sorted[0]
    if end > days_sorted[-1]:
        end = days_sorted[-1]

    start_idx = next((i for i, d in enumerate(days_sorted) if d >= start), 0)
    end_idx = max(i for i, d in enumerate(days_sorted) if d <= end)

    start_idx = max(0, start_idx - pad)
    end_idx = min(len(days_sorted) - 1, end_idx + pad)
    return days_sorted[start_idx], days_sorted[end_idx]


def _pad_by_weeks(start: date, end: date, week_pad: int) -> tuple[date, date]:
    if week_pad <= 0:
        return start, end
    delta = timedelta(days=7 * week_pad)
    return start - delta, end + delta


def _weekly_base_range(mtf: pd.DataFrame, week_close_date: date) -> tuple[date, date] | None:
    # Weekly streaks are keyed by the week's last trading day ("close date"), not by Sunday.
    d = mtf.copy()
    d["day_date"] = pd.to_datetime(d["day_date"], errors="coerce").dt.date
    d["week_end"] = pd.to_datetime(d["week_end"], errors="coerce").dt.date
    d["week_start"] = pd.to_datetime(d["week_start"], errors="coerce").dt.date
    d = d.dropna(subset=["day_date", "week_start", "week_end"]).copy()
    row = d[d["day_date"] == week_close_date]
    if row.empty:
        # fallback to last available trading day <= week_close_date
        row = d[d["day_date"] <= week_close_date].tail(1)
    if row.empty:
        return None
    ws = row["week_start"].iloc[0]
    we = row["week_end"].iloc[0]
    if pd.isna(ws) or pd.isna(we):
        return None
    return ws, we


def _filter_window(mtf: pd.DataFrame, window: Window) -> pd.DataFrame:
    out = mtf.copy()
    out["day_date"] = pd.to_datetime(out["day_date"], errors="coerce").dt.date
    out = out.dropna(subset=["day_date"]).copy()
    out = out[(out["day_date"] >= window.start) & (out["day_date"] <= window.end)].copy()
    out.insert(1, "window", window.name)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract padded indicator windows from the daily MTF dataset using best gain streak ranges.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--as-of", required=True, help="As-of date used in file names (YYYY-MM-DD).")
    parser.add_argument("--last-months", type=int, default=2, help="Used only for file naming.")
    parser.add_argument("--mtf-csv", required=True, help="Daily MTF indicator dataset CSV (day_* + week_* + month_*).")
    parser.add_argument("--daily-streaks", required=True, help="Gain streaks daily CSV with is_best_total_gain.")
    parser.add_argument("--weekly-streaks", required=True, help="Gain streaks weekly CSV with is_best_total_gain.")
    parser.add_argument("--day-pad", type=int, default=2, help="Pad by N trading days on both sides.")
    parser.add_argument("--week-pad", type=int, default=1, help="Pad by N calendar weeks on both sides.")
    parser.add_argument("--output-prefix", default=None, help="Output prefix path (without suffix). Defaults to reports.")
    args = parser.parse_args()

    symbol = str(args.symbol).strip().upper()
    as_of = _parse_ymd(args.as_of)

    mtf_path = Path(args.mtf_csv)
    daily_streaks_path = Path(args.daily_streaks)
    weekly_streaks_path = Path(args.weekly_streaks)

    mtf = pd.read_csv(mtf_path)
    if mtf.empty or "day_date" not in mtf.columns:
        raise SystemExit(f"Invalid mtf dataset: {mtf_path}")

    trading_days = _trading_days(mtf)

    windows: list[Window] = []

    daily_best = _read_best_streak(daily_streaks_path)
    if daily_best:
        base_start, base_end = daily_best
        cal_start, cal_end = _pad_by_weeks(base_start, base_end, int(args.week_pad))
        start2, end2 = _pad_by_trading_days(trading_days, cal_start, cal_end, int(args.day_pad))
        windows.append(Window(name="best_daily_gain", start=start2, end=end2))

    weekly_best = _read_best_streak(weekly_streaks_path)
    if weekly_best:
        w_end_start, w_end_end = weekly_best
        # weekly streak range is expressed as week_end dates; map to week start/end dates.
        base1 = _weekly_base_range(mtf, w_end_start)
        base2 = _weekly_base_range(mtf, w_end_end)
        if base1 and base2:
            base_start = min(base1[0], base2[0])
            base_end = max(base1[1], base2[1])
            cal_start, cal_end = _pad_by_weeks(base_start, base_end, int(args.week_pad))
            start2, end2 = _pad_by_trading_days(trading_days, cal_start, cal_end, int(args.day_pad))
            windows.append(Window(name="best_weekly_gain", start=start2, end=end2))

    if not windows:
        raise SystemExit("No best streak found to extract windows from.")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = (
        Path(args.output_prefix)
        if args.output_prefix
        else (REPORT_DIR / f"{symbol}_gain_windows_{as_of.strftime('%Y%m%d')}_last{int(args.last_months)}m")
    )

    out_frames = []
    for w in windows:
        sliced = _filter_window(mtf, w)
        out_frames.append(sliced)
        out_path = Path(str(prefix) + f"_{w.name}.csv")
        sliced.to_csv(out_path, index=False)
        print(str(out_path))

    combined = pd.concat(out_frames, ignore_index=True)
    combined_path = Path(str(prefix) + "_combined.csv")
    combined.to_csv(combined_path, index=False)
    print(str(combined_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
