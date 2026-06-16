from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import backend.run_all as run_all


class RunAllTests(unittest.TestCase):
    def test_candle_ingestion_command_includes_expected_arguments(self):
        with patch.object(run_all, "INGESTION_ENABLED", True), patch.object(
            run_all, "INGESTION_SCRIPT", run_all.ROOT / "backend" / "market_candle_ingestion.py"
        ), patch.object(run_all, "_run", return_value=0) as run_mock:
            rc = run_all._run_candle_ingestion()

        self.assertEqual(rc, 0)
        run_mock.assert_called_once()
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs.get("extra_args"), [
            "--universe",
            run_all.INGESTION_UNIVERSE,
            "--market",
            run_all.INGESTION_MARKET,
            "--interval",
            run_all.INGESTION_INTERVAL,
            "--data-range",
            run_all.INGESTION_DATA_RANGE,
        ])

    def test_crypto_scanner_command_is_opt_in(self):
        with patch.object(run_all, "CRYPTO_SCANNER_ENABLED", True), patch.object(
            run_all, "CRYPTO_SCANNER_SCRIPT", run_all.ROOT / "backend" / "crypto_pattern_oi_vwap_ema_scanner.py"
        ), patch.object(run_all, "_run", return_value=0) as run_mock:
            rc = run_all._run_crypto_scanner()

        self.assertEqual(rc, 0)
        run_mock.assert_called_once()
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs.get("extra_args"), [
            "--market",
            "crypto",
            "--interval",
            run_all.os.environ.get("CRYPTO_SCANNER_INTERVAL", "15m").strip() or "15m",
            "--store-timeframe",
            run_all.os.environ.get("CRYPTO_SCANNER_STORE_TIMEFRAME", "15m").strip() or "15m",
        ])

    def test_timeframe_scanner_command_is_opt_in(self):
        script_path = run_all.ROOT / "backend" / "pattern_oi_vwap_ema_scanner_75m.py"
        with patch.object(run_all, "_run", return_value=0) as run_mock:
            rc = run_all._run_tf_scanner(script_path, True, "pattern_oi_vwap_ema_scanner_75m", "75m")

        self.assertEqual(rc, 0)
        run_mock.assert_called_once()
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs.get("extra_args"), ["--once"])

    def test_main_15m_scanner_runs_once_when_due(self):
        with patch.object(run_all, "_tf_due", return_value=(True, "15m:2026-06-16T09:30:00+05:30")), patch.object(
            run_all, "_run", return_value=0
        ) as run_mock, patch.object(run_all, "_mark_tf_complete") as mark_mock:
            rc = run_all._run_main_15m_scanner()

        self.assertEqual(rc, 0)
        run_mock.assert_called_once()
        _, kwargs = run_mock.call_args
        self.assertIn("--once", kwargs.get("extra_args"))
        mark_mock.assert_called_once_with("15m", "15m:2026-06-16T09:30:00+05:30")

    def test_timeframe_bundle_flags_can_be_enabled(self):
        self.assertTrue(run_all._timeframe_enabled("75m", False, True))
        self.assertTrue(run_all._timeframe_enabled("3h", False, True))
        self.assertTrue(run_all._timeframe_enabled("daily", False, True))
        self.assertFalse(run_all._timeframe_enabled("weekly", False, False))


if __name__ == "__main__":
    unittest.main()
