from __future__ import annotations

from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pandas as pd


IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)

TIMEFRAME_RULES = {
    "75m": "75min",
    "3h": "3h",
    "4h": "4h",
    "daily": "1d",
    "weekly": "W-FRI",
    "monthly": "ME",
}

HIGHER_TIMEFRAME_CONTEXT = {
    "15m": "75m",
    "75m": "3h",
    "3h": "4h",
    "4h": "daily",
    "daily": "weekly",
    "weekly": "monthly",
}


def normalize_timeframe(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "15m"
    return text


def higher_timeframe_for(interval: str | None) -> str | None:
    return HIGHER_TIMEFRAME_CONTEXT.get(normalize_timeframe(interval))


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "dt_utc" not in work.columns:
        work = work.reset_index()
        if "index" in work.columns and "dt_utc" not in work.columns:
            work = work.rename(columns={"index": "dt_utc"})
    if "dt_utc" not in work.columns:
        return pd.DataFrame()
    work["dt_utc"] = pd.to_datetime(work["dt_utc"], utc=True, errors="coerce")
    work = work.dropna(subset=["dt_utc"]).sort_values("dt_utc")
    if work.empty:
        return pd.DataFrame()
    if "dt_ist" not in work.columns:
        work["dt_ist"] = work["dt_utc"].dt.tz_convert(IST)
    else:
        work["dt_ist"] = pd.to_datetime(work["dt_ist"], errors="coerce")
        if work["dt_ist"].dt.tz is None:
            work["dt_ist"] = work["dt_ist"].dt.tz_localize(IST)
        else:
            work["dt_ist"] = work["dt_ist"].dt.tz_convert(IST)
    rename_map = {}
    for src, dst in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"), ("Volume", "volume")):
        if src in work.columns:
            rename_map[src] = dst
    work = work.rename(columns=rename_map)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in work.columns:
            work[col] = None
    for col in ("open", "high", "low", "close", "volume"):
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["open", "high", "low", "close"])
    if work.empty:
        return pd.DataFrame()
    return work[["dt_utc", "dt_ist", "open", "high", "low", "close", "volume"]].copy()


def resample_ohlcv(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    work = _normalize_frame(frame)
    if work.empty:
        return pd.DataFrame()
    rule = TIMEFRAME_RULES.get(normalize_timeframe(timeframe))
    if not rule:
        return pd.DataFrame()
    if rule == "1d":
        return work.copy()
    indexed = work.set_index("dt_ist")
    is_calendar_frame = rule in {"W-FRI", "ME"}
    resampled = (
        indexed.resample(
            rule,
            label="right",
            closed="right",
            origin="start_day",
            offset=pd.Timedelta(hours=MARKET_OPEN.hour, minutes=MARKET_OPEN.minute),
        )
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
    )
    if resampled.empty:
        return pd.DataFrame()
    if not is_calendar_frame:
        resampled = resampled.loc[
            (resampled.index.time >= MARKET_OPEN) & (resampled.index.time <= MARKET_CLOSE)
        ].copy()
        if resampled.empty:
            return pd.DataFrame()
    resampled["dt_ist"] = pd.to_datetime(resampled.index).tz_convert(IST)
    resampled["dt_utc"] = resampled["dt_ist"].dt.tz_convert("UTC")
    return resampled.reset_index(drop=True)[["dt_utc", "dt_ist", "open", "high", "low", "close", "volume"]]


def derive_custom_intraday(base_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    work = _normalize_frame(base_df)
    if work.empty:
        return {"75m": pd.DataFrame(), "3h": pd.DataFrame(), "4h": pd.DataFrame()}
    return {
        "75m": resample_ohlcv(work, "75m"),
        "3h": resample_ohlcv(work, "3h"),
        "4h": resample_ohlcv(work, "4h"),
    }


def derive_macro_timeframes(daily_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    work = _normalize_frame(daily_df)
    if work.empty:
        return {"weekly": pd.DataFrame(), "monthly": pd.DataFrame()}
    return {
        "weekly": resample_ohlcv(work, "weekly"),
        "monthly": resample_ohlcv(work, "monthly"),
    }
