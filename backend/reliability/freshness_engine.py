from datetime import datetime, timezone, timedelta

DEFAULT_THRESHOLDS = {
    "prices_fast": timedelta(minutes=5),
    "prices_slow": timedelta(minutes=15),
    "events": timedelta(hours=6),
    "news": timedelta(hours=3),
}

def _parse_timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%y %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def freshness_status(last_updated_iso, threshold):
    if not last_updated_iso:
        return {"status": "STALE", "reason": "missing_timestamp"}

    last = _parse_timestamp(last_updated_iso)
    if not last:
        return {"status": "STALE", "reason": "invalid_timestamp"}
    now = datetime.now(timezone.utc)
    age = now - last

    if age <= threshold:
        return {"status": "FRESH", "age_sec": int(age.total_seconds())}
    return {"status": "STALE", "age_sec": int(age.total_seconds())}

def attach_freshness(obj, last_updated_iso, threshold):
    try:
        f = freshness_status(last_updated_iso, threshold)
    except Exception:
        f = {"status": "STALE", "reason": "parse_error"}
    obj["freshness"] = f["status"]
    obj["freshness_meta"] = f
    return obj
