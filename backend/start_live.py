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
UPDATER_ENABLED = os.environ.get("AUTO_UPDATER_ENABLED", "0").strip() == "1"


def _spawn(script_path, extra_env=None, extra_args=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
    )


def main():
    missing = [path.name for path in (LIVE_SCRIPT,) if not path.exists()]
    if UPDATER_ENABLED and not UPDATER_SCRIPT.exists():
        missing.append(UPDATER_SCRIPT.name)
    if SCANNER_ENABLED and not SCANNER_SCRIPT.exists():
        missing.append(SCANNER_SCRIPT.name)
    if missing:
        print(f"[START] Missing script(s): {', '.join(missing)}")
        return 1

    live = _spawn(LIVE_SCRIPT)
    updater = _spawn(UPDATER_SCRIPT) if UPDATER_ENABLED else None
    scanner = _spawn(SCANNER_SCRIPT, extra_args=["--fast-mode", "--fast-strike-window", "1"]) if SCANNER_ENABLED else None
    scanner_restart_count = 0
    scanner_restart_delay = 1.0

    print("[START] Live server" + (" + auto updater" if UPDATER_ENABLED else "") + (" + pattern scanner" if SCANNER_ENABLED else "") + " running. Press Ctrl+C to stop.")
    if not UPDATER_ENABLED:
        print("[START] auto_updater.py is disabled (AUTO_UPDATER_ENABLED=0)")

    try:
        while True:
            if live.poll() is not None:
                print("[START] live_server.py exited. Stopping...")
                break
            if updater is not None and updater.poll() is not None:
                print("[START] auto_updater.py exited. Stopping...")
                break
            if scanner is not None and scanner.poll() is not None:
                code = scanner.returncode
                scanner_restart_count += 1
                print(
                    f"[START] pattern_oi_vwap_ema_scanner.py exited with code {code}; "
                    f"restarting (count={scanner_restart_count})..."
                )
                time.sleep(scanner_restart_delay)
                scanner_restart_delay = min(scanner_restart_delay * 2.0, 60.0)
                scanner = _spawn(SCANNER_SCRIPT, extra_args=["--fast-mode", "--fast-strike-window", "1"])
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
