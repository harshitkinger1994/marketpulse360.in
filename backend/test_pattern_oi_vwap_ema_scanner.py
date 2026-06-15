from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.market_snapshot_store import MarketSnapshotStore
from backend.pattern_oi_vwap_ema_scanner import (
    _alert_signature_text,
    _deliver_telegram_alerts,
    _format_gate12_group_message,
)


class PatternOIVwapEmaScannerTests(unittest.TestCase):
    def test_alert_delivery_dedupes_on_signature(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarketSnapshotStore(base_dir=Path(tmpdir))
            alert = {
                "symbol": "TCS",
                "group_message": "group alert",
                "personal_message": "personal alert",
                "signature": {"symbol": "TCS", "direction": "BULLISH", "pattern": "Hammer"},
                "strategy": {"direction": "BULLISH", "pattern": "Hammer"},
                "source_note": "Universe",
            }

            with patch("backend.pattern_oi_vwap_ema_scanner.MarketSnapshotStore", lambda: store), patch(
                "backend.pattern_oi_vwap_ema_scanner._send_telegram_to", return_value=True
            ) as send_mock:
                _deliver_telegram_alerts(
                    [alert],
                    gate_label="gate12",
                    strategy_key="india_ema9_growth30_on",
                    market="india",
                    interval="15m",
                )
                _deliver_telegram_alerts(
                    [alert],
                    gate_label="gate12",
                    strategy_key="india_ema9_growth30_on",
                    market="india",
                    interval="15m",
                )

            self.assertEqual(send_mock.call_count, 2)
            event_path = store.alert_event_path(
                "india_ema9_growth30_on_gate12",
                "TCS",
                "india",
                "15m",
                _alert_signature_text(alert, "gate12"),
            )
            self.assertTrue(event_path.exists())

    def test_higher_timeframe_message_includes_occurrence_time(self):
        class Snapshot:
            close = 511.95
            vwap = 506.41
            ema9 = 503.4
            candle_time_ist = "2026-06-15T14:15:00+05:30"
            rsi14 = 54.2
            option_chain = None

        text = _format_gate12_group_message(
            "TCS",
            Snapshot(),
            {"direction": "BULLISH", "pattern": "Double Bottom"},
            strategy_name="Pattern+OI+VWAP/EMA",
            source_note="Universe",
            timeframe_label="75m",
            higher_timeframe_context={
                "timeframe": "3H",
                "pattern": "Bearish Engulfing",
                "direction": "BEARISH",
                "candle_time_ist": "2026-06-15T12:15:00+05:30",
            },
        )

        self.assertIn("Higher TF 3H: Bearish Engulfing | When: 2026-06-15T12:15:00+05:30 | Direction: BEARISH", text)


if __name__ == "__main__":
    unittest.main()
