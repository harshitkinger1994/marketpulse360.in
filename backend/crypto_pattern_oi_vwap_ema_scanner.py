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

from backend.data_fetcher import CRYPTO
from backend.market_snapshot_store import MarketSnapshotStore
from backend.pattern_oi_vwap_ema_scanner import (
    _compute_body_metrics,
    _compute_rsi,
    _compute_vwap,
    _deliver_telegram_alerts,
    _evaluate_strategy,
    _format_gate12_group_message,
    _format_gate12_personal_message,
    _historical_snapshot_from_work,
    _load_env_files,
    _to_numeric,
)


IST = ZoneInfo("Asia/Kolkata")
DEFAULT_STRATEGY_NAME = "Crypto Pattern+OI+VWAP/EMA"
DEFAULT_STRATEGY_ID = "crypto_pattern_oi_vwap_ema_gate12_on"
DEFAULT_MARKET = "crypto"
DEFAULT_INTERVAL = "15m"
DEFAULT_STORE_TIMEFRAME = "15m"


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


def _resolve_symbols(symbols: list[str] | None = None) -> list[str]:
    if symbols:
        return [str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()]
    return list(CRYPTO.keys())


def _prepare_work(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "dt_utc" not in work.columns:
        work = work.reset_index()
        if "index" in work.columns and "dt_utc" not in work.columns:
            work = work.rename(columns={"index": "dt_utc"})
    work["dt_utc"] = pd.to_datetime(work["dt_utc"], utc=True, errors="coerce")
    work = work.dropna(subset=["dt_utc"]).sort_values("dt_utc").reset_index(drop=True)
    if work.empty:
        return work
    if "dt_ist" not in work.columns:
        work["dt_ist"] = work["dt_utc"].dt.tz_convert(IST)
    rename_map = {}
    for src, dst in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"), ("Volume", "volume")):
        if src in work.columns:
            rename_map[src] = dst
    work = work.rename(columns=rename_map)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in work.columns:
            work[col] = None
    work["ema9"] = _to_numeric(work["close"]).ewm(span=9, adjust=False).mean()
    work["vwap"] = _compute_vwap(
        work.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    )
    work["rsi14"] = _compute_rsi(work.rename(columns={"close": "Close"}))
    work = _compute_body_metrics(work)
    return work


def _build_alert(symbol: str, snapshot: Any, strategy: dict[str, Any], strategy_name: str) -> dict[str, Any]:
    source_note = "Crypto Store"
    return {
        "symbol": symbol.upper(),
        "signature": {
            "symbol": symbol.upper(),
            "direction": strategy.get("direction"),
            "pattern": strategy.get("gate1_pattern") or strategy.get("pattern"),
            "candle_time_ist": snapshot.candle_time_ist,
            "close": snapshot.close,
            "vwap": snapshot.vwap,
            "ema9": snapshot.ema9,
        },
        "group_message": _format_gate12_group_message(symbol, snapshot, strategy, strategy_name=strategy_name, source_note=source_note),
        "personal_message": _format_gate12_personal_message(symbol, snapshot, strategy, strategy_name=strategy_name, source_note=source_note),
        "strategy": strategy,
        "source_note": source_note,
    }


def scan_once(
    *,
    interval: str = DEFAULT_INTERVAL,
    store_timeframe: str = DEFAULT_STORE_TIMEFRAME,
    symbols: list[str] | None = None,
    strategy_name: str = DEFAULT_STRATEGY_NAME,
    strategy_id: str = DEFAULT_STRATEGY_ID,
    market: str = DEFAULT_MARKET,
) -> dict[str, Any]:
    _load_env_files()
    store = MarketSnapshotStore()
    selected = _resolve_symbols(symbols)
    if not selected:
        return {"symbols": 0, "read": 0, "alerts": 0, "messages": ["No crypto symbols configured"]}

    alerts: list[dict[str, Any]] = []
    read = 0
    skipped = 0
    for symbol in selected:
        try:
            frame = store.read_candle_history(store_timeframe, symbol, market, interval)
            if frame is None or frame.empty:
                skipped += 1
                continue
            read += 1
            work = _prepare_work(frame)
            if work.empty:
                skipped += 1
                continue
            snapshot = _historical_snapshot_from_work(work, len(work) - 1, symbol, interval)
            strategy = _evaluate_strategy(snapshot)
            if not (strategy.get("gate1_pass") and strategy.get("gate2_pass")):
                skipped += 1
                continue
            direction = str(strategy.get("direction") or "").strip().upper()
            if direction not in {"BULLISH", "BEARISH", "BOTH"}:
                skipped += 1
                continue
            alerts.append(_build_alert(symbol, snapshot, strategy, strategy_name))
        except Exception as exc:
            skipped += 1
            alerts.append(
                {
                    "symbol": symbol.upper(),
                    "signature": {
                        "symbol": symbol.upper(),
                        "direction": "ERROR",
                        "pattern": "ERROR",
                        "error": str(exc),
                    },
                    "group_message": "",
                    "personal_message": "",
                    "strategy": {"gate1_pass": False, "gate2_pass": False, "direction": "ERROR", "pattern": "ERROR"},
                    "source_note": "Crypto Store",
                }
            )

    valid_alerts = [alert for alert in alerts if alert.get("group_message") or alert.get("personal_message")]
    if valid_alerts:
        _deliver_telegram_alerts(
            valid_alerts,
            gate_label="gate12",
            strategy_key=strategy_id,
            market=market,
            interval=interval,
        )
    return {
        "symbols": len(selected),
        "read": read,
        "alerts": len(valid_alerts),
        "skipped": skipped,
        "store_timeframe": store_timeframe,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crypto Pattern+OI+VWAP/EMA Gate 1/2 scanner powered by the central crypto candle store.")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="Candle interval to read from the store.")
    parser.add_argument("--store-timeframe", default=DEFAULT_STORE_TIMEFRAME, help="Store timeframe to read from, usually 15m.")
    parser.add_argument("--strategy-name", default=DEFAULT_STRATEGY_NAME, help="Label used in Telegram alerts.")
    parser.add_argument("--strategy-id", default=DEFAULT_STRATEGY_ID, help="Strategy id used for dedupe artifacts.")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="Market label used in the store path.")
    parser.add_argument("--symbols", default="", help="Comma-separated crypto symbols to override the default universe.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = scan_once(
        interval=args.interval,
        store_timeframe=args.store_timeframe,
        symbols=_parse_symbols(args.symbols),
        strategy_name=args.strategy_name,
        strategy_id=args.strategy_id,
        market=args.market,
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
