#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.equity_gainer_indicator_study import (  # noqa: E402
    _compute_indicators,
    _download_history,
    _load_equity_universe,
    _resample_ohlcv,
)


REPORT_DIR = ROOT / "backend" / "reports"
DEFAULT_PROFILE_NAME = "topdown_weekly_daily_intraday_v1"


PROFILE = {
    "strategy_name": "Topdown Weekly-Daily-Intraday Alignment v1",
    "description": (
        "Weekly defines regime, daily defines setup, and 4h + 3h confirm timing. "
        "A stock passes only when all required checks align."
    ),
    "weekly": {
        "required": [
            {"field": "above_ema9", "op": "is_true", "label": "Weekly close > EMA9"},
            {"field": "above_ema21", "op": "is_true", "label": "Weekly close > EMA21"},
            {"field": "rsi14", "op": ">=", "value": 50.0, "label": "Weekly RSI14 >= 50"},
        ],
        "support": [
            {"field": "above_sma50", "op": "is_true", "label": "Weekly close > SMA50"},
            {"field": "adx14", "op": ">=", "value": 20.0, "label": "Weekly ADX14 >= 20"},
            {"field": "bb_pos", "op": ">=", "value": 0.50, "label": "Weekly BB position >= 0.50"},
        ],
    },
    "daily": {
        "required": [
            {"field": "above_ema9", "op": "is_true", "label": "Daily close > EMA9"},
            {"field": "above_ema21", "op": "is_true", "label": "Daily close > EMA21"},
            {"field": "ema9_gt_ema21", "op": "is_true", "label": "Daily EMA9 > EMA21"},
            {"field": "macd_hist_pos", "op": "is_true", "label": "Daily MACD histogram > 0"},
            {"field": "bb_pos", "op": ">=", "value": 0.50, "label": "Daily BB position >= 0.50"},
            {"field": "rsi14", "op": "between", "min": 58.0, "max": 67.0, "label": "Daily RSI14 in 58-67"},
            {
                "field": "close_vs_ema21_pct",
                "op": "between",
                "min": 3.0,
                "max": 6.5,
                "label": "Daily close vs EMA21 in +3.0% to +6.5%",
            },
            {"field": "adx14", "op": ">=", "value": 20.0, "label": "Daily ADX14 >= 20"},
        ],
        "support": [
            {"field": "above_sma50", "op": "is_true", "label": "Daily close > SMA50"},
            {
                "field": "close_vs_ema9_pct",
                "op": "between",
                "min": 1.0,
                "max": 4.5,
                "label": "Daily close vs EMA9 in +1.0% to +4.5%",
            },
            {
                "field": "stoch_d",
                "op": "between",
                "min": 85.0,
                "max": 96.0,
                "label": "Daily Stoch D in 85-96",
            },
        ],
    },
    "4h": {
        "required": [
            {"field": "above_ema21", "op": "is_true", "label": "4h close > EMA21"},
            {"field": "above_sma50", "op": "is_true", "label": "4h close > SMA50"},
            {"field": "ema9_gt_ema21", "op": "is_true", "label": "4h EMA9 > EMA21"},
            {"field": "ema21_gt_sma50", "op": "is_true", "label": "4h EMA21 > SMA50"},
            {"field": "rsi14", "op": ">=", "value": 60.0, "label": "4h RSI14 >= 60"},
            {"field": "adx14", "op": ">=", "value": 20.0, "label": "4h ADX14 >= 20"},
            {"field": "bb_pos", "op": ">=", "value": 0.50, "label": "4h BB position >= 0.50"},
            {
                "field": "close_vs_ema21_pct",
                "op": "between",
                "min": 1.5,
                "max": 4.5,
                "label": "4h close vs EMA21 in +1.5% to +4.5%",
            },
        ],
        "support": [
            {"field": "above_ema9", "op": "is_true", "label": "4h close > EMA9"},
            {"field": "macd_hist_pos", "op": "is_true", "label": "4h MACD histogram > 0"},
        ],
    },
    "3h": {
        "required": [
            {"field": "above_ema21", "op": "is_true", "label": "3h close > EMA21"},
            {"field": "above_sma50", "op": "is_true", "label": "3h close > SMA50"},
            {"field": "ema9_gt_ema21", "op": "is_true", "label": "3h EMA9 > EMA21"},
            {"field": "ema21_gt_sma50", "op": "is_true", "label": "3h EMA21 > SMA50"},
            {"field": "rsi14", "op": ">=", "value": 60.0, "label": "3h RSI14 >= 60"},
            {"field": "adx14", "op": ">=", "value": 20.0, "label": "3h ADX14 >= 20"},
            {"field": "bb_pos", "op": ">=", "value": 0.50, "label": "3h BB position >= 0.50"},
            {
                "field": "close_vs_ema21_pct",
                "op": "between",
                "min": 1.0,
                "max": 4.0,
                "label": "3h close vs EMA21 in +1.0% to +4.0%",
            },
        ],
        "support": [
            {"field": "above_ema9", "op": "is_true", "label": "3h close > EMA9"},
            {"field": "macd_hist_pos", "op": "is_true", "label": "3h MACD histogram > 0"},
        ],
    },
}


def _evaluate_rule(value, rule):
    op = rule["op"]
    if value is None:
        return False
    if op == "is_true":
        return bool(value)
    if op == ">=":
        return float(value) >= float(rule["value"])
    if op == "<=":
        return float(value) <= float(rule["value"])
    if op == "between":
        return float(rule["min"]) <= float(value) <= float(rule["max"])
    raise ValueError(f"Unsupported rule op: {op}")


def _evaluate_timeframe(indicators, timeframe_rules):
    required_checks = []
    support_checks = []

    for rule in timeframe_rules.get("required", []):
        value = indicators.get(rule["field"])
        passed = _evaluate_rule(value, rule)
        required_checks.append(
            {
                "field": rule["field"],
                "label": rule["label"],
                "value": value,
                "passed": passed,
            }
        )
    for rule in timeframe_rules.get("support", []):
        value = indicators.get(rule["field"])
        passed = _evaluate_rule(value, rule)
        support_checks.append(
            {
                "field": rule["field"],
                "label": rule["label"],
                "value": value,
                "passed": passed,
            }
        )

    required_pass = sum(1 for c in required_checks if c["passed"])
    support_pass = sum(1 for c in support_checks if c["passed"])
    return {
        "required_pass": required_pass,
        "required_total": len(required_checks),
        "required_all_pass": required_pass == len(required_checks),
        "support_pass": support_pass,
        "support_total": len(support_checks),
        "required_checks": required_checks,
        "support_checks": support_checks,
    }


def _fetch_timeframes(yf_symbol):
    daily_long = _download_history(yf_symbol, period="15y", interval="1d")
    hourly = _download_history(yf_symbol, period="730d", interval="60m")
    return {
        "weekly": _resample_ohlcv(daily_long, "W-FRI"),
        "daily": daily_long.copy(),
        "4h": _resample_ohlcv(hourly, "4h"),
        "3h": _resample_ohlcv(hourly, "3h"),
    }


def _build_symbol_result(symbol_row):
    tf_frames = _fetch_timeframes(symbol_row["yf_symbol"])
    tf_indicators = {}
    tf_eval = {}
    for timeframe in ("weekly", "daily", "4h", "3h"):
        indicators = _compute_indicators(tf_frames.get(timeframe))
        tf_indicators[timeframe] = indicators
        tf_eval[timeframe] = _evaluate_timeframe(indicators, PROFILE[timeframe]) if indicators else None

    required_total = 0
    required_pass = 0
    support_total = 0
    support_pass = 0
    timeframe_passes = {}
    for timeframe in ("weekly", "daily", "4h", "3h"):
        ev = tf_eval.get(timeframe)
        if not ev:
            timeframe_passes[timeframe] = False
            continue
        required_total += ev["required_total"]
        required_pass += ev["required_pass"]
        support_total += ev["support_total"]
        support_pass += ev["support_pass"]
        timeframe_passes[timeframe] = bool(ev["required_all_pass"])

    strict_pass = all(timeframe_passes.values()) and bool(timeframe_passes)
    return {
        "symbol": symbol_row["symbol"],
        "yf_symbol": symbol_row["yf_symbol"],
        "market": symbol_row["market"],
        "strict_pass": strict_pass,
        "required_pass": required_pass,
        "required_total": required_total,
        "required_ratio": round(required_pass / required_total, 4) if required_total else 0.0,
        "support_pass": support_pass,
        "support_total": support_total,
        "support_ratio": round(support_pass / support_total, 4) if support_total else 0.0,
        "timeframe_passes": timeframe_passes,
        "timeframes": tf_indicators,
        "evaluations": tf_eval,
    }


def main():
    parser = argparse.ArgumentParser(description="Strict top-down weekly/daily/4h/3h alignment scan")
    parser.add_argument("--limit", type=int, default=0, help="Optional max universe size for testing")
    parser.add_argument(
        "--out-json",
        default=str(REPORT_DIR / "topdown_mtf_alignment_scan_latest.json"),
        help="Output JSON file",
    )
    args = parser.parse_args()

    universe = _load_equity_universe()
    if args.limit and args.limit > 0:
        universe = universe[: int(args.limit)]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    skipped = []
    for row in universe:
        try:
            results.append(_build_symbol_result(row))
        except Exception as exc:
            skipped.append(
                {
                    "symbol": row["symbol"],
                    "yf_symbol": row["yf_symbol"],
                    "market": row["market"],
                    "reason": repr(exc),
                }
            )

    strict = [r for r in results if r["strict_pass"]]
    near_misses = [r for r in results if not r["strict_pass"]]
    near_misses.sort(
        key=lambda r: (
            r["required_ratio"],
            r["support_ratio"],
            r["required_pass"],
            r["support_pass"],
            r["symbol"],
        ),
        reverse=True,
    )

    summary_rows = []
    for r in strict + near_misses:
        timeframes = r["timeframes"]
        summary_rows.append(
            {
                "symbol": r["symbol"],
                "market": r["market"],
                "strict_pass": r["strict_pass"],
                "required_pass": r["required_pass"],
                "required_total": r["required_total"],
                "required_ratio": r["required_ratio"],
                "support_pass": r["support_pass"],
                "support_total": r["support_total"],
                "support_ratio": r["support_ratio"],
                "weekly_pass": r["timeframe_passes"].get("weekly"),
                "daily_pass": r["timeframe_passes"].get("daily"),
                "4h_pass": r["timeframe_passes"].get("4h"),
                "3h_pass": r["timeframe_passes"].get("3h"),
                "weekly_close": (timeframes.get("weekly") or {}).get("close"),
                "weekly_rsi14": (timeframes.get("weekly") or {}).get("rsi14"),
                "daily_close": (timeframes.get("daily") or {}).get("close"),
                "daily_rsi14": (timeframes.get("daily") or {}).get("rsi14"),
                "daily_adx14": (timeframes.get("daily") or {}).get("adx14"),
                "daily_close_vs_ema21_pct": (timeframes.get("daily") or {}).get("close_vs_ema21_pct"),
                "4h_rsi14": (timeframes.get("4h") or {}).get("rsi14"),
                "4h_adx14": (timeframes.get("4h") or {}).get("adx14"),
                "3h_rsi14": (timeframes.get("3h") or {}).get("rsi14"),
                "3h_adx14": (timeframes.get("3h") or {}).get("adx14"),
            }
        )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile_name": DEFAULT_PROFILE_NAME,
        "profile": PROFILE,
        "universe_size": len(universe),
        "strict_candidate_count": len(strict),
        "strict_candidates": strict,
        "top_near_misses": near_misses[:25],
        "summary_rows": summary_rows[:50],
        "skipped": skipped,
    }

    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))

    print(f"OUT_JSON={out_path}")
    print(f"UNIVERSE_SIZE={len(universe)}")
    print(f"STRICT_CANDIDATE_COUNT={len(strict)}")
    if strict:
        print("STRICT_CANDIDATES:")
        for r in strict:
            print(
                f"- {r['symbol']} | {r['market']} | req {r['required_pass']}/{r['required_total']} "
                f"| support {r['support_pass']}/{r['support_total']}"
            )
    else:
        print("STRICT_CANDIDATES: NONE")
    print("TOP_NEAR_MISSES:")
    for r in near_misses[:15]:
        print(
            f"- {r['symbol']} | {r['market']} | req {r['required_pass']}/{r['required_total']} "
            f"| weekly={r['timeframe_passes'].get('weekly')} daily={r['timeframe_passes'].get('daily')} "
            f"| 4h={r['timeframe_passes'].get('4h')} 3h={r['timeframe_passes'].get('3h')}"
        )


if __name__ == "__main__":
    main()
