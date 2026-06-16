#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional for local smoke tests
    def load_dotenv(*args, **kwargs):  # type: ignore[override]
        return False

from backend.market_snapshot_store import MarketSnapshotStore
from backend.dhan_intraday import fetch_intraday_history
from backend.data_fetcher import CRYPTO, _fetch_dhan_india_daily_frame, fetch_crypto_history
from backend.timeframe_rollup import derive_custom_intraday, derive_macro_timeframes

try:
    from backend.pattern_oi_vwap_ema_scanner import _load_broad_india_universe_symbols
except Exception:  # pragma: no cover - fallback for stripped environments
    _load_broad_india_universe_symbols = None


IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MARKET = "india"
DEFAULT_INTERVAL = "15m"
DEFAULT_DATA_RANGE = "60d"
INTRADAY_DERIVED_TIMEFRAMES = {"75m", "3h", "4h"}
SLOW_DERIVED_TIMEFRAMES = {"weekly", "monthly"}


def _load_env() -> None:
    load_dotenv(ROOT / "backend" / ".env", override=False)


def _parse_symbols(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        symbol = str(token or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def _infer_retention_days(data_range: str, retention_days: int | None) -> int:
    if retention_days is not None and retention_days > 0:
        return retention_days
    text = str(data_range or "").strip().lower()
    if text.endswith("d") and text[:-1].isdigit():
        return max(2 * int(text[:-1]), int(text[:-1]))
    if text.endswith("mo") and text[:-2].isdigit():
        return max(60, 2 * int(text[:-2]) * 30)
    if text.endswith("y") and text[:-1].isdigit():
        return max(365, 2 * int(text[:-1]) * 365)
    return 120


def _daily_frame_to_candles(daily: pd.DataFrame) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    work = daily.copy()
    if "date" not in work.columns:
        work = work.reset_index().rename(columns={"index": "date"})
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date"])
    if work.empty:
        return pd.DataFrame()
    if work["date"].dt.tz is None:
        work["dt_ist"] = work["date"].dt.tz_localize("Asia/Kolkata")
    else:
        work["dt_ist"] = work["date"].dt.tz_convert("Asia/Kolkata")
    work["dt_utc"] = work["dt_ist"].dt.tz_convert("UTC")
    rename_map = {}
    for src, dst in (("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"), ("volume", "volume")):
        if src in work.columns:
            rename_map[src] = dst
    work = work.rename(columns=rename_map)
    keep_cols = ["dt_utc", "dt_ist", "open", "high", "low", "close", "volume"]
    for col in keep_cols:
        if col not in work.columns:
            work[col] = None
    return work[keep_cols].copy()


def _intraday_frame_to_candles(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    if "dt_utc" not in work.columns:
        work = work.reset_index()
        if "index" in work.columns and "dt_utc" not in work.columns:
            work = work.rename(columns={"index": "dt_utc"})
    rename_map = {}
    for src, dst in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"), ("Volume", "volume")):
        if src in work.columns:
            rename_map[src] = dst
    work = work.rename(columns=rename_map)
    if "dt_utc" not in work.columns:
        return pd.DataFrame()
    work["dt_utc"] = pd.to_datetime(work["dt_utc"], utc=True, errors="coerce")
    work = work.dropna(subset=["dt_utc"])
    if work.empty:
        return pd.DataFrame()
    if "dt_ist" not in work.columns:
        work["dt_ist"] = work["dt_utc"].dt.tz_convert(IST)
    keep_cols = ["dt_utc", "dt_ist", "open", "high", "low", "close", "volume"]
    for col in keep_cols:
        if col not in work.columns:
            work[col] = None
    return work[keep_cols].copy()


def _load_15m_history_for_symbol(symbol: str, market: str) -> pd.DataFrame | None:
    try:
        store = MarketSnapshotStore()
        frame = store.read_candle_history("15m", symbol, market, "15m")
        if frame is None or frame.empty:
            return None
        return frame.reset_index()
    except Exception:
        return None


def _derive_75m_from_15m(symbol: str, market: str) -> pd.DataFrame | None:
    base = _load_15m_history_for_symbol(symbol, market)
    if base is None or base.empty:
        return None
    work = derive_custom_intraday(base)
    frame = work.get("75m")
    return frame if frame is not None and not frame.empty else None


def _fetch_direct_60m(symbol: str, market: str, data_range: str) -> pd.DataFrame | None:
    try:
        frame, _meta = fetch_intraday_history(symbol, interval="60m", data_range=data_range, market=market)
        frame = _intraday_frame_to_candles(frame if isinstance(frame, pd.DataFrame) else pd.DataFrame())
        if frame is not None and not frame.empty:
            return frame
    except Exception:
        pass
    return None


def _derive_3h_4h_from_60m(symbol: str, market: str, timeframe: str, data_range: str) -> pd.DataFrame | None:
    base = _fetch_direct_60m(symbol, market, data_range)
    if base is None or base.empty:
        return None
    work = derive_custom_intraday(base)
    frame = work.get(timeframe)
    return frame if frame is not None and not frame.empty else None


def _derive_weekly_monthly_from_daily(symbol: str, market: str) -> pd.DataFrame | None:
    daily, _snapshot = _fetch_dhan_india_daily_frame(symbol)
    frame = _daily_frame_to_candles(daily)
    if frame is None or frame.empty:
        return None
    return frame


def _derive_timeframe_from_sources(symbol: str, market: str, timeframe: str) -> pd.DataFrame | None:
    tf = str(timeframe or "").strip().lower()
    if tf == "75m":
        return _derive_75m_from_15m(symbol, market)
    if tf in {"3h", "4h"}:
        # Dhan historical intraday only exposes up to 60m, so bootstrap 3h/4h from the native 60m source.
        return _derive_3h_4h_from_60m(symbol, market, tf, data_range="60d")
    if tf in {"weekly", "monthly"}:
        daily = _derive_weekly_monthly_from_daily(symbol, market)
        if daily is None or daily.empty:
            return None
        work = derive_macro_timeframes(daily)
        frame = work.get(tf)
        return frame if frame is not None and not frame.empty else None
    return None


def _resolve_universe(universe: str, symbols: list[str]) -> list[str]:
    if symbols:
        return symbols
    key = str(universe or "").strip().lower()
    if key == "crypto":
        loaded = list(CRYPTO.keys())
        if loaded:
            return loaded
    if key == "broad-india" and callable(_load_broad_india_universe_symbols):
        loaded = _load_broad_india_universe_symbols()
        if loaded:
            return loaded
    if key == "nifty50":
        try:
            from backend.data_fetcher import get_nifty50_symbols

            loaded = list(get_nifty50_symbols().keys())
            if loaded:
                return loaded
        except Exception:
            pass
    if key == "manual":
        return []
    if callable(_load_broad_india_universe_symbols):
        loaded = _load_broad_india_universe_symbols()
        if loaded:
            return loaded
    try:
        loaded = list(CRYPTO.keys())
        if loaded and key == "crypto":
            return loaded
    except Exception:
        pass
    try:
        from backend.data_fetcher import get_nifty50_symbols

        return list(get_nifty50_symbols().keys())
    except Exception:
        return []


def fetch_and_store(
    *,
    universe: str,
    market: str,
    interval: str,
    data_range: str,
    symbols: list[str] | None = None,
    retention_days: int | None = None,
) -> dict[str, Any]:
    _load_env()
    store = MarketSnapshotStore()
    resolved_symbols = _resolve_universe(universe, symbols or [])
    if not resolved_symbols:
        return {"symbols": 0, "written": 0, "failed": 0, "messages": ["No symbols to ingest"]}

    now = datetime.now(timezone.utc)
    retention = _infer_retention_days(data_range, retention_days)
    universe_label = str(universe or "").strip().lower()
    market_label = str(market or "").strip().lower()
    effective_market = "crypto" if universe_label == "crypto" or market_label == "crypto" else (market_label or "india")
    written = 0
    failed = 0
    skipped_missing = 0
    messages: list[str] = []
    for raw_symbol in resolved_symbols:
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol:
            continue
        try:
            interval_label = str(interval or "").strip().lower()
            if effective_market == "crypto":
                frame, _meta = fetch_crypto_history(symbol, interval=interval, data_range=data_range)
                frame = _intraday_frame_to_candles(frame if isinstance(frame, pd.DataFrame) else pd.DataFrame())
            elif interval_label == "75m":
                frame, _meta = fetch_intraday_history(symbol, interval=interval, data_range=data_range, market=market)
                frame = _intraday_frame_to_candles(frame if isinstance(frame, pd.DataFrame) else pd.DataFrame())
                if frame is None or frame.empty:
                    frame = _derive_timeframe_from_sources(symbol, effective_market, interval_label)
            elif interval_label == "3h":
                frame = _derive_3h_4h_from_60m(symbol, effective_market, interval_label, data_range)
            elif interval_label == "4h":
                frame = _derive_3h_4h_from_60m(symbol, effective_market, interval_label, data_range)
            elif interval_label in {"weekly", "monthly"}:
                frame = _derive_timeframe_from_sources(symbol, effective_market, interval_label)
            elif interval_label in {"1d", "daily"}:
                daily, _snapshot = _fetch_dhan_india_daily_frame(symbol)
                frame = _daily_frame_to_candles(daily)
            else:
                frame, _meta = fetch_intraday_history(symbol, interval=interval, data_range=data_range, market=market)
                frame = _intraday_frame_to_candles(frame if isinstance(frame, pd.DataFrame) else pd.DataFrame())
            if frame is None or frame.empty:
                skipped_missing += 1
                messages.append(f"{symbol}: no candles returned for {interval_label}")
                continue
            path = store.write_candle_history(
                timeframe=interval,
                symbol=symbol,
                market=effective_market,
                interval=interval,
                frame=frame,
                retention_days=retention,
                now=now,
            )
            if path is None:
                failed += 1
                continue
            written += 1
        except Exception as exc:
            failed += 1
            messages.append(f"{symbol}: {exc}")
    return {
        "symbols": len(resolved_symbols),
        "written": written,
        "failed": failed,
        "skipped_missing": skipped_missing,
        "retention_days": retention,
        "messages": messages,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Dhan candles and store them as centralized Parquet history.")
    parser.add_argument("--universe", default="broad-india", choices=("broad-india", "nifty50", "manual", "crypto"), help="Symbol universe to ingest")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="Market label to use in storage")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Candle interval to ingest, e.g. 15m or 1d")
    parser.add_argument("--data-range", default=DEFAULT_DATA_RANGE, help="Dhan history range, e.g. 60d")
    parser.add_argument("--symbols", default="", help="Comma-separated symbol override")
    parser.add_argument("--retention-days", type=int, default=0, help="Optional retention window in days")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = fetch_and_store(
        universe=args.universe,
        market=args.market,
        interval=args.interval,
        data_range=args.data_range,
        symbols=_parse_symbols(args.symbols),
        retention_days=args.retention_days or None,
    )
    print(summary)
    return 0 if summary.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
