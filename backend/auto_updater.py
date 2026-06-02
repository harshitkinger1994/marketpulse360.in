import os
import sys
import time
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "backend" / "run_all.py"
# Default to 5 minutes. A 1s loop makes the site look like a "clock" and wastes CPU/network.
INTERVAL_SEC = int(os.environ.get("UPDATE_INTERVAL_SEC", "300"))
BACKOFF_SEC = int(os.environ.get("UPDATE_FAIL_BACKOFF_SEC", "60"))


def main():
    if not RUN_SCRIPT.exists():
        print(f"[AUTO] Missing script: {RUN_SCRIPT}")
        return 1

    while True:
        start = time.perf_counter()
        rc = subprocess.run(
            [sys.executable, str(RUN_SCRIPT)],
            cwd=str(ROOT),
            check=False,
        ).returncode
        elapsed = time.perf_counter() - start
        print(f"[AUTO] run_all exit {rc} in {elapsed:.2f}s")
        interval = max(0, INTERVAL_SEC)
        sleep_for = interval if rc == 0 else max(interval, BACKOFF_SEC)
        if sleep_for > 0:
            print(f"[AUTO] next run in {sleep_for}s")
            time.sleep(sleep_for)


if __name__ == "__main__":
    raise SystemExit(main())
