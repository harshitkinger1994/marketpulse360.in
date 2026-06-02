import os
import sys
import time
import signal
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = ROOT / "backend" / "live_server.py"
UPDATER_SCRIPT = ROOT / "backend" / "auto_updater.py"


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
    if not LIVE_SCRIPT.exists() or not UPDATER_SCRIPT.exists():
        print("[START] Missing live_server.py or auto_updater.py")
        return 1

    live = _spawn(LIVE_SCRIPT)
    updater = _spawn(UPDATER_SCRIPT)

    print("[START] Live server + auto updater running. Press Ctrl+C to stop.")

    try:
        while True:
            if live.poll() is not None:
                print("[START] live_server.py exited. Stopping...")
                break
            if updater.poll() is not None:
                print("[START] auto_updater.py exited. Stopping...")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (updater, live):
            if proc.poll() is None:
                proc.terminate()
        time.sleep(0.5)
        for proc in (updater, live):
            if proc.poll() is None:
                proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
