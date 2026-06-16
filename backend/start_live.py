import os
import sys
import time
import signal
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = ROOT / "backend" / "live_server.py"
UPDATER_SCRIPT = ROOT / "backend" / "auto_updater.py"
UPDATER_ENABLED = os.environ.get("AUTO_UPDATER_ENABLED", "1").strip() == "1"


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
    if missing:
        print(f"[START] Missing script(s): {', '.join(missing)}")
        return 1

    live = _spawn(LIVE_SCRIPT)
    updater = _spawn(UPDATER_SCRIPT) if UPDATER_ENABLED else None

    print("[START] Live server" + (" + auto updater" if UPDATER_ENABLED else "") + " running. Press Ctrl+C to stop.")
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
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (updater, live):
            if proc is None:
                continue
            if proc.poll() is None:
                proc.terminate()
        time.sleep(0.5)
        for proc in (updater, live):
            if proc is None:
                continue
            if proc.poll() is None:
                proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
