from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.market_candle_ingestion import fetch_and_store
from backend.market_snapshot_store import MarketSnapshotStore


class MarketCandleIngestionTests(unittest.TestCase):
    def test_strategy_artifact_paths_are_namespaced(self):
        store = MarketSnapshotStore(base_dir=Path("/tmp/test-center-store"))
        signal_path = store.signal_artifact_path("india_ema9_growth30_on", "TCS", "india", "15m")
        feature_path = store.feature_artifact_path("india_ema9_growth30_on", "TCS", "india", "15m")
        alert_path = store.alert_artifact_path("india_ema9_growth30_on", "TCS", "india", "15m")
        self.assertIn("/artifacts/signals/india_ema9_growth30_on/india/15m/TCS.parquet", str(signal_path))
        self.assertIn("/artifacts/features/india_ema9_growth30_on/india/15m/TCS.parquet", str(feature_path))
        self.assertIn("/artifacts/alert_events/india_ema9_growth30_on/india/15m/TCS.parquet", str(alert_path))

    def test_fetch_and_store_writes_raw_candle_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarketSnapshotStore(base_dir=Path(tmpdir))
            frame = pd.DataFrame(
                {
                    "Open": [100.0, 101.0],
                    "High": [101.5, 102.0],
                    "Low": [99.5, 100.5],
                    "Close": [101.0, 101.8],
                    "Volume": [1000, 1200],
                },
                index=pd.to_datetime(
                    [
                        "2026-06-15T09:15:00Z",
                        "2026-06-15T09:30:00Z",
                    ]
                ),
            )
            captured_paths: list[str] = []

            def fake_write(frame_arg: pd.DataFrame, path: Path) -> None:
                captured_paths.append(str(path))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            with patch("backend.market_candle_ingestion._load_broad_india_universe_symbols", return_value=["TCS"]), patch(
                "backend.market_candle_ingestion.fetch_intraday_history", return_value=(frame, {"source": "DHAN"})
            ), patch("backend.market_candle_ingestion.MarketSnapshotStore", return_value=store), patch.object(
                store, "_write_frame_to_parquet", side_effect=fake_write
            ):
                summary = fetch_and_store(
                    universe="broad-india",
                    market="india",
                    interval="15m",
                    data_range="60d",
                    symbols=[],
                    retention_days=120,
                )

            self.assertEqual(summary["symbols"], 1)
            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertTrue(any("15_min_center_data/candle_history/india/15m/TCS.parquet" in p for p in captured_paths))

    def test_fetch_and_store_supports_crypto_universe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarketSnapshotStore(base_dir=Path(tmpdir))
            frame = pd.DataFrame(
                {
                    "Open": [100.0, 101.0],
                    "High": [101.5, 102.0],
                    "Low": [99.5, 100.5],
                    "Close": [101.0, 101.8],
                    "Volume": [1000, 1200],
                },
                index=pd.to_datetime(
                    [
                        "2026-06-15T09:15:00Z",
                        "2026-06-15T09:30:00Z",
                    ]
                ),
            )
            captured_paths: list[str] = []

            def fake_write(frame_arg: pd.DataFrame, path: Path) -> None:
                captured_paths.append(str(path))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            with patch("backend.market_candle_ingestion.fetch_crypto_history", return_value=(frame, {"source": "YFINANCE_CRYPTO"})), patch(
                "backend.market_candle_ingestion.MarketSnapshotStore", return_value=store
            ), patch.object(store, "_write_frame_to_parquet", side_effect=fake_write):
                summary = fetch_and_store(
                    universe="crypto",
                    market="crypto",
                    interval="15m",
                    data_range="60d",
                    symbols=[],
                    retention_days=120,
                )

            self.assertEqual(summary["symbols"], 5)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(summary["written"], 5)
            self.assertTrue(any("15_min_center_data/candle_history/crypto/15m/BTC.parquet" in p for p in captured_paths))

    def test_fetch_and_store_uses_direct_3h_fetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MarketSnapshotStore(base_dir=Path(tmpdir))
            frame = pd.DataFrame(
                {
                    "dt_utc": pd.to_datetime(
                        [
                            "2026-06-15T03:45:00Z",
                            "2026-06-15T06:45:00Z",
                        ]
                    ),
                    "dt_ist": pd.to_datetime(
                        [
                            "2026-06-15T09:15:00+05:30",
                            "2026-06-15T12:15:00+05:30",
                        ]
                    ),
                    "open": [100.0, 110.0],
                    "high": [101.0, 111.0],
                    "low": [99.0, 109.0],
                    "close": [100.5, 110.5],
                    "volume": [1000, 1200],
                }
            )
            captured_paths: list[str] = []

            def fake_write(frame_arg: pd.DataFrame, path: Path) -> None:
                captured_paths.append(str(path))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            with patch("backend.market_candle_ingestion._load_broad_india_universe_symbols", return_value=["TCS"]), patch(
                "backend.market_candle_ingestion.fetch_intraday_history", return_value=(frame, {"source": "DHAN"})
            ), patch(
                "backend.market_candle_ingestion.MarketSnapshotStore", return_value=store
            ), patch.object(store, "_write_frame_to_parquet", side_effect=fake_write):
                summary = fetch_and_store(
                    universe="broad-india",
                    market="india",
                    interval="3h",
                    data_range="60d",
                    symbols=[],
                    retention_days=120,
                )

            self.assertEqual(summary["symbols"], 1)
            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertTrue(any("3h_center_data/candle_history/india/3h/TCS.parquet" in p for p in captured_paths))
            self.assertTrue(any("3h_center_data/candle_history/india/3h/TCS.parquet" in p for p in captured_paths))


if __name__ == "__main__":
    unittest.main()
