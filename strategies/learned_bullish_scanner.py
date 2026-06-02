#!/usr/bin/env python3
"""
Learn a bullish technical scanner from cached daily OHLCV data.

The script uses the local CSV cache in `.price_cache`, scores several bullish
technical setups on forward returns, chooses a robust parameter set, and then
applies that setup to the latest cached date.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_MIN_HISTORY_ROWS = 80
DEFAULT_MIN_TRADES = 40
DEFAULT_TOP_N = 10

BREAKOUT_LOOKBACKS = (20, 30, 55)
MIN_VOLUME_MULTIPLES = (1.0, 1.2, 1.5)
RSI_MIN_LEVELS = (52.0, 55.0, 58.0, 60.0)
RSI_MAX_LEVELS = (72.0, 75.0, 80.0)
CLOSE_POSITION_MIN_LEVELS = (0.55, 0.65, 0.75)
ATR_MIN_LEVELS = (1.5, 2.0)
RS20_MIN_LEVELS = (0.0, 3.0, 6.0)
FORWARD_HORIZONS = (5, 10)

MAX_BREAKOUT_DISTANCE_PCT = 8.0
MAX_EMA_GAP_PCT = 9.0
TREND_FAST = 9
TREND_MID = 21
TREND_SLOW = 50
VOLUME_SMA = 20
ATR_WINDOW = 14
RSI_WINDOW = 14
BENCHMARK_FILE = "INDEX_NSEI.csv"


@dataclass(frozen=True)
class Setup:
    breakout_lookback: int
    min_volume_multiple: float
    rsi_min: float
    rsi_max: float
    close_position_min: float
    min_atr_pct: float
    market_gate_enabled: bool
    rs20_min_pct: float
    forward_horizon_days: int
    max_breakout_distance_pct: float = MAX_BREAKOUT_DISTANCE_PCT
    max_ema_gap_pct: float = MAX_EMA_GAP_PCT


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_cache_dir() -> Path:
    return Path(__file__).resolve().parent / ".price_cache"


def _default_output_path() -> Path:
    return _repo_root() / "outputs" / "bullish_tech_scanner_latest.json"


def _to_iso_date(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _to_float(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        val = float(value)
    except Exception:
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return round(val, digits)


def _rsi(series: pd.Series, window: int = RSI_WINDOW) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _load_benchmark(cache_dir: Path) -> pd.DataFrame:
    path = cache_dir / BENCHMARK_FILE
    if not path.exists():
        raise FileNotFoundError(f"Missing benchmark file: {path}")

    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Benchmark file is empty: {path}")

    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["bench_sma50"] = df["Close"].rolling(TREND_SLOW).mean()
    df["bench_ret20"] = (df["Close"] / df["Close"].shift(20)) - 1.0
    df["bench_ok"] = (df["Close"] > df["bench_sma50"]) & (df["bench_ret20"] > -0.01)
    return df[["Date", "Close", "bench_sma50", "bench_ret20", "bench_ok"]].rename(
        columns={"Close": "bench_close"}
    )


def _build_symbol_frame(csv_path: Path, benchmark: pd.DataFrame, min_history_rows: int) -> pd.DataFrame | None:
    df = pd.read_csv(csv_path)
    if len(df) < min_history_rows:
        return None

    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    prev_close = close.shift(1)

    df["ema9"] = close.ewm(span=TREND_FAST, adjust=False).mean()
    df["ema21"] = close.ewm(span=TREND_MID, adjust=False).mean()
    df["sma50"] = close.rolling(TREND_SLOW).mean()
    df["ret20"] = (close / close.shift(20)) - 1.0
    df["rsi14"] = _rsi(close, RSI_WINDOW)

    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr14"] = tr.rolling(ATR_WINDOW).mean()
    df["atr_pct"] = (df["atr14"] / close) * 100.0
    df["vol_sma20"] = volume.rolling(VOLUME_SMA).mean()
    df["vol_mult"] = volume / df["vol_sma20"]
    df["close_position"] = np.where((high - low) > 0, (close - low) / (high - low), np.nan)
    df["ema_gap_pct"] = ((close / df["ema9"]) - 1.0) * 100.0

    for lookback in BREAKOUT_LOOKBACKS:
        prior_high = high.shift(1).rolling(lookback).max()
        df[f"hh_{lookback}"] = prior_high
        df[f"breakout_dist_{lookback}"] = ((close / prior_high) - 1.0) * 100.0

    for horizon in FORWARD_HORIZONS:
        df[f"fwd_{horizon}d_ret"] = (close.shift(-horizon) / close) - 1.0
        df[f"fwd_{horizon}d_pos"] = (df[f"fwd_{horizon}d_ret"] > 0).astype(float)
        df[f"fwd_{horizon}d_up2"] = (df[f"fwd_{horizon}d_ret"] >= 0.02).astype(float)

    df["symbol"] = csv_path.stem.replace(".NS", "")
    df = df.merge(benchmark, on="Date", how="left")
    df["rs20_pct"] = (df["ret20"] - df["bench_ret20"]) * 100.0
    return df


def _load_feature_universe(cache_dir: Path, min_history_rows: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    benchmark = _load_benchmark(cache_dir)
    stock_files = sorted(p for p in cache_dir.glob("*.csv") if not p.name.startswith("INDEX_"))

    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    for csv_path in stock_files:
        frame = _build_symbol_frame(csv_path, benchmark, min_history_rows)
        if frame is None:
            skipped.append(csv_path.name)
            continue
        frames.append(frame)

    if not frames:
        raise ValueError(f"No usable stock files found in {cache_dir}")

    universe = pd.concat(frames, ignore_index=True)
    universe = universe.dropna(
        subset=[
            "ema9",
            "ema21",
            "sma50",
            "rsi14",
            "atr_pct",
            "vol_mult",
            "close_position",
            "rs20_pct",
        ]
    ).reset_index(drop=True)

    latest_bench = benchmark.iloc[-1]
    meta = {
        "stock_files_total": len(stock_files),
        "stock_files_used": len(frames),
        "stock_files_skipped": skipped,
        "benchmark_latest_date": _to_iso_date(latest_bench["Date"]),
        "benchmark_latest_close": _to_float(latest_bench["bench_close"]),
        "benchmark_sma50": _to_float(latest_bench["bench_sma50"]),
        "benchmark_ret20_pct": _to_float(latest_bench["bench_ret20"] * 100.0),
        "benchmark_market_gate_ok": bool(latest_bench["bench_ok"]),
        "history_start": _to_iso_date(universe["Date"].min()),
        "history_end": _to_iso_date(universe["Date"].max()),
        "rows": int(len(universe)),
    }
    return universe, meta


def _score_sample(sample: pd.DataFrame, horizon: int) -> dict[str, float]:
    avg_ret_pct = float(sample[f"fwd_{horizon}d_ret"].mean() * 100.0)
    median_ret_pct = float(sample[f"fwd_{horizon}d_ret"].median() * 100.0)
    hit_pos_pct = float(sample[f"fwd_{horizon}d_pos"].mean() * 100.0)
    hit_2pct_pct = float(sample[f"fwd_{horizon}d_up2"].mean() * 100.0)
    score = avg_ret_pct + (hit_2pct_pct / 10.0) + (hit_pos_pct / 20.0) + math.log(len(sample))
    return {
        "avg_forward_return_pct": avg_ret_pct,
        "median_forward_return_pct": median_ret_pct,
        "hit_positive_pct": hit_pos_pct,
        "hit_target_2pct_pct": hit_2pct_pct,
        "score": score,
    }


def _evaluate_setup(universe: pd.DataFrame, setup: Setup) -> dict[str, Any] | None:
    breakout_col = f"hh_{setup.breakout_lookback}"
    breakout_dist_col = f"breakout_dist_{setup.breakout_lookback}"

    mask = (
        (universe["Close"] > universe["ema9"])
        & (universe["ema9"] > universe["ema21"])
        & (universe["ema21"] > universe["sma50"])
        & (universe["Close"] > universe[breakout_col])
        & (universe["vol_mult"] >= setup.min_volume_multiple)
        & (universe["rsi14"] >= setup.rsi_min)
        & (universe["rsi14"] <= setup.rsi_max)
        & (universe["close_position"] >= setup.close_position_min)
        & (universe["atr_pct"] >= setup.min_atr_pct)
        & (universe["rs20_pct"] >= setup.rs20_min_pct)
        & (universe[breakout_dist_col] <= setup.max_breakout_distance_pct)
        & (universe["ema_gap_pct"] <= setup.max_ema_gap_pct)
    )
    if setup.market_gate_enabled:
        mask = mask & (universe["bench_ok"] == True)

    sample = universe.loc[
        mask,
        [
            f"fwd_{setup.forward_horizon_days}d_ret",
            f"fwd_{setup.forward_horizon_days}d_pos",
            f"fwd_{setup.forward_horizon_days}d_up2",
        ],
    ].dropna()

    if sample.empty:
        return None

    metrics = _score_sample(sample, setup.forward_horizon_days)
    return {
        **asdict(setup),
        "trade_count": int(len(sample)),
        **{k: round(v, 4) for k, v in metrics.items()},
    }


def learn_setups(universe: pd.DataFrame, min_trades: int, top_n: int) -> dict[str, Any]:
    setups: list[dict[str, Any]] = []
    for values in product(
        BREAKOUT_LOOKBACKS,
        MIN_VOLUME_MULTIPLES,
        RSI_MIN_LEVELS,
        RSI_MAX_LEVELS,
        CLOSE_POSITION_MIN_LEVELS,
        ATR_MIN_LEVELS,
        (False, True),
        RS20_MIN_LEVELS,
        FORWARD_HORIZONS,
    ):
        setup = Setup(*values)
        if setup.rsi_max <= setup.rsi_min:
            continue
        result = _evaluate_setup(universe, setup)
        if not result or result["trade_count"] < min_trades:
            continue
        setups.append(result)

    if not setups:
        raise ValueError("No setup satisfied the minimum trade count.")

    setups_sorted = sorted(
        setups,
        key=lambda item: (
            item["score"],
            item["avg_forward_return_pct"],
            item["hit_target_2pct_pct"],
            item["trade_count"],
        ),
        reverse=True,
    )
    gated_sorted = [item for item in setups_sorted if item["market_gate_enabled"]]
    ungated_sorted = [item for item in setups_sorted if not item["market_gate_enabled"]]

    recommended = gated_sorted[0] if gated_sorted else setups_sorted[0]
    return {
        "recommended_setup": recommended,
        "top_setups_overall": setups_sorted[:top_n],
        "top_setups_market_aligned": gated_sorted[:top_n],
        "top_setups_without_market_gate": ungated_sorted[:top_n],
    }


def _setup_from_result(result: dict[str, Any], market_gate_enabled: bool | None = None) -> Setup:
    return Setup(
        breakout_lookback=int(result["breakout_lookback"]),
        min_volume_multiple=float(result["min_volume_multiple"]),
        rsi_min=float(result["rsi_min"]),
        rsi_max=float(result["rsi_max"]),
        close_position_min=float(result["close_position_min"]),
        min_atr_pct=float(result["min_atr_pct"]),
        market_gate_enabled=(
            bool(result["market_gate_enabled"]) if market_gate_enabled is None else bool(market_gate_enabled)
        ),
        rs20_min_pct=float(result["rs20_min_pct"]),
        forward_horizon_days=int(result["forward_horizon_days"]),
        max_breakout_distance_pct=float(result["max_breakout_distance_pct"]),
        max_ema_gap_pct=float(result["max_ema_gap_pct"]),
    )


def _build_current_row_record(row: pd.Series, setup: Setup) -> dict[str, Any]:
    breakout_col = f"hh_{setup.breakout_lookback}"
    breakout_dist_col = f"breakout_dist_{setup.breakout_lookback}"

    checks = {
        "trend_alignment": bool(row["Close"] > row["ema9"] > row["ema21"] > row["sma50"]),
        f"breakout_{setup.breakout_lookback}d": bool(row["Close"] > row[breakout_col]),
        "volume_confirmation": bool(row["vol_mult"] >= setup.min_volume_multiple),
        "rsi_window": bool(setup.rsi_min <= row["rsi14"] <= setup.rsi_max),
        "close_near_high": bool(row["close_position"] >= setup.close_position_min),
        "atr_floor": bool(row["atr_pct"] >= setup.min_atr_pct),
        "relative_strength": bool(row["rs20_pct"] >= setup.rs20_min_pct),
        "breakout_distance_ok": bool(row[breakout_dist_col] <= setup.max_breakout_distance_pct),
        "ema_gap_ok": bool(row["ema_gap_pct"] <= setup.max_ema_gap_pct),
    }
    if setup.market_gate_enabled:
        checks["market_gate"] = bool(row["bench_ok"])

    failed = [name for name, passed in checks.items() if not passed]
    return {
        "symbol": str(row["symbol"]),
        "date": _to_iso_date(row["Date"]),
        "pass_count": int(sum(1 for passed in checks.values() if passed)),
        "check_count": int(len(checks)),
        "failed_checks": failed,
        "close": _to_float(row["Close"]),
        "ema9": _to_float(row["ema9"]),
        "ema21": _to_float(row["ema21"]),
        "sma50": _to_float(row["sma50"]),
        "rsi14": _to_float(row["rsi14"]),
        "atr_pct": _to_float(row["atr_pct"]),
        "vol_mult": _to_float(row["vol_mult"]),
        "close_position": _to_float(row["close_position"]),
        "rs20_pct_vs_nifty": _to_float(row["rs20_pct"]),
        "breakout_distance_pct": _to_float(row[breakout_dist_col]),
        "ema_gap_pct": _to_float(row["ema_gap_pct"]),
    }


def scan_latest(universe: pd.DataFrame, setup: Setup, near_miss_gap: int = 2) -> dict[str, Any]:
    latest_date = universe["Date"].max()
    latest_rows = universe.loc[universe["Date"] == latest_date].copy()

    records = [_build_current_row_record(row, setup) for _, row in latest_rows.iterrows()]
    matches = [record for record in records if record["pass_count"] == record["check_count"]]
    near_misses = [
        record
        for record in records
        if record["pass_count"] >= max(1, record["check_count"] - near_miss_gap)
        and record["pass_count"] < record["check_count"]
    ]

    def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            item["pass_count"],
            item.get("rs20_pct_vs_nifty") or -999.0,
            item.get("vol_mult") or -999.0,
            -abs(item.get("breakout_distance_pct") or 999.0),
        )

    matches.sort(key=_sort_key, reverse=True)
    near_misses.sort(key=_sort_key, reverse=True)
    return {
        "as_of_date": _to_iso_date(latest_date),
        "matches": matches,
        "near_misses": near_misses[:10],
    }


def build_report(cache_dir: Path, output_path: Path, min_history_rows: int, min_trades: int, top_n: int) -> dict[str, Any]:
    universe, meta = _load_feature_universe(cache_dir, min_history_rows)
    learning = learn_setups(universe, min_trades=min_trades, top_n=top_n)
    recommended_setup = _setup_from_result(learning["recommended_setup"])
    gated_scan = scan_latest(universe, recommended_setup)
    relaxed_scan = scan_latest(universe, _setup_from_result(learning["recommended_setup"], market_gate_enabled=False))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_dir": str(cache_dir.resolve()),
        "learning_window": {
            "history_start": meta["history_start"],
            "history_end": meta["history_end"],
            "stock_files_total": meta["stock_files_total"],
            "stock_files_used": meta["stock_files_used"],
            "rows": meta["rows"],
            "min_history_rows": min_history_rows,
            "min_trades_required": min_trades,
        },
        "benchmark_context": {
            "symbol": "NIFTY",
            "latest_date": meta["benchmark_latest_date"],
            "latest_close": meta["benchmark_latest_close"],
            "sma50": meta["benchmark_sma50"],
            "ret20_pct": meta["benchmark_ret20_pct"],
            "market_gate_ok": meta["benchmark_market_gate_ok"],
        },
        "recommended_setup": learning["recommended_setup"],
        "top_setups_overall": learning["top_setups_overall"],
        "top_setups_market_aligned": learning["top_setups_market_aligned"],
        "top_setups_without_market_gate": learning["top_setups_without_market_gate"],
        "current_scan": gated_scan,
        "watchlist_if_market_gate_off": relaxed_scan,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Learn a bullish technical scanner from cached OHLCV data.")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory containing cached CSV data.")
    parser.add_argument("--output", type=Path, default=_default_output_path(), help="Where to write the JSON report.")
    parser.add_argument("--min-history-rows", type=int, default=DEFAULT_MIN_HISTORY_ROWS, help="Minimum rows required per symbol.")
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES, help="Minimum historical signals required for a setup.")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="How many top setups to include in the report.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    report = build_report(
        cache_dir=args.cache_dir.resolve(),
        output_path=args.output.resolve(),
        min_history_rows=max(10, int(args.min_history_rows)),
        min_trades=max(5, int(args.min_trades)),
        top_n=max(1, int(args.top_n)),
    )

    recommended = report["recommended_setup"]
    scan = report["current_scan"]
    relaxed = report["watchlist_if_market_gate_off"]

    print(
        "Learned bullish scanner:",
        f"{report['learning_window']['history_start']} -> {report['learning_window']['history_end']},",
        f"{report['learning_window']['stock_files_used']} stocks",
    )
    print(
        "Recommended setup:",
        f"breakout {recommended['breakout_lookback']}D,",
        f"volume>={recommended['min_volume_multiple']:.1f}x,",
        f"RSI {recommended['rsi_min']:.0f}-{recommended['rsi_max']:.0f},",
        f"ATR>={recommended['min_atr_pct']:.1f}%,",
        f"RS20>={recommended['rs20_min_pct']:.1f}%,",
        f"market_gate={'on' if recommended['market_gate_enabled'] else 'off'},",
        f"horizon={recommended['forward_horizon_days']}D",
    )
    print(
        "Historical quality:",
        f"trades={recommended['trade_count']},",
        f"avg_return={recommended['avg_forward_return_pct']:.2f}%,",
        f"hit_positive={recommended['hit_positive_pct']:.2f}%,",
        f"hit_2pct={recommended['hit_target_2pct_pct']:.2f}%",
    )
    print(
        "Current scan:",
        f"as_of={scan['as_of_date']},",
        f"matches={len(scan['matches'])},",
        f"benchmark_gate_ok={report['benchmark_context']['market_gate_ok']}",
    )
    if not scan["matches"] and relaxed["matches"]:
        symbols = ", ".join(item["symbol"] for item in relaxed["matches"][:5])
        print(f"Watchlist if market gate is ignored: {symbols}")
    print(f"Wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
