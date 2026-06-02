import os
import sys
import time
import subprocess
from pathlib import Path
import requests


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "backend" / ".env"
SWING_SCRIPT = ROOT / "strategies" / "reliance_open_close.py"
DAILY_SCRIPT = ROOT / "backend" / "daily_run.py"
LOG_DIR = ROOT / "backend" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _load_env_file(path):
    if not path.exists():
        return
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


_load_env_file(ENV_PATH)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # Back-compat
TELEGRAM_STATUS_CHAT_ID = (
    os.environ.get("TELEGRAM_STATUS_CHAT_ID")
    or os.environ.get("TELEGRAM_PERSONAL_CHAT_ID")
    or TELEGRAM_CHAT_ID
)


def _send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_STATUS_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_STATUS_CHAT_ID, "text": message},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _run(script_path, name, extra_env=None):
    if not script_path.exists():
        print(f"[RUN_ALL] Missing {name} script: {script_path}")
        _send_telegram(f"[RUN_ALL] Missing {name} script: {script_path}")
        return 1
    log_path = LOG_DIR / f"{name}.log"
    with open(log_path, "a") as f:
        f.write(f"\n[RUN_ALL] {name} started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.flush()
        rc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            env={**os.environ, **(extra_env or {})},
            stdout=f,
            stderr=f,
            check=False,
        ).returncode
        f.write(f"[RUN_ALL] {name} exit code: {rc}\n")
    if rc != 0:
        print(f"[RUN_ALL] {name} failed. See {log_path}")
        _send_telegram(f"[RUN_ALL] {name} failed. Check log: {log_path}")
    return rc


def main():
    start = time.perf_counter()
    exit_code = 0
    # Avoid duplicate Telegram alerts; daily_run sends centralized strategy notifications.
    swing_rc = _run(SWING_SCRIPT, "swing", extra_env={"TELEGRAM_NOTIFICATIONS": "0"})
    if swing_rc != 0:
        exit_code = swing_rc
    daily_rc = _run(DAILY_SCRIPT, "daily")
    if daily_rc != 0 and exit_code == 0:
        exit_code = daily_rc
    elapsed = time.perf_counter() - start
    print(f"[RUN_ALL] Total execution time: {elapsed:.2f}s")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
