import sqlite3
import time
from datetime import datetime, timezone


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_suggest_tables(conn):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS suggestion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at_utc TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            name TEXT,
            email TEXT,
            message TEXT,
            page TEXT,
            result TEXT NOT NULL,
            reason TEXT,
            turnstile_ok INTEGER
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_suggest_events_ip_time ON suggestion_events(ip, created_at_utc)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ip_blocklist (
            ip TEXT PRIMARY KEY,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            blocked_until_utc TEXT,
            reason TEXT
        )
        """
    )
    conn.commit()


def log_event(conn, *, ip, user_agent, name, email, message, page, result, reason, turnstile_ok):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO suggestion_events(
            created_at_utc, ip, user_agent, name, email, message, page, result, reason, turnstile_ok
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            _utc_now_iso(),
            ip,
            user_agent,
            name,
            email,
            message,
            page,
            result,
            reason,
            1 if turnstile_ok else 0 if turnstile_ok is not None else None,
        ),
    )
    conn.commit()


def get_recent_submission_times(conn, ip, now_ts=None):
    if not ip:
        return []
    if now_ts is None:
        now_ts = time.time()
    cutoff = datetime.fromtimestamp(now_ts - 3600, tz=timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        "SELECT created_at_utc FROM suggestion_events WHERE ip=? AND created_at_utc>=? ORDER BY created_at_utc DESC",
        (ip, cutoff),
    )
    rows = cur.fetchall() or []
    out = []
    for (ts,) in rows:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
            out.append(dt.timestamp())
        except Exception:
            continue
    return out


def get_block_status(conn, ip, now_ts=None):
    if not ip:
        return False, None
    if now_ts is None:
        now_ts = time.time()
    cur = conn.cursor()
    cur.execute("SELECT blocked_until_utc FROM ip_blocklist WHERE ip=?", (ip,))
    row = cur.fetchone()
    if not row:
        return False, None
    blocked_until = row[0]
    if not blocked_until:
        return True, None
    try:
        until_dt = datetime.fromisoformat(str(blocked_until).replace("Z", "+00:00")).astimezone(timezone.utc)
        if until_dt.timestamp() > now_ts:
            return True, until_dt.isoformat()
    except Exception:
        return True, None
    return False, None


def upsert_block(conn, ip, *, blocked_until_utc, reason):
    now_iso = _utc_now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ip_blocklist(ip, created_at_utc, updated_at_utc, blocked_until_utc, reason)
        VALUES(?,?,?,?,?)
        ON CONFLICT(ip) DO UPDATE SET
          updated_at_utc=excluded.updated_at_utc,
          blocked_until_utc=excluded.blocked_until_utc,
          reason=excluded.reason
        """,
        (ip, now_iso, now_iso, blocked_until_utc, reason),
    )
    conn.commit()

