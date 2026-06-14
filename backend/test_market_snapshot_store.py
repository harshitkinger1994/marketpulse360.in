from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.market_snapshot_store import MarketSnapshotStore, load_latest_market_snapshot_payload


class MarketSnapshotStoreTests(unittest.TestCase):
    def test_write_and_read_15m_payload_round_trips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarketSnapshotStore(base_dir=Path(tmpdir))
            payload = {
                "generated_at": "2026-06-13T10:00:00+05:30",
                "strategy_name": "Pattern+OI+VWAP/EMA",
                "snapshots": [
                    {
                        "symbol": "TCS",
                        "ticker": "TCS",
                        "interval": "15m",
                        "candle_time_ist": "2026-06-13T10:15:00+05:30",
                        "close": 100.5,
                        "open": 99.0,
                        "high": 101.0,
                        "low": 98.5,
                        "volume": 1200,
                        "strategy": {"pattern": "Hammer", "direction": "BULLISH"},
                        "option_chain": {"pcr_intraday": 1.2, "highest_call_oi": 2000},
                    }
                ],
            }

            parquet_frames: dict[str, pd.DataFrame] = {}

            def fake_write(frame: pd.DataFrame, path: Path) -> None:
                parquet_frames[str(path)] = frame.copy()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            def fake_read(path: Path) -> pd.DataFrame:
                return parquet_frames[str(path)].copy()

            with patch.object(store, "_write_frame_to_parquet", side_effect=fake_write), patch.object(
                store, "_read_frame_from_parquet", side_effect=fake_read
            ):
                latest_path = store.write_payload(payload, timeframe="15m")
                roundtrip = store.read_payload("15m")

            self.assertTrue(str(latest_path).endswith("15_min_center_data_latest.parquet"))
            self.assertIsInstance(roundtrip, dict)
            self.assertIn("TCS", roundtrip["data"])
            row = roundtrip["data"]["TCS"]
            self.assertEqual(row["symbol"], "TCS")
            self.assertEqual(row["close"], 100.5)
            self.assertEqual(row["strategy"]["pattern"], "Hammer")
            self.assertEqual(row["option_chain"]["pcr_intraday"], 1.2)

    def test_dashboard_payload_prefers_data_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarketSnapshotStore(base_dir=Path(tmpdir))
            payload = {
                "generated_at": "2026-06-13T15:30:00+05:30",
                "data": {
                    "NIFTY": {"symbol": "NIFTY", "close": 23412.5, "current_price": 23412.5},
                    "SBIN": {"symbol": "SBIN", "close": 800.0, "current_price": 800.0},
                },
            }
            parquet_frames: dict[str, pd.DataFrame] = {}

            def fake_write(frame: pd.DataFrame, path: Path) -> None:
                parquet_frames[str(path)] = frame.copy()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            def fake_read(path: Path) -> pd.DataFrame:
                return parquet_frames[str(path)].copy()

            with patch.object(store, "_write_frame_to_parquet", side_effect=fake_write), patch.object(
                store, "_read_frame_from_parquet", side_effect=fake_read
            ):
                store.write_payload(payload, timeframe="dashboard")
                roundtrip = store.read_payload("dashboard")

            self.assertIn("NIFTY", roundtrip["data"])
            self.assertEqual(roundtrip["data"]["SBIN"]["close"], 800.0)

    def test_load_latest_market_snapshot_payload_can_be_stubbed(self):
        with patch("backend.market_snapshot_store.MarketSnapshotStore.read_payload", return_value={"data": {"TCS": {"symbol": "TCS"}}}):
            payload = load_latest_market_snapshot_payload(("15m",))
        self.assertEqual(payload["data"]["TCS"]["symbol"], "TCS")


if __name__ == "__main__":
    unittest.main()
