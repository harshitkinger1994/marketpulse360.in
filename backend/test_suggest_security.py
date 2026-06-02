import sys
from pathlib import Path
import sqlite3
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.suggest_security import (
    MESSAGE_MAX_LEN,
    NAME_MAX_LEN,
    EMAIL_MAX_LEN,
    parse_and_validate_payload,
    rate_limit_state,
)
from backend.suggest_store import (
    get_block_status,
    get_recent_submission_times,
    init_suggest_tables,
    log_event,
    upsert_block,
)


class SuggestSecurityTests(unittest.TestCase):
    def test_validation_rejects_empty(self):
        s, err = parse_and_validate_payload({})
        self.assertIsNone(s)
        self.assertIn(err, {"invalid_name", "invalid_email", "empty_message"})

    def test_validation_limits(self):
        payload = {
            "name": "A" * (NAME_MAX_LEN + 50),
            "email": ("a" * (EMAIL_MAX_LEN - 6)) + "@x.io",
            "message": "M" * (MESSAGE_MAX_LEN + 50),
            "company": "",
        }
        s, err = parse_and_validate_payload(payload)
        self.assertIsNotNone(s)
        self.assertIsNone(err)
        self.assertEqual(len(s.name), NAME_MAX_LEN)
        self.assertLessEqual(len(s.email), EMAIL_MAX_LEN)
        self.assertEqual(len(s.message), MESSAGE_MAX_LEN)

    def test_blocks_script_payload(self):
        payload = {"name": "n", "email": "a@b.co", "message": "<script>alert(1)</script>", "company": ""}
        s, err = parse_and_validate_payload(payload)
        self.assertIsNone(s)
        self.assertEqual(err, "blocked_payload")

    def test_honeypot(self):
        payload = {"name": "n", "email": "a@b.co", "message": "hello", "company": "x"}
        s, err = parse_and_validate_payload(payload)
        self.assertIsNone(s)
        self.assertEqual(err, "honeypot_triggered")

    def test_rate_limit(self):
        now = time.time()
        allowed, reason = rate_limit_state(now, [now - 5])
        self.assertFalse(allowed)
        self.assertEqual(reason, "rate_limit_30s")


class SuggestStoreTests(unittest.TestCase):
    def _conn(self):
        conn = sqlite3.connect(":memory:")
        init_suggest_tables(conn)
        return conn

    def test_event_logging_and_recent_fetch(self):
        conn = self._conn()
        now = time.time()
        log_event(
            conn,
            ip="1.2.3.4",
            user_agent="ua",
            name="n",
            email="a@b.co",
            message="hello",
            page="india",
            result="accepted",
            reason=None,
            turnstile_ok=True,
        )
        recent = get_recent_submission_times(conn, "1.2.3.4", now_ts=now)
        self.assertTrue(len(recent) >= 1)
        conn.close()

    def test_blocklist(self):
        conn = self._conn()
        upsert_block(conn, "9.9.9.9", blocked_until_utc=None, reason="manual")
        blocked, _ = get_block_status(conn, "9.9.9.9")
        self.assertTrue(blocked)
        conn.close()


if __name__ == "__main__":
    unittest.main()
