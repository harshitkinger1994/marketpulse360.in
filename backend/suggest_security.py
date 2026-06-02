import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


EMAIL_MAX_LEN = 255
NAME_MAX_LEN = 100
MESSAGE_MAX_LEN = 1000

RATE_LIMIT_WINDOW_SEC = 30
RATE_LIMIT_MAX_PER_WINDOW = 1
RATE_LIMIT_HOURLY_MAX = 5
AUTO_BLOCK_HOURLY_MAX = 20
AUTO_BLOCK_DURATION_SEC = 24 * 3600

BLOCKED_SUBSTRINGS = (
    "<script",
    "javascript:",
    "<iframe",
    "onerror=",
    "onload=",
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_utc_iso(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def clamp_text(value, max_len):
    txt = "" if value is None else str(value)
    txt = txt.strip()
    if len(txt) > max_len:
        txt = txt[:max_len]
    return txt


def is_valid_email(email):
    email = (email or "").strip()
    if not email or len(email) > EMAIL_MAX_LEN:
        return False
    return bool(EMAIL_RE.match(email))


def contains_blocked_payload(*values):
    blob = " ".join(str(v or "") for v in values).lower()
    return any(token in blob for token in BLOCKED_SUBSTRINGS)


def normalize_ip(ip):
    ip = str(ip or "").strip()
    if not ip:
        return ""
    if IPV4_RE.match(ip):
        parts = ip.split(".")
        for p in parts:
            try:
                n = int(p)
            except Exception:
                return ""
            if n < 0 or n > 255:
                return ""
        return ip
    # Minimal IPv6 sanity (accept and log, not validate fully).
    if ":" in ip and len(ip) <= 64:
        return ip
    return ""


def client_ip_from_headers(headers, fallback_ip=""):
    def _first_ip(value):
        if not value:
            return ""
        parts = [p.strip() for p in str(value).split(",") if p.strip()]
        return parts[0] if parts else ""

    ip = _first_ip(headers.get("X-Forwarded-For")) or str(headers.get("X-Real-IP") or "").strip()
    ip = normalize_ip(ip) or normalize_ip(fallback_ip)
    return ip or ""


def safe_telegram_text(value, max_len=3500):
    # Telegram sendMessage defaults to plain text when parse_mode is not set,
    # but we still remove control chars and clamp size.
    txt = "" if value is None else str(value)
    txt = txt.replace("\x00", "")
    txt = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", txt)
    txt = txt.strip()
    if len(txt) > max_len:
        txt = txt[: max_len - 20].rstrip() + "\n\n[TRUNCATED]"
    return txt


@dataclass
class Suggestion:
    name: str
    email: str
    message: str
    page: str
    honeypot: str
    turnstile_token: str


def parse_and_validate_payload(payload):
    name = clamp_text(payload.get("name", ""), NAME_MAX_LEN)
    email = clamp_text(payload.get("email", ""), EMAIL_MAX_LEN)
    message = clamp_text(payload.get("message", ""), MESSAGE_MAX_LEN)
    page = clamp_text(payload.get("page", ""), 32)
    honeypot = clamp_text(payload.get("company", ""), 200)
    turnstile_token = clamp_text(payload.get("turnstile_token", ""), 4096)

    if not name:
        return None, "invalid_name"
    if not is_valid_email(email):
        return None, "invalid_email"
    if not message:
        return None, "empty_message"
    if contains_blocked_payload(name, email, message):
        return None, "blocked_payload"
    if honeypot:
        return None, "honeypot_triggered"
    return Suggestion(
        name=name,
        email=email,
        message=message,
        page=page,
        honeypot=honeypot,
        turnstile_token=turnstile_token,
    ), None


def rate_limit_state(now_ts, recent_timestamps):
    # Returns (allowed, reason)
    recent = [t for t in recent_timestamps if now_ts - t <= 3600]
    last_30 = [t for t in recent if now_ts - t <= RATE_LIMIT_WINDOW_SEC]
    if len(last_30) >= RATE_LIMIT_MAX_PER_WINDOW:
        return False, "rate_limit_30s"
    if len(recent) >= RATE_LIMIT_HOURLY_MAX:
        return False, "rate_limit_1h"
    if len(recent) >= AUTO_BLOCK_HOURLY_MAX:
        return False, "auto_block_threshold"
    return True, None


def auto_block_until(now_ts):
    return datetime.fromtimestamp(now_ts, tz=timezone.utc) + timedelta(seconds=AUTO_BLOCK_DURATION_SEC)

