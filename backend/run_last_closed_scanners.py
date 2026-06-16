#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable or str(ROOT / "venv" / "bin" / "python")
DEFAULT_SCAN_WORKERS = os.environ.get("RUN_LAST_CLOSED_SCAN_WORKERS", "3")
DEFAULT_SCAN_BATCH_SIZE = os.environ.get("RUN_LAST_CLOSED_SCAN_BATCH_SIZE", "10")
DEFAULT_OPTION_CHAIN_WORKERS = os.environ.get("RUN_LAST_CLOSED_OPTION_CHAIN_WORKERS", "1")
DEFAULT_INTRADAY_RETRIES = os.environ.get("RUN_LAST_CLOSED_INTRADAY_RETRIES", "2")


def _scanner_cmd(script: str, *, lookback_days: int) -> list[str]:
    return [
        PYTHON,
        str(ROOT / "backend" / script),
        "--once",
        "--latest-only",
        "--nifty-futures",
        "--lookback-days",
        str(lookback_days),
        "--option-lookback-days",
        "2",
        "--setup-alerts",
        "--gate3-alerts",
        "--gate4-alerts",
    ]


def _scanner_cmd_interval(*, interval: str, lookback_days: int) -> list[str]:
    return [
        PYTHON,
        str(ROOT / "backend" / "pattern_oi_vwap_ema_scanner.py"),
        "--interval",
        interval,
        "--once",
        "--latest-only",
        "--nifty-futures",
        "--lookback-days",
        str(lookback_days),
        "--option-lookback-days",
        "2",
        "--setup-alerts",
        "--gate3-alerts",
        "--gate4-alerts",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the 15m, 75m, 3h, 4h, and daily pattern scanners once against the latest closed candle."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands that would run and exit without executing the scanners.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runs = [
        ("15m", _scanner_cmd_interval(interval="15m", lookback_days=30)),
        ("75m", "pattern_oi_vwap_ema_scanner_75m.py", 30),
        ("3h", "pattern_oi_vwap_ema_scanner_3h.py", 60),
        ("4h", "pattern_oi_vwap_ema_scanner_4h.py", 60),
        ("daily", "pattern_oi_vwap_ema_scanner_daily.py", 120),
    ]
    for item in runs:
        if len(item) == 2:
            label, cmd = item
        else:
            label, script, lookback_days = item
            cmd = _scanner_cmd(script, lookback_days=lookback_days)
        print(f"[RUN] {label}: {' '.join(cmd)}", flush=True)
        if args.dry_run:
            continue
        started_at = time.perf_counter()
        env = os.environ.copy()
        env["DHAN_SCAN_WORKERS"] = DEFAULT_SCAN_WORKERS
        env["DHAN_SCAN_BATCH_SIZE"] = DEFAULT_SCAN_BATCH_SIZE
        env["DHAN_OPTION_CHAIN_WORKERS"] = DEFAULT_OPTION_CHAIN_WORKERS
        env["DHAN_INTRADAY_RETRIES"] = DEFAULT_INTRADAY_RETRIES
        proc = subprocess.run(cmd, cwd=str(ROOT), check=False, env=env)
        elapsed = time.perf_counter() - started_at
        if proc.returncode != 0:
            print(f"[FAIL] {label} exited with code {proc.returncode} after {elapsed:.1f}s", flush=True)
            return proc.returncode
        print(f"[OK] {label} in {elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
