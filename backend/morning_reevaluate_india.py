import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import pytz
import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agentic_pipeline import format_single_agent_group_message, run_single_agent_quant_terminal


def _load_env_file(path: Path):
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


_load_env_file(ROOT / "backend" / ".env")


IST = pytz.timezone("Asia/Kolkata")

QUEUE_PATH = ROOT / "backend" / "data" / "india_morning_reeval_queue.json"
SENT_PATH = ROOT / "backend" / "data" / "india_morning_reeval_sent.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_TRADE_CHAT_ID = (
    os.environ.get("TELEGRAM_TRADE_CHAT_ID")
    or os.environ.get("TELEGRAM_CHAT_ID")
    or ""
).strip()


def _safe_read_json(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text())
    except Exception:
        return default


def _safe_write_json(path: Path, data) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return True
    except Exception:
        return False


def _prev_business_day(d):
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


def _send_telegram_trade(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_TRADE_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_TRADE_CHAT_ID, "text": message},
            timeout=12,
        )
        return resp.status_code == 200
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="", help="YYYY-MM-DD override (defaults to previous working day IST)")
    ap.add_argument("--dry-run", action="store_true", help="Print outputs; do not send to Telegram")
    args = ap.parse_args()

    if args.day:
        day = args.day.strip()
    else:
        today_ist = datetime.now(IST).date()
        day = _prev_business_day(today_ist).isoformat()

    queue = _safe_read_json(QUEUE_PATH, default={})
    sent = _safe_read_json(SENT_PATH, default={})
    if not isinstance(queue, dict):
        queue = {}
    if not isinstance(sent, dict):
        sent = {}

    bucket = queue.get(day) or {}
    if not isinstance(bucket, dict) or not bucket:
        return 0

    already = set(sent.get(day) or [])
    to_send = [(k, v) for k, v in bucket.items() if k and k not in already and isinstance(v, dict)]

    if not to_send:
        return 0

    sent_keys = []
    for trade_key, rec in to_send:
        ticker = str(rec.get("ticker") or "").strip().upper() or "UNKNOWN"
        side = str(rec.get("side") or "").strip().upper()
        signal_time = str(rec.get("signal_time") or "").strip()
        compact_line = str(rec.get("compact_line") or "").strip()
        if not compact_line:
            compact_line = f"INDIA | {ticker} | {side} | {signal_time}".strip()
        terminal_result = run_single_agent_quant_terminal(ticker, strategy_item=rec)
        brief = format_single_agent_group_message(
            compact_line,
            terminal_result,
            market="INDIA",
            strategy_context={
                "title": "India Morning Re-Evaluation",
                "id": "india_morning_reeval",
                "mode": "RE-EVAL",
                "market": "INDIA",
                "trade_type": "SWING",
                "selection": "queued prior-day INDIA trades",
                "freshness": "next-working-day review",
                "filters": "Auto-queued from prior India trade alerts",
            },
        )
        if not brief:
            continue
        if args.dry_run:
            print(brief)
            print()
            sent_keys.append(trade_key)
            continue
        ok = _send_telegram_trade(brief)
        if ok:
            sent_keys.append(trade_key)

    if sent_keys:
        new_list = list(already.union(sent_keys))
        sent[day] = sorted(new_list)
        _safe_write_json(SENT_PATH, sent)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
