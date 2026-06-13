import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.auto_trader as trader


class AutoTraderTests(unittest.TestCase):
    def _write_strategy(self, directory, name, payload):
        path = Path(directory) / name
        path.write_text(json.dumps(payload, indent=2))
        return path

    def test_discover_trade_signals_reads_strategy_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = {
                "strategy_id": "india_breakout_retest_on",
                "title": "India Breakout Retest",
                "market": "india",
                "trade_type": "SWING",
                "generated_at": "2026-06-01T09:00:00+05:30",
                "items": [
                    {
                        "ticker": "SBIN",
                        "name": "State Bank of India",
                        "symbol": "SBIN.NS",
                        "side": "BUY",
                        "signal_time": "2026-06-01T09:10:00+05:30",
                        "entry_time": "2026-06-01T09:20:00+05:30",
                        "entry_price": 100.0,
                        "stop_price": 95.0,
                        "target_price": 110.0,
                        "rr_ratio": 2.0,
                        "vol_mult": 3.1,
                        "notify_key": "SBIN|BUY|100",
                        "lines": ["BUY | SBIN breakout", "Entry 100.00 | SL 95.00 | Target 110.00 | RR 2.00"],
                    }
                ],
            }
            self._write_strategy(tmpdir, "india_breakout_retest_on.json", payload)
            signals = trader.discover_trade_signals(strategy_dir=Path(tmpdir))

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["ticker"], "SBIN")
        self.assertEqual(signals[0]["side"], "BUY")
        self.assertEqual(signals[0]["market"], "india")

    def test_select_trade_signals_filters_conflicts(self):
        now = trader._parse_dt("2026-06-01T09:30:00+05:30", market_hint="india")
        signals = [
            {
                "signal_uid": "a",
                "strategy_id": "strat_a",
                "strategy_title": "A",
                "market": "india",
                "trade_type": "SWING",
                "ticker": "SBIN",
                "name": "SBIN",
                "symbol": "SBIN.NS",
                "side": "BUY",
                "signal_dt": now,
                "entry_dt": now,
                "generated_dt": now,
                "signal_time": now.isoformat(),
                "entry_time": now.isoformat(),
                "generated_at": now.isoformat(),
                "entry_price": 100.0,
                "stop_price": 95.0,
                "target_price": 110.0,
                "rr_ratio": 2.0,
                "vol_mult": 3.0,
                "notify_key": "BUY",
                "source_path": "a.json",
                "source_lines": ["BUY"],
            },
            {
                "signal_uid": "b",
                "strategy_id": "strat_b",
                "strategy_title": "B",
                "market": "india",
                "trade_type": "SWING",
                "ticker": "SBIN",
                "name": "SBIN",
                "symbol": "SBIN.NS",
                "side": "SELL",
                "signal_dt": now,
                "entry_dt": now,
                "generated_dt": now,
                "signal_time": now.isoformat(),
                "entry_time": now.isoformat(),
                "generated_at": now.isoformat(),
                "entry_price": 100.0,
                "stop_price": 105.0,
                "target_price": 90.0,
                "rr_ratio": 2.0,
                "vol_mult": 3.0,
                "notify_key": "SELL",
                "source_path": "b.json",
                "source_lines": ["SELL"],
            },
        ]

        selected, skipped, eligible = trader.select_trade_signals(
            signals,
            now=now,
            markets={"india"},
            current_prices=trader.load_current_prices(snapshot_path=Path("/tmp/does-not-exist.json")),
            max_orders=3,
            min_rr=1.5,
            market_hours_only=False,
        )

        self.assertEqual(len(eligible), 2)
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].get("conflict_detected"))
        self.assertEqual(selected[0].get("ticker"), "SBIN")

    def test_build_order_ticket_sizes_by_risk(self):
        signal = {
            "signal_uid": "sig",
            "strategy_id": "india_breakout_retest_on",
            "ticker": "SBIN",
            "symbol": "SBIN.NS",
            "market": "india",
            "side": "BUY",
            "trade_type": "SWING",
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 110.0,
        }
        ticket, reason = trader.build_order_ticket(
            signal,
            capital=100000,
            risk_pct=0.5,
            max_position_pct=25,
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["quantity"], 100)
        self.assertAlmostEqual(ticket["risk_amount"], 500.0, places=2)
        self.assertAlmostEqual(ticket["notional"], 10000.0, places=2)


if __name__ == "__main__":
    unittest.main()
