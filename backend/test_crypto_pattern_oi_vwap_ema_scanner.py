from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.crypto_pattern_oi_vwap_ema_scanner import scan_once
from backend.market_snapshot_store import MarketSnapshotStore


class CryptoPatternScannerTests(unittest.TestCase):
    def test_scan_once_reads_crypto_store_and_emits_gate12_alert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarketSnapshotStore(base_dir=Path(tmpdir))
            frame = pd.DataFrame(
                {
                    "open": [100.0, 101.0, 102.0],
                    "high": [101.5, 102.5, 103.0],
                    "low": [99.5, 100.5, 101.0],
                    "close": [101.0, 102.0, 102.8],
                    "volume": [1000, 1200, 1300],
                },
                index=pd.to_datetime(
                    [
                        "2026-06-15T09:15:00Z",
                        "2026-06-15T09:30:00Z",
                        "2026-06-15T09:45:00Z",
                    ]
                ),
            )
            store.write_candle_history(
                timeframe="15m",
                symbol="BTC",
                market="crypto",
                interval="15m",
                frame=frame,
                retention_days=120,
            )

            with patch("backend.crypto_pattern_oi_vwap_ema_scanner.MarketSnapshotStore", return_value=store), patch(
                "backend.crypto_pattern_oi_vwap_ema_scanner._evaluate_strategy",
                return_value={
                    "strategy_pass": False,
                    "pattern": "Bullish Engulfing",
                    "patterns": ["Bullish Engulfing"],
                    "gate1_pass": True,
                    "gate2_pass": True,
                    "gate3_pass": False,
                    "gate4_pass": False,
                    "entry": 102.8,
                    "stop_loss": 100.5,
                    "target": None,
                    "pcr": None,
                    "pcr_prev": None,
                    "put_oi_velocity_pct": None,
                    "call_oi_velocity_pct": None,
                    "prev4_close": 101.0,
                    "direction": "BULLISH",
                    "close": 102.8,
                    "high": 103.0,
                    "volume_avg": 1100.0,
                    "body_size": 1.0,
                    "body_avg_40": 0.2,
                    "body_avg_14": 0.2,
                    "is_big_candle": True,
                    "score": 90,
                },
            ), patch("backend.crypto_pattern_oi_vwap_ema_scanner._deliver_telegram_alerts") as deliver_mock:
                summary = scan_once(symbols=["BTC"])

            self.assertEqual(summary["symbols"], 1)
            self.assertEqual(summary["read"], 1)
            self.assertEqual(summary["alerts"], 1)
            deliver_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
