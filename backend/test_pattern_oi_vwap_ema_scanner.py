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
    _gate4_metrics,
    _normalize_symbol_token,
    _preflight_dhan_token,
    _seconds_until_first_closed_scan,
    _strategy_gate_summary,
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

    def test_gate4_uses_pcr_shift_bands_and_gate3_is_disabled_by_default(self):
        class OptionChain:
            pcr_intraday = 1.12
            pcr_intraday_past = 1.0

        metrics = _gate4_metrics(OptionChain())
        self.assertTrue(metrics["bullish_gate4"])
        self.assertFalse(metrics["bearish_gate4"])
        self.assertEqual(metrics["pcr_shift_pct"], 12.0)
        self.assertIn("bullish confirmation band", metrics["reason"])

        summary = _strategy_gate_summary(["Double Bottom"], True, False, True, direction="BULLISH")
        self.assertTrue(summary["strategy_pass"])
        self.assertTrue(summary["gate3_pass"])
        self.assertFalse(summary["gate3_enabled"])

    def test_gate4_bearish_shift_band_is_supported(self):
        class OptionChain:
            pcr_intraday = 0.94
            pcr_intraday_past = 1.0

        metrics = _gate4_metrics(OptionChain())
        self.assertFalse(metrics["bullish_gate4"])
        self.assertTrue(metrics["bearish_gate4"])
        self.assertEqual(metrics["pcr_shift_pct"], -6.0)
        self.assertIn("bearish confirmation band", metrics["reason"])

    def test_powerindia_symbol_normalizes_to_powergrid(self):
        self.assertEqual(_normalize_symbol_token("POWERINDIA"), "POWERGRID")
        self.assertEqual(_normalize_symbol_token("POWERINDIA.NS"), "POWERGRID")

    def test_preflight_no_history_warns_instead_of_exiting(self):
        class Client:
            def fetch_equity_history(self, *args, **kwargs):
                raise RuntimeError("No Dhan intraday history returned for POWERGRID")

        with patch("builtins.print") as print_mock:
            _preflight_dhan_token(Client(), "POWERINDIA")

        printed = " ".join(" ".join(map(str, call.args)) for call in print_mock.call_args_list)
        self.assertIn("Dhan token validation warning for POWERINDIA", printed)

    def test_first_open_scan_waits_until_first_closed_boundary(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime(2026, 6, 16, 9, 15, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
        delay = _seconds_until_first_closed_scan(now, buffer_seconds=1.5)
        self.assertGreater(delay, 800)
        self.assertLess(delay, 900)

    def test_first_open_scan_not_delayed_later_in_session(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime(2026, 6, 16, 10, 5, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        delay = _seconds_until_first_closed_scan(now, buffer_seconds=1.5)
        self.assertEqual(delay, 0.0)


if __name__ == "__main__":
    unittest.main()
