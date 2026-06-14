from __future__ import annotations

import unittest

import pandas as pd

from backend.dhan_strategy_schema import (
    normalize_contract_meta,
    required_strategy_input_manifest,
    standardize_dhan_history_frame,
    standardize_dhan_history_frame_from_daily,
)


class DhanStrategySchemaTests(unittest.TestCase):
    def test_required_manifest_includes_core_fields(self):
        manifest = required_strategy_input_manifest()
        fields = {item["field"] for item in manifest}
        self.assertIn("symbol", fields)
        self.assertIn("security_id", fields)
        self.assertIn("open", fields)
        self.assertIn("close", fields)
        self.assertIn("volume", fields)
        self.assertIn("timestamp", fields)

    def test_normalize_contract_meta(self):
        meta = normalize_contract_meta(
            {
                "security_id": "123",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "trading_symbol": "TCS",
                "display_name": "TCS Limited",
            },
            symbol="TCS",
            market="india",
            interval="15m",
        )
        self.assertEqual(meta.symbol, "TCS")
        self.assertEqual(meta.security_id, "123")
        self.assertEqual(meta.exchange_segment, "NSE_EQ")
        self.assertEqual(meta.instrument, "EQUITY")

    def test_standardize_dhan_history_frame_from_daily(self):
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-06-12", "2026-06-13"]),
                "open": [100.0, 102.0],
                "high": [105.0, 106.0],
                "low": [99.0, 101.0],
                "close": [104.0, 105.5],
                "volume": [1000, 1200],
            }
        )
        rows, meta = standardize_dhan_history_frame_from_daily(
            frame,
            symbol="TCS",
            market="india",
            interval="1d",
            contract={"security_id": "123", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
            price_source="DHAN_INTRADAY",
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "TCS")
        self.assertEqual(rows[0]["market"], "india")
        self.assertEqual(rows[0]["open"], 100.0)
        self.assertEqual(rows[1]["close"], 105.5)
        self.assertEqual(meta["schema_version"], "dhan_strategy_input_v1")

    def test_standardize_dhan_history_frame_from_intraday_index(self):
        frame = pd.DataFrame(
            {
                "dt_utc": pd.to_datetime(
                    [
                        "2026-06-12T03:45:00+00:00",
                        "2026-06-12T04:00:00+00:00",
                    ]
                ),
                "open": [100.0, 101.0],
                "high": [101.0, 102.0],
                "low": [99.5, 100.5],
                "close": [100.5, 101.8],
                "volume": [1000, 1100],
            }
        ).set_index("dt_utc")
        rows, meta = standardize_dhan_history_frame(
            frame,
            symbol="TCS",
            market="india",
            interval="15m",
            contract={"security_id": "123", "exchange_segment": "NSE_EQ", "instrument": "EQUITY"},
            price_source="DHAN_INTRADAY",
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["timestamp"].endswith("+05:30"))
        self.assertEqual(rows[0]["price_source"], "DHAN_INTRADAY")
        self.assertEqual(meta["contract"]["symbol"], "TCS")


if __name__ == "__main__":
    unittest.main()
