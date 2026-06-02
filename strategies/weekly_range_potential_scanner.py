#!/usr/bin/env python3
"""
Scan Indian stocks for weekly 7-8 week range setups with 10-15% move potential.

Long idea:
- stock is in an uptrend
- current weekly close is in the upper part of the last N-week range
- range is large enough to support a 10-15% measured move
- relative strength and weekly volume are supportive

Short idea:
- stock is in a downtrend
- current weekly close is in the lower part of the last N-week range
- range is large enough to support a 10-15% measured move
- relative weakness and weekly volume are supportive
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_LOOKBACK_WEEKS = 8
DEFAULT_NEAR_BOUNDARY_PCT = 3.0
DEFAULT_MIN_RANGE_PCT = 10.0
DEFAULT_MAX_TARGET_PCT = 15.0
DEFAULT_MAX_ITEMS = 20
BENCHMARK_FILE = "INDEX_NSEI.csv"


def _root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_cache_dir() -> Path:
    return Path(__file__).resolve().parent / ".price_cache"


def _default_output_path() -> Path:
    return _root_dir() / "outputs" / "weekly_range_potential_latest.json"


def _to_float(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        val = float(value)
    except Exception:
        return None
    if np.isnan(val) or np.isinf(val):
        return None
    return round(val, digits)


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.set_index("Date")
    out = (
        pd.DataFrame(
            {
                "Open": idx["Open"].resample("W-FRI").first(),
                "High": idx["High"].resample("W-FRI").max(),
                "Low": idx["Low"].resample("W-FRI").min(),
                "Close": idx["Close"].resample("W-FRI").last(),
                "Volume": idx["Volume"].resample("W-FRI").sum(),
            }
        )
        .dropna(subset=["Close"])
        .reset_index()
    )
    return out


def _load_daily_csv(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if df.empty:
        return None
    df["Date"] = pd.to_datetime(df["Date"])
    needed = {"Open", "High", "Low", "Close", "Volume", "Date"}
    if not needed.issubset(df.columns):
        return None
    return df.sort_values("Date").reset_index(drop=True)


def _build_weekly_frame(df: pd.DataFrame, lookback_weeks: int, benchmark_weekly: pd.DataFrame | None = None) -> pd.DataFrame:
    weekly = _resample_weekly(df)
    close = weekly["Close"]
    high = weekly["High"]
    low = weekly["Low"]
    vol = weekly["Volume"]

    weekly["ema10w"] = close.ewm(span=10, adjust=False).mean()
    weekly["ema20w"] = close.ewm(span=20, adjust=False).mean()
    weekly["rsi14w"] = _rsi(close, 14)
    weekly["ret8w"] = (close / close.shift(lookback_weeks)) - 1.0
    weekly["vol_sma8w"] = vol.rolling(lookback_weeks).mean()
    weekly["vol_mult_8w"] = vol / weekly["vol_sma8w"]
    weekly["range_high_8w"] = high.rolling(lookback_weeks).max()
    weekly["range_low_8w"] = low.rolling(lookback_weeks).min()
    weekly["prev_range_high_8w"] = high.shift(1).rolling(lookback_weeks).max()
    weekly["prev_range_low_8w"] = low.shift(1).rolling(lookback_weeks).min()

    range_span = weekly["range_high_8w"] - weekly["range_low_8w"]
    weekly["range_pos_8w"] = np.where(range_span > 0, (close - weekly["range_low_8w"]) / range_span, np.nan)
    weekly["weekly_close_pos"] = np.where((high - low) > 0, (close - low) / (high - low), np.nan)
    weekly["range_height_pct_8w"] = np.where(
        weekly["range_low_8w"] > 0,
        (range_span / weekly["range_low_8w"]) * 100.0,
        np.nan,
    )
    weekly["dist_to_high_pct_8w"] = np.where(
        weekly["range_high_8w"] > 0,
        ((weekly["range_high_8w"] - close) / weekly["range_high_8w"]) * 100.0,
        np.nan,
    )
    weekly["dist_to_low_pct_8w"] = np.where(
        weekly["range_low_8w"] > 0,
        ((close - weekly["range_low_8w"]) / weekly["range_low_8w"]) * 100.0,
        np.nan,
    )
    weekly["breakout_pct"] = np.where(
        weekly["prev_range_high_8w"] > 0,
        ((close / weekly["prev_range_high_8w"]) - 1.0) * 100.0,
        np.nan,
    )
    weekly["breakdown_pct"] = np.where(
        weekly["prev_range_low_8w"] > 0,
        ((weekly["prev_range_low_8w"] / close) - 1.0) * 100.0,
        np.nan,
    )

    if benchmark_weekly is not None:
        weekly = weekly.merge(
            benchmark_weekly[["Date", "ret8w"]].rename(columns={"ret8w": "bench_ret8w"}),
            on="Date",
            how="left",
        )
        weekly["rs_vs_nifty_pct"] = (weekly["ret8w"] - weekly["bench_ret8w"]) * 100.0
    else:
        weekly["bench_ret8w"] = np.nan
        weekly["rs_vs_nifty_pct"] = np.nan

    return weekly


def _project_target_pct(range_height_pct: float | None, min_range_pct: float, max_target_pct: float) -> float | None:
    if range_height_pct is None or np.isnan(range_height_pct):
        return None
    if range_height_pct < min_range_pct:
        return None
    return round(min(float(range_height_pct), float(max_target_pct)), 4)


def _build_buy_candidate(symbol: str, row: pd.Series, near_boundary_pct: float, min_range_pct: float, max_target_pct: float) -> dict[str, Any] | None:
    target_pct = _project_target_pct(_to_float(row["range_height_pct_8w"]), min_range_pct, max_target_pct)
    if target_pct is None:
        return None

    checks = {
        "trend_up": bool(row["Close"] > row["ema10w"] >= row["ema20w"]),
        "rsi_ok": bool(55.0 <= row["rsi14w"] <= 72.0),
        "volume_ok": bool(row["vol_mult_8w"] >= 1.0),
        "range_position_ok": bool(row["range_pos_8w"] >= 0.75),
        "close_strength_ok": bool(row["weekly_close_pos"] >= 0.55),
        "relative_strength_ok": bool(row["rs_vs_nifty_pct"] >= 0.0),
        "near_upper_range": bool(row["dist_to_high_pct_8w"] <= near_boundary_pct),
    }
    if not all(checks.values()):
        return None

    triggered = bool(pd.notna(row["prev_range_high_8w"]) and row["Close"] > row["prev_range_high_8w"])
    score = 0
    score += 2 if triggered else 1
    score += 1 if row["vol_mult_8w"] >= 1.2 else 0
    score += 1 if 58.0 <= row["rsi14w"] <= 68.0 else 0
    score += 1 if row["rs_vs_nifty_pct"] >= 5.0 else 0
    score += 1 if row["range_pos_8w"] >= 0.85 else 0
    score += 1 if row["weekly_close_pos"] >= 0.70 else 0

    return {
        "symbol": symbol,
        "side": "BUY",
        "state": "BUY_BREAKOUT" if triggered else "BUY_UPPER_RANGE",
        "score": int(score),
        "as_of_week": row["Date"].date().isoformat(),
        "close": _to_float(row["Close"]),
        "ema10w": _to_float(row["ema10w"]),
        "ema20w": _to_float(row["ema20w"]),
        "rsi14w": _to_float(row["rsi14w"]),
        "vol_mult_8w": _to_float(row["vol_mult_8w"]),
        "rs_vs_nifty_pct": _to_float(row["rs_vs_nifty_pct"]),
        "range_high_8w": _to_float(row["range_high_8w"]),
        "range_low_8w": _to_float(row["range_low_8w"]),
        "range_height_pct_8w": _to_float(row["range_height_pct_8w"]),
        "range_pos_8w": _to_float(row["range_pos_8w"]),
        "dist_to_high_pct_8w": _to_float(row["dist_to_high_pct_8w"]),
        "breakout_pct": _to_float(row["breakout_pct"]),
        "projected_move_pct": target_pct,
        "stop_reference": {
            "ema10w": _to_float(row["ema10w"]),
            "range_low_8w": _to_float(row["range_low_8w"]),
        },
    }


def _build_sell_candidate(symbol: str, row: pd.Series, near_boundary_pct: float, min_range_pct: float, max_target_pct: float) -> dict[str, Any] | None:
    target_pct = _project_target_pct(_to_float(row["range_height_pct_8w"]), min_range_pct, max_target_pct)
    if target_pct is None:
        return None

    checks = {
        "trend_down": bool(row["Close"] < row["ema10w"] <= row["ema20w"]),
        "rsi_ok": bool(28.0 <= row["rsi14w"] <= 45.0),
        "volume_ok": bool(row["vol_mult_8w"] >= 1.0),
        "range_position_ok": bool(row["range_pos_8w"] <= 0.25),
        "close_strength_ok": bool(row["weekly_close_pos"] <= 0.45),
        "relative_strength_ok": bool(row["rs_vs_nifty_pct"] <= 0.0),
        "near_lower_range": bool(row["dist_to_low_pct_8w"] <= near_boundary_pct),
    }
    if not all(checks.values()):
        return None

    triggered = bool(pd.notna(row["prev_range_low_8w"]) and row["Close"] < row["prev_range_low_8w"])
    score = 0
    score += 2 if triggered else 1
    score += 1 if row["vol_mult_8w"] >= 1.2 else 0
    score += 1 if 32.0 <= row["rsi14w"] <= 42.0 else 0
    score += 1 if row["rs_vs_nifty_pct"] <= -5.0 else 0
    score += 1 if row["range_pos_8w"] <= 0.15 else 0
    score += 1 if row["weekly_close_pos"] <= 0.30 else 0

    return {
        "symbol": symbol,
        "side": "SELL",
        "state": "SELL_BREAKDOWN" if triggered else "SELL_LOWER_RANGE",
        "score": int(score),
        "as_of_week": row["Date"].date().isoformat(),
        "close": _to_float(row["Close"]),
        "ema10w": _to_float(row["ema10w"]),
        "ema20w": _to_float(row["ema20w"]),
        "rsi14w": _to_float(row["rsi14w"]),
        "vol_mult_8w": _to_float(row["vol_mult_8w"]),
        "rs_vs_nifty_pct": _to_float(row["rs_vs_nifty_pct"]),
        "range_high_8w": _to_float(row["range_high_8w"]),
        "range_low_8w": _to_float(row["range_low_8w"]),
        "range_height_pct_8w": _to_float(row["range_height_pct_8w"]),
        "range_pos_8w": _to_float(row["range_pos_8w"]),
        "dist_to_low_pct_8w": _to_float(row["dist_to_low_pct_8w"]),
        "breakdown_pct": _to_float(row["breakdown_pct"]),
        "projected_move_pct": target_pct,
        "stop_reference": {
            "ema10w": _to_float(row["ema10w"]),
            "range_high_8w": _to_float(row["range_high_8w"]),
        },
    }


def build_report(
    cache_dir: Path,
    output_path: Path,
    lookback_weeks: int,
    near_boundary_pct: float,
    min_range_pct: float,
    max_target_pct: float,
    max_items: int,
) -> dict[str, Any]:
    benchmark_df = _load_daily_csv(cache_dir / BENCHMARK_FILE)
    if benchmark_df is None:
        raise FileNotFoundError(f"Missing usable benchmark file: {cache_dir / BENCHMARK_FILE}")
    benchmark_weekly = _build_weekly_frame(benchmark_df, lookback_weeks)

    buy_candidates: list[dict[str, Any]] = []
    sell_candidates: list[dict[str, Any]] = []
    skipped: list[str] = []

    for csv_path in sorted(cache_dir.glob("*.csv")):
        if csv_path.name.startswith("INDEX_"):
            continue
        df = _load_daily_csv(csv_path)
        if df is None or len(df) < 60:
            skipped.append(csv_path.name)
            continue

        weekly = _build_weekly_frame(df, lookback_weeks, benchmark_weekly)
        if len(weekly) < max(lookback_weeks + 12, 20):
            skipped.append(csv_path.name)
            continue

        row = weekly.iloc[-1]
        symbol = csv_path.stem.replace(".NS", "")

        buy = _build_buy_candidate(symbol, row, near_boundary_pct, min_range_pct, max_target_pct)
        if buy:
            buy_candidates.append(buy)

        sell = _build_sell_candidate(symbol, row, near_boundary_pct, min_range_pct, max_target_pct)
        if sell:
            sell_candidates.append(sell)

    buy_candidates.sort(
        key=lambda item: (
            item["score"],
            1 if item["state"] == "BUY_BREAKOUT" else 0,
            item.get("rs_vs_nifty_pct") or -999.0,
            item.get("vol_mult_8w") or -999.0,
        ),
        reverse=True,
    )
    sell_candidates.sort(
        key=lambda item: (
            item["score"],
            1 if item["state"] == "SELL_BREAKDOWN" else 0,
            -(item.get("rs_vs_nifty_pct") or 999.0),
            item.get("vol_mult_8w") or -999.0,
        ),
        reverse=True,
    )

    latest_bench = benchmark_weekly.iloc[-1]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_dir": str(cache_dir.resolve()),
        "benchmark": {
            "symbol": "NIFTY",
            "as_of_week": latest_bench["Date"].date().isoformat(),
            "close": _to_float(latest_bench["Close"]),
            "ema10w": _to_float(latest_bench["ema10w"]),
            "ema20w": _to_float(latest_bench["ema20w"]),
            "rsi14w": _to_float(latest_bench["rsi14w"]),
            "ret8w_pct": _to_float(latest_bench["ret8w"] * 100.0),
        },
        "rules": {
            "lookback_weeks": lookback_weeks,
            "goal_move_pct_range": [min_range_pct, max_target_pct],
            "near_boundary_pct": near_boundary_pct,
            "buy": {
                "trend": "close > ema10w >= ema20w",
                "rsi14w": [55.0, 72.0],
                "volume_multiple_min": 1.0,
                "range_position_min": 0.75,
                "weekly_close_position_min": 0.55,
                "relative_strength_vs_nifty_min_pct": 0.0,
                "distance_to_upper_range_max_pct": near_boundary_pct,
                "range_height_min_pct": min_range_pct,
            },
            "sell": {
                "trend": "close < ema10w <= ema20w",
                "rsi14w": [28.0, 45.0],
                "volume_multiple_min": 1.0,
                "range_position_max": 0.25,
                "weekly_close_position_max": 0.45,
                "relative_strength_vs_nifty_max_pct": 0.0,
                "distance_to_lower_range_max_pct": near_boundary_pct,
                "range_height_min_pct": min_range_pct,
            },
        },
        "counts": {
            "stocks_scanned": len(list(cache_dir.glob("*.csv"))) - len(list(cache_dir.glob("INDEX_*.csv"))),
            "buy_candidates": len(buy_candidates),
            "sell_candidates": len(sell_candidates),
            "skipped": len(skipped),
        },
        "buy_candidates": buy_candidates[:max_items],
        "sell_candidates": sell_candidates[:max_items],
        "skipped_symbols": skipped[:50],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly 7-8 week range potential scanner")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory containing cached daily CSV files.")
    parser.add_argument("--output", type=Path, default=_default_output_path(), help="JSON report output path.")
    parser.add_argument("--lookback-weeks", type=int, default=DEFAULT_LOOKBACK_WEEKS, help="Weekly range lookback. Use 7 or 8.")
    parser.add_argument("--near-boundary-pct", type=float, default=DEFAULT_NEAR_BOUNDARY_PCT, help="How close price must be to the range edge.")
    parser.add_argument("--min-range-pct", type=float, default=DEFAULT_MIN_RANGE_PCT, help="Minimum 7-8 week range height to consider.")
    parser.add_argument("--max-target-pct", type=float, default=DEFAULT_MAX_TARGET_PCT, help="Cap projected move at this percent.")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS, help="Maximum candidates to keep per side.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    report = build_report(
        cache_dir=args.cache_dir.resolve(),
        output_path=args.output.resolve(),
        lookback_weeks=max(4, int(args.lookback_weeks)),
        near_boundary_pct=max(0.5, float(args.near_boundary_pct)),
        min_range_pct=max(1.0, float(args.min_range_pct)),
        max_target_pct=max(float(args.min_range_pct), float(args.max_target_pct)),
        max_items=max(1, int(args.max_items)),
    )

    print(
        f"AS_OF_WEEK={report['benchmark']['as_of_week']} "
        f"BUY_CANDIDATES={report['counts']['buy_candidates']} "
        f"SELL_CANDIDATES={report['counts']['sell_candidates']}"
    )
    if report["buy_candidates"]:
        print("TOP_BUY_CANDIDATES:")
        for item in report["buy_candidates"][:10]:
            print(
                f"- {item['symbol']} | {item['state']} | score={item['score']} | "
                f"move={item['projected_move_pct']}% | rs8w={item['rs_vs_nifty_pct']}% | vol={item['vol_mult_8w']}x"
            )
    else:
        print("TOP_BUY_CANDIDATES: NONE")
    if report["sell_candidates"]:
        print("TOP_SELL_CANDIDATES:")
        for item in report["sell_candidates"][:10]:
            print(
                f"- {item['symbol']} | {item['state']} | score={item['score']} | "
                f"move={item['projected_move_pct']}% | rs8w={item['rs_vs_nifty_pct']}% | vol={item['vol_mult_8w']}x"
            )
    else:
        print("TOP_SELL_CANDIDATES: NONE")
    print(f"OUT_JSON={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
