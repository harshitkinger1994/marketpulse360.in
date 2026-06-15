import io
import os
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import backend.dhan_intraday as dhan
import backend.data_fetcher as data_fetcher


class DhanIntradayTests(unittest.TestCase):
    def test_resolve_equity_contract_candidates_prefers_nse(self):
        frame = pd.DataFrame(
            [
                {"EXCH_ID": "BSE", "SEGMENT": "E", "SECURITY_ID": "2002", "INSTRUMENT": "EQUITY", "UNDERLYING_SYMBOL": "BAJFINANCE", "SYMBOL_NAME": "BAJFINANCE", "DISPLAY_NAME": "BAJFINANCE"},
                {"EXCH_ID": "NSE", "SEGMENT": "E", "SECURITY_ID": "1001", "INSTRUMENT": "EQUITY", "UNDERLYING_SYMBOL": "BAJFINANCE", "SYMBOL_NAME": "BAJFINANCE", "DISPLAY_NAME": "BAJFINANCE"},
                {"EXCH_ID": "NSE", "SEGMENT": "D", "SECURITY_ID": "3003", "INSTRUMENT": "OPTSTK", "UNDERLYING_SYMBOL": "BAJFINANCE", "SYMBOL_NAME": "BAFLOPT", "DISPLAY_NAME": "BAJFINANCE 25 JUN 900 CALL"},
            ]
        )
        with patch.object(dhan, "_fetch_dhan_scrip_master", return_value=frame):
            out = dhan.resolve_equity_contract_candidates("BAJFINANCE")
        self.assertEqual(out[0]["security_id"], "1001")
        self.assertEqual(out[0]["exchange_segment"], "NSE_EQ")
        self.assertEqual(out[0]["instrument"], "EQUITY")
        self.assertEqual(out[1]["security_id"], "2002")
        self.assertEqual(out[1]["exchange_segment"], "BSE_EQ")

    def test_resolve_index_future_contract_candidates_prefers_front_month(self):
        frame = pd.DataFrame(
            [
                {"EXCH_ID": "NSE", "SEGMENT": "D", "SECURITY_ID": "1001", "INSTRUMENT": "FUTIDX", "UNDERLYING_SYMBOL": "NIFTY", "SYMBOL_NAME": "NIFTY", "DISPLAY_NAME": "NIFTY 30 JUN FUT", "SM_EXPIRY_DATE": "30 JUN 2026"},
                {"EXCH_ID": "NSE", "SEGMENT": "D", "SECURITY_ID": "1002", "INSTRUMENT": "FUTIDX", "UNDERLYING_SYMBOL": "NIFTY", "SYMBOL_NAME": "NIFTY", "DISPLAY_NAME": "NIFTY 28 JUL FUT", "SM_EXPIRY_DATE": "28 JUL 2026"},
                {"EXCH_ID": "NSE", "SEGMENT": "D", "SECURITY_ID": "2002", "INSTRUMENT": "FUTIDX", "UNDERLYING_SYMBOL": "BANKNIFTY", "SYMBOL_NAME": "BANKNIFTY", "DISPLAY_NAME": "BANKNIFTY 30 JUN FUT", "SM_EXPIRY_DATE": "30 JUN 2026"},
            ]
        )
        with patch.object(dhan, "_fetch_dhan_scrip_master", return_value=frame):
            out = dhan.resolve_contract_candidates("NIFTY", market="india")
        self.assertEqual(out[0]["security_id"], "1001")
        self.assertEqual(out[0]["instrument"], "FUTIDX")
        self.assertEqual(out[0]["exchange_segment"], "NSE_FNO")

    def test_fetch_intraday_history_normalizes_payload(self):
        payload = {
            "start_Time": [1717224300000, 1717225200000],
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.5, 100.5],
            "close": [101.5, 102.5],
            "volume": [1000, 1100],
        }
        response = Mock()
        response.status_code = 200
        response.content = b"ok"
        response.json.return_value = payload

        with patch.dict(os.environ, {"DHAN_ACCESS_TOKEN": "token", "DHAN_CLIENT_ID": "cid", "DHAN_INTRADAY_CHUNK_DAYS": "1"}), \
             patch.object(dhan, "resolve_contract_candidates", return_value=[{"security_id": "1001", "exchange_segment": "NSE_EQ", "instrument": "EQUITY", "trading_symbol": "BAJFINANCE"}]), \
             patch.object(dhan, "_dhan_request", return_value=response):
            frame, meta = dhan.fetch_intraday_history("BAJFINANCE", interval="15m", data_range="1d")

        self.assertEqual(meta["security_id"], "1001")
        self.assertEqual(meta["exchange_segment"], "NSE_EQ")
        self.assertEqual(list(frame.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(len(frame), 2)
        self.assertTrue(pd.api.types.is_datetime64tz_dtype(frame.index))

    def test_commodity_live_snapshot_prefers_dhan_helper(self):
        idx = pd.to_datetime(["2026-06-12 09:15:00+00:00", "2026-06-12 09:30:00+00:00"], utc=True)
        daily_df = pd.DataFrame(
            {
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.5, 100.5],
                "close": [101.5, 102.5],
                "volume": [1000, 1100],
            },
            index=idx,
        )
        fake_snapshot = {
            "price": 102.5,
            "timestamp": "2026-06-12T09:30:00+00:00",
            "day_range": {
                "open": 100.0,
                "high": 103.0,
                "low": 99.5,
                "current": 102.5,
                "previous_close": 100.0,
                "timestamp": "2026-06-12T09:30:00+00:00",
                "basis": "daily",
                "source": "DHAN_INTRADAY",
            },
            "history": [{"date": "2026-06-12", "close": 102.5}],
        }

        with patch.object(data_fetcher, "_fetch_dhan_commodity_daily_frame", return_value=(daily_df, fake_snapshot)):
            snapshot = data_fetcher.fetch_live_snapshot("GC=F", name="GOLD")
            price, ts = data_fetcher.fetch_live_price("GC=F")

        self.assertEqual(snapshot["price"], 102.5)
        self.assertEqual(snapshot["day_range"]["source"], "DHAN_INTRADAY")
        self.assertEqual(price, 102.5)
        self.assertEqual(ts, "2026-06-12T09:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
