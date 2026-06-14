import os
import sys
import time
import signal
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = ROOT / "backend" / "live_server.py"
UPDATER_SCRIPT = ROOT / "backend" / "auto_updater.py"
SCANNER_SCRIPT = ROOT / "backend" / "pattern_oi_vwap_ema_scanner.py"
SCANNER_ENABLED = os.environ.get("PATTERN_OI_VWAP_EMA_ENABLED", "1").strip() == "1"


def _spawn(script_path, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=str(ROOT),
        env=env,
    )


def main():
    missing = [path.name for path in (LIVE_SCRIPT, UPDATER_SCRIPT) if not path.exists()]
    if SCANNER_ENABLED and not SCANNER_SCRIPT.exists():
        missing.append(SCANNER_SCRIPT.name)
    if missing:
        print(f"[START] Missing script(s): {', '.join(missing)}")
        return 1

    live = _spawn(LIVE_SCRIPT)
    updater = _spawn(UPDATER_SCRIPT)
    scanner = _spawn(SCANNER_SCRIPT) if SCANNER_ENABLED else None

    print("[START] Live server + auto updater" + (" + pattern scanner" if SCANNER_ENABLED else "") + " running. Press Ctrl+C to stop.")

    try:
        while True:
            if live.poll() is not None:
                print("[START] live_server.py exited. Stopping...")
                break
            if updater.poll() is not None:
                print("[START] auto_updater.py exited. Stopping...")
                break
            if scanner is not None and scanner.poll() is not None:
                print("[START] pattern_oi_vwap_ema_scanner.py exited. Stopping...")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (scanner, updater, live):
            if proc is None:
                continue
            if proc.poll() is None:
                proc.terminate()
        time.sleep(0.5)
        for proc in (scanner, updater, live):
            if proc is None:
                continue
            if proc.poll() is None:
                proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
