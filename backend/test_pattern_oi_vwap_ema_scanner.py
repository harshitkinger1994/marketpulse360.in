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
    _format_gate3_group_message,
    _format_gate4_group_message,
    _format_gate4_personal_message,
    _format_gate3_personal_message,
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

    def test_gate12_message_uses_na_when_pattern_is_missing(self):
        class Snapshot:
            close = 511.95
            vwap = 506.41
            ema9 = 503.4
            candle_time_ist = "2026-06-15T15:15:00+05:30"
            option_chain = None

        text = _format_gate12_group_message(
            "TCS",
            Snapshot(),
            {"direction": "BULLISH"},
            strategy_name="Pattern+OI+VWAP/EMA",
            source_note="Universe",
            timeframe_label="15m",
        )

        self.assertIn("Pattern: NA", text)
        self.assertIn("Gate 1: FAIL | Pattern Name: NA", text)
        self.assertIn("Gate 2: FAIL | Close: 511.95 | VWAP: 506.41 | EMA9: 503.4", text)
        self.assertIn("Gate 3: NA", text)
        self.assertIn("Gate 4: NA", text)

    def test_gate3_personal_message_includes_latest_previous_candle_and_oi_labels(self):
        class OptionChain:
            highest_call_oi = 12345
            highest_call_oi_past = 12001
            highest_call_oi_strike = 51500
            highest_put_oi = 9987
            highest_put_oi_past = 10044
            highest_put_oi_strike = 50000

        class Snapshot:
            close = 511.95
            vwap = 506.4142
            ema9 = 503.4009
            candle_time_ist = "2026-06-15T15:15:00+05:30"
            recent_bars = [
                {"dt_ist": "2026-06-15T15:00:00+05:30"},
                {"dt_ist": "2026-06-15T15:15:00+05:30"},
            ]
            option_chain = OptionChain()

        text = _format_gate3_personal_message(
            "TCS",
            Snapshot(),
            {
                "direction": "BULLISH",
                "gate1_pass": True,
                "gate2_pass": True,
                "gate3_pass": True,
                "gate4_pass": True,
                "pattern": "Double Bottom",
                "pcr": 1.12,
                "pcr_prev": 1.0,
                "pcr_shift_pct": 12.0,
                "call_oi_velocity_pct": 2.87,
                "put_oi_velocity_pct": -0.57,
            },
            None,
            strategy_name="Pattern+OI+VWAP/EMA",
            source_note="From 9 EMA strategy pool",
            timeframe_label="75m",
        )

        self.assertIn("Latest Candle: 2026-06-15T15:15:00+05:30 | Previous Candle: 2026-06-15T15:00:00+05:30", text)
        self.assertIn("Gate 1: PASS | Pattern Name: Double Bottom", text)
        self.assertIn("Gate 2: PASS | Close: 511.95 | VWAP: 506.4142 | EMA9: 503.4009", text)
        self.assertIn("Gate 3: PASS | Call OI: 12345 @ 51500 vs 12001 | Change: +2.87% | ↓ Shorts Added", text)
        self.assertIn("Gate 4: PASS | Put OI: 9987 @ 50000 vs 10044 | Change: -0.57% | ↓ Shorts Added | PCR: 1.12 | Prev PCR: 1.0 | PCR Shift: +12.00% | ↑ Long Added", text)
        self.assertIn("Gate 4: PASS | Put OI: 9987 @ 50000 vs 10044 | Change: -0.57% | ↓ Shorts Added", text)

    def test_gate3_group_message_fallback_includes_oi_labels(self):
        class OptionChain:
            highest_call_oi = 12345
            highest_call_oi_past = 12001
            highest_call_oi_strike = 51500
            highest_put_oi = 9987
            highest_put_oi_past = 10044
            highest_put_oi_strike = 50000

        class Snapshot:
            close = 511.95
            vwap = 506.4142
            ema9 = 503.4009
            candle_time_ist = "2026-06-15T15:15:00+05:30"
            recent_bars = [
                {"dt_ist": "2026-06-15T15:00:00+05:30"},
                {"dt_ist": "2026-06-15T15:15:00+05:30"},
            ]
            option_chain = OptionChain()
            rsi14 = 54.2

        with patch("backend.pattern_oi_vwap_ema_scanner.format_single_agent_group_message", side_effect=RuntimeError("boom")):
            text = _format_gate3_group_message(
                "TCS",
                Snapshot(),
                {
                    "direction": "BULLISH",
                    "gate1_pattern": "Double Bottom",
                    "gate1_pass": True,
                    "gate2_pass": True,
                    "gate3_pass": True,
                    "gate4_pass": True,
                    "pcr": 1.12,
                    "pcr_prev": 1.0,
                    "pcr_shift_pct": 12.0,
                    "call_oi_velocity_pct": 2.87,
                    "put_oi_velocity_pct": -0.57,
                },
                None,
                strategy_name="Pattern+OI+VWAP/EMA",
                source_note="From 9 EMA strategy pool",
                timeframe_label="75m",
            )

        self.assertIn("Latest Candle: 2026-06-15T15:15:00+05:30 | Previous Candle: 2026-06-15T15:00:00+05:30", text)
        self.assertIn("Gate 1: PASS | Pattern Name: Double Bottom", text)
        self.assertIn("Gate 2: PASS | Close: 511.95 | VWAP: 506.4142 | EMA9: 503.4009", text)
        self.assertIn("Gate 3: PASS | Call OI: 12345 @ 51500 vs 12001 | Change: +2.87% | ↓ Shorts Added", text)
        self.assertIn("Gate 4: PASS | Put OI: 9987 @ 50000 vs 10044 | Change: -0.57% | ↓ Shorts Added", text)
        self.assertIn("Gate 4: PASS | Put OI: 9987 @ 50000 vs 10044 | Change: -0.57% | ↓ Shorts Added | PCR: 1.12 | Prev PCR: 1.0 | PCR Shift: +12.00% | ↑ Long Added", text)

    def test_gate4_uses_pcr_shift_bands_and_gate3_is_enabled_by_default(self):
        class OptionChain:
            pcr_intraday = 1.12
            pcr_intraday_past = 1.0

        metrics = _gate4_metrics(OptionChain())
        self.assertTrue(metrics["bullish_gate4"])
        self.assertFalse(metrics["bearish_gate4"])
        self.assertEqual(metrics["pcr_shift_pct"], 12.0)
        self.assertIn("bullish confirmation band", metrics["reason"])

        summary = _strategy_gate_summary(["Double Bottom"], True, True, True, direction="BULLISH")
        self.assertTrue(summary["strategy_pass"])
        self.assertTrue(summary["gate3_pass"])
        self.assertTrue(summary["gate3_enabled"])

    def test_gate4_message_helpers_emit_gate4_labels(self):
        class OptionChain:
            highest_call_oi = 12345
            highest_call_oi_past = 12001
            highest_call_oi_strike = 51500
            highest_put_oi = 9987
            highest_put_oi_past = 10044
            highest_put_oi_strike = 50000

        class Snapshot:
            close = 511.95
            vwap = 506.4142
            ema9 = 503.4009
            candle_time_ist = "2026-06-15T15:15:00+05:30"
            recent_bars = [
                {"dt_ist": "2026-06-15T15:00:00+05:30"},
                {"dt_ist": "2026-06-15T15:15:00+05:30"},
            ]
            option_chain = OptionChain()
            rsi14 = 54.2

        with patch("backend.pattern_oi_vwap_ema_scanner.format_single_agent_group_message", side_effect=RuntimeError("boom")):
            group = _format_gate4_group_message(
                "TCS",
                Snapshot(),
                {
                    "direction": "BULLISH",
                    "gate1_pattern": "Double Bottom",
                    "gate1_pass": True,
                    "gate2_pass": True,
                    "gate3_pass": True,
                    "gate4_pass": True,
                    "pcr": 1.12,
                    "pcr_prev": 1.0,
                    "pcr_shift_pct": 12.0,
                    "call_oi_velocity_pct": 2.87,
                    "put_oi_velocity_pct": -0.57,
                },
                None,
                strategy_name="Pattern+OI+VWAP/EMA",
                source_note="Universe",
                timeframe_label="75m",
            )
        personal = _format_gate4_personal_message(
            "TCS",
            Snapshot(),
            {
                "direction": "BULLISH",
                "gate1_pass": True,
                "gate2_pass": True,
                "gate3_pass": True,
                "gate4_pass": True,
                "pattern": "Double Bottom",
                "pcr": 1.12,
                "pcr_prev": 1.0,
                "pcr_shift_pct": 12.0,
                "call_oi_velocity_pct": 2.87,
                "put_oi_velocity_pct": -0.57,
            },
            None,
            strategy_name="Pattern+OI+VWAP/EMA",
            source_note="Universe",
            timeframe_label="75m",
        )

        self.assertIn("Gate 3: PASS | Call OI: 12345 @ 51500 vs 12001 | Change: +2.87% | ↓ Shorts Added", group)
        self.assertIn("Gate 4: PASS | Put OI: 9987 @ 50000 vs 10044 | Change: -0.57% | ↓ Shorts Added | PCR: 1.12 | Prev PCR: 1.0 | PCR Shift: +12.00% | ↑ Long Added", group)
        self.assertIn("Gate 3: PASS | Call OI: 12345 @ 51500 vs 12001 | Change: +2.87% | ↓ Shorts Added", personal)
        self.assertIn("Gate 4: PASS | Put OI: 9987 @ 50000 vs 10044 | Change: -0.57% | ↓ Shorts Added | PCR: 1.12 | Prev PCR: 1.0 | PCR Shift: +12.00% | ↑ Long Added", personal)

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
