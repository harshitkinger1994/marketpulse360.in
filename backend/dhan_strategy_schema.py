from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_DATA_SOURCE = "DHAN"


REQUIRED_STRATEGY_INPUT_FIELDS = [
    {
        "field": "symbol",
        "source": "Dhan scrip master / strategy universe",
        "required": True,
        "notes": "Tradable symbol name used by the strategy.",
    },
    {
        "field": "security_id",
        "source": "Dhan scrip master",
        "required": True,
        "notes": "Dhan security identifier required for chart lookup.",
    },
    {
        "field": "exchange_segment",
        "source": "Dhan scrip master",
        "required": True,
        "notes": "Exchange routing segment such as NSE_EQ, NSE_FNO or MCX_COMM.",
    },
    {
        "field": "instrument",
        "source": "Dhan scrip master",
        "required": True,
        "notes": "Instrument type used by the Dhan charts endpoint.",
    },
    {
        "field": "market",
        "source": "Strategy selection",
        "required": True,
        "notes": "India, global, commodities, or crypto.",
    },
    {
        "field": "interval",
        "source": "Strategy selection",
        "required": True,
        "notes": "15m, 1h, 1d, etc.",
    },
    {
        "field": "timestamp",
        "source": "Dhan intraday / daily chart",
        "required": True,
        "notes": "Close timestamp for the candle used by the strategy.",
    },
    {
        "field": "open",
        "source": "Dhan OHLC",
        "required": True,
        "notes": "Candle open price.",
    },
    {
        "field": "high",
        "source": "Dhan OHLC",
        "required": True,
        "notes": "Candle high price.",
    },
    {
        "field": "low",
        "source": "Dhan OHLC",
        "required": True,
        "notes": "Candle low price.",
    },
    {
        "field": "close",
        "source": "Dhan OHLC",
        "required": True,
        "notes": "Candle close price.",
    },
    {
        "field": "volume",
        "source": "Dhan volume / chart payload",
        "required": True,
        "notes": "Candle volume.",
    },
    {
        "field": "vwap",
        "source": "Derived from Dhan OHLCV",
        "required": False,
        "notes": "Intraday session VWAP when the strategy uses it.",
    },
    {
        "field": "ema9",
        "source": "Derived from Dhan candles",
        "required": False,
        "notes": "Primary EMA9 trigger line if the strategy needs it.",
    },
    {
        "field": "rsi14",
        "source": "Derived from Dhan candles",
        "required": False,
        "notes": "Momentum filter.",
    },
    {
        "field": "atr14",
        "source": "Derived from Dhan candles",
        "required": False,
        "notes": "Range / volatility filter.",
    },
    {
        "field": "volume_sma14",
        "source": "Derived from Dhan candles",
        "required": False,
        "notes": "Relative volume filter baseline.",
    },
    {
        "field": "daily_ema9",
        "source": "Derived from Dhan daily candles",
        "required": False,
        "notes": "Higher timeframe trend confirmation.",
    },
    {
        "field": "weekly_ema9",
        "source": "Derived from Dhan weekly candles",
        "required": False,
        "notes": "Higher timeframe trend confirmation.",
    },
    {
        "field": "day_range",
        "source": "Dhan session OHLC",
        "required": False,
        "notes": "Structured session range for UI and risk controls.",
    },
    {
        "field": "call_oi_strike",
        "source": "Dhan options chain",
        "required": False,
        "notes": "Highest call OI strike for option-barrier strategies.",
    },
    {
        "field": "put_oi_strike",
        "source": "Dhan options chain",
        "required": False,
        "notes": "Highest put OI strike for option-barrier strategies.",
    },
    {
        "field": "pcr_ratio",
        "source": "Dhan options chain",
        "required": False,
        "notes": "Put-call ratio when derivatives context is used.",
    },
]


@dataclass(frozen=True)
class DhanStrategyContract:
    symbol: str
    security_id: str | None = None
    exchange_segment: str | None = None
    instrument: str | None = None
    trading_symbol: str | None = None
    display_name: str | None = None
    market: str | None = None
    interval: str | None = None
    data_source: str = DEFAULT_DATA_SOURCE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DhanStrategyInput:
    symbol: str
    market: str
    interval: str
    timestamp: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    security_id: str | None = None
    exchange_segment: str | None = None
    instrument: str | None = None
    trading_symbol: str | None = None
    display_name: str | None = None
    data_source: str = DEFAULT_DATA_SOURCE
    price_source: str = DEFAULT_DATA_SOURCE
    vwap: float | None = None
    ema9: float | None = None
    rsi14: float | None = None
    atr14: float | None = None
    volume_sma14: float | None = None
    daily_ema9: float | None = None
    weekly_ema9: float | None = None
    day_range: dict[str, Any] | None = None
    call_oi_strike: float | None = None
    put_oi_strike: float | None = None
    pcr_ratio: float | None = None
    oi_shift_data: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload.get("extra") is None:
            payload.pop("extra", None)
        return payload


def required_strategy_input_manifest() -> list[dict[str, Any]]:
    return [dict(item) for item in REQUIRED_STRATEGY_INPUT_FIELDS]


def _coerce_float(value: Any) -> float | None:
    try:
        num = float(value)
    except Exception:
        return None
    if pd.isna(num):
        return None
    return float(num)


def _normalize_ts(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    try:
        ts = ts.tz_convert(IST)
    except Exception:
        try:
            ts = ts.tz_localize(IST)
        except Exception:
            return None
    return ts.isoformat()


def normalize_contract_meta(
    contract: dict[str, Any] | None,
    *,
    symbol: str,
    market: str,
    interval: str,
    data_source: str = DEFAULT_DATA_SOURCE,
) -> DhanStrategyContract:
    contract = contract or {}
    sym = str(symbol or "").strip().upper()
    return DhanStrategyContract(
        symbol=sym,
        security_id=str(contract.get("security_id") or contract.get("securityId") or "").strip() or None,
        exchange_segment=str(contract.get("exchange_segment") or contract.get("exchangeSegment") or "").strip() or None,
        instrument=str(contract.get("instrument") or "").strip() or None,
        trading_symbol=str(contract.get("trading_symbol") or contract.get("tradingSymbol") or sym).strip() or sym,
        display_name=str(contract.get("display_name") or contract.get("displayName") or sym).strip() or sym,
        market=str(market or "").strip().lower() or None,
        interval=str(interval or "").strip(),
        data_source=str(data_source or DEFAULT_DATA_SOURCE).strip() or DEFAULT_DATA_SOURCE,
    )


def standardize_dhan_history_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    interval: str,
    contract: dict[str, Any] | None = None,
    price_source: str = DEFAULT_DATA_SOURCE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if frame is None or frame.empty:
        return [], {}

    work = frame.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        dt_col = None
        for candidate in ("dt_utc", "dt_ist", "date", "timestamp", "time", "datetime"):
            if candidate in work.columns:
                dt_col = candidate
                break
        if dt_col is not None:
            work[dt_col] = pd.to_datetime(work[dt_col], errors="coerce", utc=True)
            work = work.dropna(subset=[dt_col]).set_index(dt_col)
    work.index = pd.to_datetime(work.index, errors="coerce", utc=True)
    work = work[~pd.isna(work.index)]
    if work.empty:
        return [], {}

    normalized = normalize_contract_meta(
        contract,
        symbol=symbol,
        market=market,
        interval=interval,
        data_source=price_source,
    )

    col_map = {str(col).strip().lower(): col for col in work.columns}

    def _col(*names: str) -> str | None:
        for name in names:
            key = str(name).strip().lower()
            if key in col_map:
                return col_map[key]
        return None

    open_col = _col("open")
    high_col = _col("high")
    low_col = _col("low")
    close_col = _col("close")
    volume_col = _col("volume")
    out: list[dict[str, Any]] = []
    for ts, row in work.sort_index().iterrows():
        open_px = _coerce_float(row.get(open_col)) if open_col else None
        high_px = _coerce_float(row.get(high_col)) if high_col else None
        low_px = _coerce_float(row.get(low_col)) if low_col else None
        close_px = _coerce_float(row.get(close_col)) if close_col else None
        volume_px = _coerce_float(row.get(volume_col)) if volume_col else None
        if close_px is None:
            continue
        ts_raw = pd.Timestamp(ts)
        if ts_raw.tzinfo is None:
            ts_utc = ts_raw.tz_localize("UTC")
        else:
            ts_utc = ts_raw.tz_convert("UTC")
        ts_ist = ts_utc.tz_convert(IST)
        date_ist = ts_ist.date().isoformat()
        out.append(
            {
                "symbol": normalized.symbol,
                "market": normalized.market,
                "interval": normalized.interval,
                "data_source": normalized.data_source,
                "price_source": price_source,
                "security_id": normalized.security_id,
                "exchange_segment": normalized.exchange_segment,
                "instrument": normalized.instrument,
                "trading_symbol": normalized.trading_symbol,
                "display_name": normalized.display_name,
                "timestamp": ts_ist.isoformat(),
                "date": date_ist,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "volume": volume_px,
            }
        )

    meta = {
        "contract": normalized.to_dict(),
        "required_fields": required_strategy_input_manifest(),
        "schema_version": "dhan_strategy_input_v1",
        "price_source": price_source,
        "market": normalized.market,
        "interval": normalized.interval,
        "symbol": normalized.symbol,
        "row_count": len(out),
    }
    return out, meta


def standardize_dhan_history_frame_from_daily(
    daily_frame: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    interval: str = "1d",
    contract: dict[str, Any] | None = None,
    price_source: str = DEFAULT_DATA_SOURCE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if daily_frame is None or daily_frame.empty:
        return [], {}
    work = daily_frame.copy()
    if "date" not in work.columns:
        work = work.reset_index().rename(columns={"index": "date"})
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date"])
    if work.empty:
        return [], {}

    rows: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        d = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(d):
            continue
        d_ts = pd.Timestamp(d)
        if d_ts.tzinfo is None:
            d_ts = d_ts.tz_localize(IST)
        else:
            d_ts = d_ts.tz_convert(IST)
        rows.append(
            {
                "symbol": str(symbol or "").strip().upper(),
                "market": str(market or "").strip().lower(),
                "interval": str(interval or "").strip(),
                "data_source": DEFAULT_DATA_SOURCE,
                "price_source": price_source,
                "security_id": str((contract or {}).get("security_id") or "").strip() or None,
                "exchange_segment": str((contract or {}).get("exchange_segment") or "").strip() or None,
                "instrument": str((contract or {}).get("instrument") or "").strip() or None,
                "trading_symbol": str((contract or {}).get("trading_symbol") or symbol or "").strip() or None,
                "display_name": str((contract or {}).get("display_name") or symbol or "").strip() or None,
                "timestamp": d_ts.isoformat(),
                "date": d_ts.date().isoformat(),
                "open": _coerce_float(row.get("open")),
                "high": _coerce_float(row.get("high")),
                "low": _coerce_float(row.get("low")),
                "close": _coerce_float(row.get("close")),
                "volume": _coerce_float(row.get("volume")),
            }
        )

    meta = {
        "contract": normalize_contract_meta(contract, symbol=symbol, market=market, interval=interval, data_source=price_source).to_dict(),
        "required_fields": required_strategy_input_manifest(),
        "schema_version": "dhan_strategy_input_v1",
        "price_source": price_source,
        "market": str(market or "").strip().lower(),
        "interval": str(interval or "").strip(),
        "symbol": str(symbol or "").strip().upper(),
        "row_count": len(rows),
    }
    return rows, meta
