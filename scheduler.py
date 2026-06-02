import os
import sys
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT = ROOT / "stock_strate" / "reliance_open_close.py"
DEFAULT_LOG = ROOT / "stock_strate" / "logs" / "reliance_open_close.log"

SCRIPT_PATH = Path(os.getenv("TARGET_SCRIPT", str(DEFAULT_SCRIPT)))
LOG_PATH = Path(os.getenv("LOG_PATH", str(DEFAULT_LOG)))
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "15"))
RUN_ONCE = os.getenv("RUN_ONCE", "0") == "1"

def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _sleep_until_next_run(interval_minutes):
    if interval_minutes <= 0:
        time.sleep(60)
        return
    now = datetime.now()
    next_min = (now.minute // interval_minutes + 1) * interval_minutes
    next_hour = now.hour
    next_day = now.date()
    if next_min >= 60:
        next_min = 0
        next_hour += 1
        if next_hour >= 24:
            next_hour = 0
            next_day = now.date() + timedelta(days=1)
    next_run = datetime.combine(next_day, datetime.min.time()).replace(
        hour=next_hour, minute=next_min, second=0, microsecond=0
    )
    sleep_seconds = max(1, (next_run - now).total_seconds())
    time.sleep(sleep_seconds)

def run_once():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"\n==== Run started {_now_str()} ====\n")
        try:
            proc = subprocess.run(
                [sys.executable, str(SCRIPT_PATH)],
                stdout=f,
                stderr=f,
                check=False,
            )
            f.write(f"==== Exit code: {proc.returncode} ====\n")
        except Exception as exc:
            f.write(f"==== Scheduler error: {exc} ====\n")

def main():
    if not SCRIPT_PATH.exists():
        raise SystemExit(f"Script not found: {SCRIPT_PATH}")
    if RUN_ONCE:
        run_once()
        return
    while True:
        run_once()
        _sleep_until_next_run(INTERVAL_MINUTES)

if __name__ == "__main__":
    main()
