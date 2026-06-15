import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "backend" / ".env"
INGESTION_SCRIPT = ROOT / "backend" / "market_candle_ingestion.py"
CRYPTO_SCANNER_SCRIPT = ROOT / "backend" / "crypto_pattern_oi_vwap_ema_scanner.py"
TF75_SCANNER_SCRIPT = ROOT / "backend" / "pattern_oi_vwap_ema_scanner_75m.py"
TF3H_SCANNER_SCRIPT = ROOT / "backend" / "pattern_oi_vwap_ema_scanner_3h.py"
TF4H_SCANNER_SCRIPT = ROOT / "backend" / "pattern_oi_vwap_ema_scanner_4h.py"
TFDAILY_SCANNER_SCRIPT = ROOT / "backend" / "pattern_oi_vwap_ema_scanner_daily.py"
TFWEEKLY_SCANNER_SCRIPT = ROOT / "backend" / "pattern_oi_vwap_ema_scanner_weekly.py"
TFMONTHLY_SCANNER_SCRIPT = ROOT / "backend" / "pattern_oi_vwap_ema_scanner_monthly.py"
SWING_SCRIPT = ROOT / "strategies" / "reliance_open_close.py"
DAILY_SCRIPT = ROOT / "backend" / "daily_run.py"
TRADER_SCRIPT = ROOT / "backend" / "auto_trader.py"
LOG_DIR = ROOT / "backend" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
INGESTION_ENABLED = os.environ.get("CANDLE_INGESTION_ENABLED", "1").strip() == "1"
INGESTION_UNIVERSE = os.environ.get("CANDLE_INGESTION_UNIVERSE", "broad-india").strip() or "broad-india"
INGESTION_MARKET = os.environ.get("CANDLE_INGESTION_MARKET", "india").strip() or "india"
INGESTION_INTERVAL = os.environ.get("CANDLE_INGESTION_INTERVAL", "15m").strip() or "15m"
INGESTION_DATA_RANGE = os.environ.get("CANDLE_INGESTION_DATA_RANGE", "60d").strip() or "60d"
INGESTION_RETENTION_DAYS = os.environ.get("CANDLE_INGESTION_RETENTION_DAYS", "").strip()
CRYPTO_SCANNER_ENABLED = os.environ.get("CRYPTO_SCANNER_ENABLED", "0").strip() == "1"
TF75_SCANNER_ENABLED = os.environ.get("TF75_SCANNER_ENABLED", "0").strip() == "1"
TF3H_SCANNER_ENABLED = os.environ.get("TF3H_SCANNER_ENABLED", "0").strip() == "1"
TF4H_SCANNER_ENABLED = os.environ.get("TF4H_SCANNER_ENABLED", "0").strip() == "1"
TFDAILY_SCANNER_ENABLED = os.environ.get("TFDAILY_SCANNER_ENABLED", "0").strip() == "1"
TFWEEKLY_SCANNER_ENABLED = os.environ.get("TFWEEKLY_SCANNER_ENABLED", "0").strip() == "1"
TFMONTHLY_SCANNER_ENABLED = os.environ.get("TFMONTHLY_SCANNER_ENABLED", "0").strip() == "1"
TF75_ONLY_GROUP_ENABLED = os.environ.get("TF75_ONLY_GROUP_ENABLED", "0").strip() == "1"
TF34_GROUP_ENABLED = os.environ.get("TF34_GROUP_ENABLED", "0").strip() == "1"
TF_DWM_GROUP_ENABLED = os.environ.get("TF_DWM_GROUP_ENABLED", "0").strip() == "1"
AUTO_TRADER_ENABLED = os.environ.get("AUTO_TRADER_ENABLED", "0").strip() == "1"
SWING_AFTER_CLOSE_ENABLED = os.environ.get("SWING_AFTER_CLOSE_ENABLED", "1").strip() == "1"
SWING_AFTER_CLOSE_RUN_HOUR = int(os.environ.get("SWING_AFTER_CLOSE_RUN_HOUR", "20"))
SWING_AFTER_CLOSE_RUN_MINUTE = int(os.environ.get("SWING_AFTER_CLOSE_RUN_MINUTE", "0"))
SWING_AFTER_CLOSE_STATE_PATH = ROOT / "backend" / "cache" / "swing_after_close_state.json"
IST = ZoneInfo("Asia/Kolkata")


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


def _load_json(path):
    try:
        if not path.exists():
            return {}
        import json

        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json(path, payload):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import json

        path.write_text(json.dumps(payload, indent=2))
    except Exception:
        return


def _swing_post_close_window_open(now=None):
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    open_time = now.replace(
        hour=SWING_AFTER_CLOSE_RUN_HOUR,
        minute=SWING_AFTER_CLOSE_RUN_MINUTE,
        second=0,
        microsecond=0,
    )
    return now >= open_time


def _swing_post_close_week_key(now=None):
    now = now or datetime.now(IST)
    return now.date().isoformat()


def _should_run_swing_after_close(now=None):
    if not SWING_AFTER_CLOSE_ENABLED:
        return False
    now = now or datetime.now(IST)
    if not _swing_post_close_window_open(now):
        return False
    state = _load_json(SWING_AFTER_CLOSE_STATE_PATH)
    return str(state.get("last_completed_day") or "") != _swing_post_close_week_key(now)


def _mark_swing_after_close_complete(now=None):
    now = now or datetime.now(IST)
    _save_json(
        SWING_AFTER_CLOSE_STATE_PATH,
        {
            "last_completed_day": _swing_post_close_week_key(now),
            "completed_at": now.astimezone(timezone.utc).isoformat(),
        },
    )


def _run(script_path, name, extra_env=None, extra_args=None):
    if not script_path.exists():
        print(f"[RUN_ALL] Missing {name} script: {script_path}")
        _send_telegram(f"[RUN_ALL] Missing {name} script: {script_path}")
        return 1
    log_path = LOG_DIR / f"{name}.log"
    with open(log_path, "a") as f:
        f.write(f"\n[RUN_ALL] {name} started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.flush()
        rc = subprocess.run(
            [sys.executable, str(script_path), *(extra_args or [])],
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


def _run_candle_ingestion():
    if not INGESTION_ENABLED:
        print("[RUN_ALL] candle ingestion is disabled (CANDLE_INGESTION_ENABLED=0)")
        return 0
    if not INGESTION_SCRIPT.exists():
        print(f"[RUN_ALL] Missing candle ingestion script: {INGESTION_SCRIPT}")
        _send_telegram(f"[RUN_ALL] Missing candle ingestion script: {INGESTION_SCRIPT}")
        return 1
    args = [
        "--universe",
        INGESTION_UNIVERSE,
        "--market",
        INGESTION_MARKET,
        "--interval",
        INGESTION_INTERVAL,
        "--data-range",
        INGESTION_DATA_RANGE,
    ]
    if INGESTION_RETENTION_DAYS:
        args.extend(["--retention-days", INGESTION_RETENTION_DAYS])
    return _run(INGESTION_SCRIPT, "candle_ingestion", extra_env=None, extra_args=args)


def _run_crypto_scanner():
    if not CRYPTO_SCANNER_ENABLED:
        print("[RUN_ALL] crypto scanner is disabled (CRYPTO_SCANNER_ENABLED=0)")
        return 0
    if not CRYPTO_SCANNER_SCRIPT.exists():
        print(f"[RUN_ALL] Missing crypto scanner script: {CRYPTO_SCANNER_SCRIPT}")
        _send_telegram(f"[RUN_ALL] Missing crypto scanner script: {CRYPTO_SCANNER_SCRIPT}")
        return 1
    args = [
        "--market",
        "crypto",
        "--interval",
        os.environ.get("CRYPTO_SCANNER_INTERVAL", "15m").strip() or "15m",
        "--store-timeframe",
        os.environ.get("CRYPTO_SCANNER_STORE_TIMEFRAME", "15m").strip() or "15m",
    ]
    return _run(CRYPTO_SCANNER_SCRIPT, "crypto_scanner", extra_env=None, extra_args=args)


def _run_tf_scanner(script_path, enabled: bool, name: str, label: str):
    if not enabled:
        print(f"[RUN_ALL] {label} scanner is disabled")
        return 0
    if not script_path.exists():
        print(f"[RUN_ALL] Missing {label} scanner script: {script_path}")
        _send_telegram(f"[RUN_ALL] Missing {label} scanner script: {script_path}")
        return 1
    return _run(script_path, name, extra_env=None, extra_args=[])


def _timeframe_enabled(label: str, direct: bool, group: bool) -> bool:
    if direct:
        return True
    return group


def main():
    start = time.perf_counter()
    exit_code = 0
    _run_candle_ingestion()
    tf_scanners = [
        (TF75_SCANNER_SCRIPT, _timeframe_enabled("75m", TF75_SCANNER_ENABLED, TF75_ONLY_GROUP_ENABLED), "pattern_oi_vwap_ema_scanner_75m", "75m"),
        (TF3H_SCANNER_SCRIPT, _timeframe_enabled("3h", TF3H_SCANNER_ENABLED, TF34_GROUP_ENABLED), "pattern_oi_vwap_ema_scanner_3h", "3h"),
        (TF4H_SCANNER_SCRIPT, _timeframe_enabled("4h", TF4H_SCANNER_ENABLED, TF34_GROUP_ENABLED), "pattern_oi_vwap_ema_scanner_4h", "4h"),
        (TFDAILY_SCANNER_SCRIPT, _timeframe_enabled("daily", TFDAILY_SCANNER_ENABLED, TF_DWM_GROUP_ENABLED), "pattern_oi_vwap_ema_scanner_daily", "daily"),
        (TFWEEKLY_SCANNER_SCRIPT, _timeframe_enabled("weekly", TFWEEKLY_SCANNER_ENABLED, TF_DWM_GROUP_ENABLED), "pattern_oi_vwap_ema_scanner_weekly", "weekly"),
        (TFMONTHLY_SCANNER_SCRIPT, _timeframe_enabled("monthly", TFMONTHLY_SCANNER_ENABLED, TF_DWM_GROUP_ENABLED), "pattern_oi_vwap_ema_scanner_monthly", "monthly"),
    ]
    for script_path, enabled, name, label in tf_scanners:
        rc = _run_tf_scanner(script_path, enabled, name, label)
        if rc != 0 and exit_code == 0:
            exit_code = rc
    crypto_rc = _run_crypto_scanner()
    if crypto_rc != 0 and exit_code == 0:
        exit_code = crypto_rc
    # Avoid duplicate Telegram alerts; daily_run sends centralized strategy notifications.
    if _should_run_swing_after_close():
        swing_rc = _run(SWING_SCRIPT, "swing", extra_env={"TELEGRAM_NOTIFICATIONS": "0"})
        if swing_rc != 0:
            exit_code = swing_rc
        else:
            _mark_swing_after_close_complete()
    else:
        print("[RUN_ALL] reliance_open_close.py deferred until post-close window (20:00 IST)")
    daily_rc = _run(DAILY_SCRIPT, "daily")
    if daily_rc != 0 and exit_code == 0:
        exit_code = daily_rc
    if daily_rc == 0 and AUTO_TRADER_ENABLED:
        trader_rc = _run(TRADER_SCRIPT, "trading")
        if trader_rc != 0 and exit_code == 0:
            exit_code = trader_rc
    elif daily_rc == 0:
        print("[RUN_ALL] auto_trader.py is paused (AUTO_TRADER_ENABLED=0)")
    elapsed = time.perf_counter() - start
    print(f"[RUN_ALL] Total execution time: {elapsed:.2f}s")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
