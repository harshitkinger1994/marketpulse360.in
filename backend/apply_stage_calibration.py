#!/usr/bin/env python3
import argparse
import math
from pathlib import Path

import pandas as pd


def _score_bucket(score: float, step: float) -> str:
    if score is None or not math.isfinite(score):
        return ""
    lo = max(0.0, math.floor(score / step) * step)
    hi = min(1.0, lo + step)
    return f"{lo:.2f}-{hi:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Join backtested calibration stats onto a stage scan file.")
    parser.add_argument("--scan", required=True, help="stage_scan_*_all.csv")
    parser.add_argument("--calibration", required=True, help="stage_backtest_*_calibration.csv")
    parser.add_argument("--bucket-step", type=float, default=0.05)
    parser.add_argument("--output", required=True, help="Output CSV path.")
    args = parser.parse_args()

    scan = pd.read_csv(args.scan)
    cal = pd.read_csv(args.calibration)
    if scan.empty:
        raise SystemExit("Empty scan CSV.")
    if cal.empty:
        raise SystemExit("Empty calibration CSV.")

    scan["best_stage"] = scan["best_stage"].astype(str)
    scan["best_score"] = pd.to_numeric(scan["best_score"], errors="coerce")
    scan["best_bucket"] = scan["best_score"].map(lambda x: _score_bucket(float(x), float(args.bucket_step)) if pd.notna(x) else "")

    cal = cal.rename(columns={"stage": "best_stage", "bucket": "best_bucket"})
    merged = scan.merge(cal, on=["best_stage", "best_bucket"], how="left")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.output, index=False)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

