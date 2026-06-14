import os
import sys
import time
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = ROOT / "backend" / "start_live.py"
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_HOST = os.environ.get("FRONTEND_HOST", "127.0.0.1")
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "8000"))


def _spawn(cmd, cwd):
    return subprocess.Popen(cmd, cwd=str(cwd), env=os.environ.copy())


def main():
    if not START_SCRIPT.exists():
        print(f"[LIVE] Missing: {START_SCRIPT}")
        return 1
    if not FRONTEND_DIR.exists():
        print(f"[LIVE] Missing: {FRONTEND_DIR}")
        return 1

    start_proc = _spawn([sys.executable, str(START_SCRIPT)], ROOT)
    web_proc = _spawn(
        [sys.executable, "-m", "http.server", str(FRONTEND_PORT), "--bind", FRONTEND_HOST],
        FRONTEND_DIR,
    )

    print("[LIVE] Started:")
    print(f"  - Data updater + live API + pattern scanner via: {START_SCRIPT}")
    print(f"  - Frontend server: http://{FRONTEND_HOST}:{FRONTEND_PORT}/index.html")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            if start_proc.poll() is not None:
                print("[LIVE] start_live.py exited. Stopping...")
                break
            if web_proc.poll() is not None:
                print("[LIVE] frontend server exited. Stopping...")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (web_proc, start_proc):
            if proc.poll() is None:
                proc.terminate()
        time.sleep(0.5)
        for proc in (web_proc, start_proc):
            if proc.poll() is None:
                proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
