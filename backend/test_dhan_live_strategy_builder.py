import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytz

import backend.dhan_live_strategy_builder as builder


IST = pytz.timezone("Asia/Kolkata")


class DhanLiveStrategyBuilderTests(unittest.TestCase):
    def test_build_strategy_payload_keeps_current_day_items(self):
        now_ist = datetime.now(IST)
        signal_time = now_ist.astimezone(timezone.utc).isoformat()
        entry_time = (now_ist + pd.Timedelta(minutes=15)).astimezone(timezone.utc).isoformat()
        fake_report = {
            "symbol": "TITAN",
            "data_source": {"source": "dhan"},
            "summary": {"signal_count": 1},
            "trade_samples": [
                {
                    "signal_time": signal_time,
                    "entry_time": entry_time,
                    "direction": "bullish",
                    "setup_type": "breakout",
                    "entry_price": 100.0,
                    "stop_price": 95.0,
                    "target_price": 110.0,
                    "risk": 5.0,
                    "reward": 10.0,
                    "volume_multiple": 2.5,
                    "reason": "Live Dhan breakout signal",
                }
            ],
        }

        with patch.object(builder, "_run_pilot", return_value=fake_report):
            payload = builder.build_strategy_payload(["TITAN"], side="bullish")

        self.assertEqual(payload["strategy_id"], "india_dhan_ema9_growth30_on")
        self.assertEqual(payload["market"], "india")
        self.assertEqual(payload["trade_type"], "INTRADAY")
        self.assertEqual(payload["counts"]["assets"], 1)
        self.assertEqual(payload["counts"]["signals_total"], 1)
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["ticker"], "TITAN")
        self.assertEqual(item["side"], "BUY")
        self.assertIn("TITAN", item["notify_key"])
        self.assertGreaterEqual(len(item["lines"]), 2)
        self.assertIn("BUY | TITAN breakout", item["lines"][0])


if __name__ == "__main__":
    unittest.main()
