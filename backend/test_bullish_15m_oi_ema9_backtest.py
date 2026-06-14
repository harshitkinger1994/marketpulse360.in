import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.bullish_15m_oi_ema9_backtest as pilot


class Bullish15mOiEma9BacktestTests(unittest.TestCase):
    def _make_frame(self, rows=80, start_price=100.0):
        start = datetime(2026, 6, 1, 3, 45, tzinfo=timezone.utc)
        idx = [start + timedelta(minutes=15 * i) for i in range(rows)]
        data = []
        price = start_price
        for i in range(rows):
            if i < rows - 2:
                open_ = price
                close = price + (0.4 if i % 2 == 0 else 0.15)
                high = max(open_, close) + 0.35
                low = min(open_, close) - 0.25
                volume = 1000 + i * 5
                price = close
            elif i == rows - 2:
                open_ = price
                close = price + 8.0
                high = close + 0.8
                low = open_ - 0.3
                volume = 12000 + i * 100
                price = close
            else:
                open_ = price
                close = price + 1.2
                high = close + 0.35
                low = open_ - 0.15
                volume = 13000 + i * 100
                price = close
            data.append((open_, high, low, close, volume))
        df = pd.DataFrame(data, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(idx))
        return df

    def test_strike_interval_for_price(self):
        self.assertEqual(pilot._strike_interval_for_price(250.0), 5.0)
        self.assertEqual(pilot._strike_interval_for_price(883.0), 20.0)
        self.assertEqual(pilot._strike_interval_for_price(1234.0), 50.0)

    def test_proxy_oi_levels(self):
        proxy = pilot._build_proxy_oi_levels(883.0, 875.0, 925.0, 890.0, 888.0, 12.0)
        self.assertEqual(proxy["put_wall"], 860.0)
        self.assertEqual(proxy["call_wall"], 940.0)
        self.assertTrue(proxy["confirmed"])

    def test_classify_signal_breakout_or_pullback(self):
        row = pd.Series(
            {
                "Open": 100.0,
                "High": 115.0,
                "Low": 99.0,
                "Close": 114.0,
                "range_multiple": 3.4,
                "body_multiple": 2.3,
                "volume_multiple": 1.7,
                "atr15": 1.4,
                "ema9_3h": 108.0,
                "ema9_3h_prev": 106.0,
                "session_vwap": 107.0,
                "prior_30_high": 110.0,
                "prior_30_low": 95.0,
                "close_location": 0.93,
                "close_from_low_loc": 0.93,
                "close_from_high_loc": 0.07,
            }
        )
        meta = pilot._classify_signal(row)
        self.assertIsNotNone(meta)
        self.assertIn(meta["setup_type"], {"breakout", "pullback"})
        self.assertGreaterEqual(meta["range_multiple"], 3.0)

    def test_classify_signal_bearish_breakout(self):
        row = pd.Series(
            {
                "Open": 120.0,
                "High": 121.0,
                "Low": 99.0,
                "Close": 100.0,
                "range_multiple": 3.2,
                "body_multiple": 2.4,
                "volume_multiple": 1.8,
                "atr15": 1.4,
                "ema9_3h": 108.0,
                "ema9_3h_prev": 110.0,
                "session_vwap": 107.0,
                "prior_30_high": 125.0,
                "prior_30_low": 101.0,
                "close_location": 0.05,
                "close_from_low_loc": 0.05,
                "close_from_high_loc": 0.95,
            }
        )
        meta = pilot._classify_signal(row, side="bearish")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["side"], "bearish")
        self.assertIn(meta["setup_type"], {"breakout", "pullback"})

    def test_simulate_short_trade(self):
        start = datetime(2026, 6, 1, 3, 45, tzinfo=timezone.utc)
        idx = [start + timedelta(minutes=15 * i) for i in range(3)]
        frame = pd.DataFrame(
            [
                (110.0, 111.0, 109.0, 110.0, 1000),
                (100.0, 101.0, 99.0, 100.0, 1200),
                (95.0, 101.0, 89.0, 90.0, 1400),
            ],
            columns=["Open", "High", "Low", "Close", "Volume"],
            index=pd.DatetimeIndex(idx),
        )
        candidate = {
            "direction": "bearish",
            "signal_time": idx[0],
            "entry_idx": 1,
            "entry_time": idx[1],
            "entry_price": 100.0,
            "stop_price": 105.0,
            "target_price": 90.0,
            "setup_type": "breakout",
            "proxy_oi_pass": True,
        }
        trade = pilot._simulate_trade(frame, candidate, [start.date()], 2)
        self.assertIsNotNone(trade)
        self.assertTrue(trade["win"])
        self.assertAlmostEqual(trade["exit_price"], 90.0, places=4)
        self.assertGreater(trade["r_multiple"], 0.0)

    def test_summary_metrics(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        trades = [
            {
                "setup_type": "breakout",
                "signal_time": now,
                "entry_time": now,
                "exit_time": now + timedelta(minutes=15),
                "r_multiple": 2.0,
                "pnl_pct": 4.0,
                "proxy_oi_pass": True,
            },
            {
                "setup_type": "pullback",
                "signal_time": now + timedelta(days=1),
                "entry_time": now + timedelta(days=1),
                "exit_time": now + timedelta(days=1, minutes=15),
                "r_multiple": -1.0,
                "pnl_pct": -1.0,
                "proxy_oi_pass": False,
            },
        ]
        summary = pilot._summarize(trades, 100000.0, 1.0)
        self.assertEqual(summary["trade_count"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertTrue(math.isfinite(summary["profit_factor"]))
        self.assertGreater(summary["max_drawdown_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
