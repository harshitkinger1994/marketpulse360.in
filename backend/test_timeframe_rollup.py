from __future__ import annotations

import unittest

import pandas as pd

from backend.timeframe_rollup import resample_ohlcv, higher_timeframe_for


class TimeframeRollupTests(unittest.TestCase):
    def test_higher_timeframe_mapping_starts_with_75m(self):
        self.assertEqual(higher_timeframe_for("15m"), "75m")
        self.assertEqual(higher_timeframe_for("75m"), "3h")
        self.assertEqual(higher_timeframe_for("3h"), "4h")

    def test_resample_75m_uses_15m_source_bars(self):
        frame = pd.DataFrame(
            {
                "dt_utc": pd.to_datetime(
                    [
                        "2026-06-15T03:45:00Z",
                        "2026-06-15T04:00:00Z",
                        "2026-06-15T04:15:00Z",
                        "2026-06-15T04:30:00Z",
                        "2026-06-15T04:45:00Z",
                    ]
                ),
                "open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "high": [101.0, 102.0, 103.0, 104.0, 105.0],
                "low": [99.5, 100.5, 101.5, 102.5, 103.5],
                "close": [100.5, 101.5, 102.5, 103.5, 104.5],
                "volume": [100, 110, 120, 130, 140],
            }
        )
        rolled = resample_ohlcv(frame, "75m")
        self.assertFalse(rolled.empty)
        self.assertIn("dt_utc", rolled.columns)
        self.assertIn("dt_ist", rolled.columns)

    def test_resample_3h_uses_15m_source_bars(self):
        frame = pd.DataFrame(
            {
                "dt_utc": pd.to_datetime(
                    [
                        "2026-06-15T03:45:00Z",
                        "2026-06-15T04:00:00Z",
                        "2026-06-15T04:15:00Z",
                        "2026-06-15T04:30:00Z",
                        "2026-06-15T04:45:00Z",
                        "2026-06-15T05:00:00Z",
                        "2026-06-15T05:15:00Z",
                        "2026-06-15T05:30:00Z",
                        "2026-06-15T05:45:00Z",
                        "2026-06-15T06:00:00Z",
                        "2026-06-15T06:15:00Z",
                        "2026-06-15T06:30:00Z",
                    ]
                ),
                "open": list(range(100, 112)),
                "high": list(range(101, 113)),
                "low": [x - 1 for x in range(100, 112)],
                "close": [x + 0.5 for x in range(100, 112)],
                "volume": [100 + (x * 10) for x in range(12)],
            }
        )
        rolled = resample_ohlcv(frame, "3h")
        self.assertFalse(rolled.empty)
        self.assertIn("dt_utc", rolled.columns)
        self.assertGreaterEqual(len(rolled), 1)


if __name__ == "__main__":
    unittest.main()
